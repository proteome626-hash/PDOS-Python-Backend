"""routers/visits_router.py — /visits/*, /appointments/*, /stock-checks, /expenses/*, /sync/*"""
import datetime
import json
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

import models, schemas, auth
from database import get_db
from routers.deps import (
    get_role_scoped_rep_ids,
    dispatch_notification_sync,
    create_activity_log,
    enrich_visits,
    haversine_distance,
    get_setting,
    manager,
    ALLOWED_TYPES, MAX_SIZE, store_file_locally,
    generate_next_reference_code,
    get_text,
)

router = APIRouter()


def notify_doctor_visit_completed(db: Session, visit: models.Visit, current_user: models.User):
    """Helper to notify supervisor and GMs when a doctor visit is completed."""
    print(f"DEBUG: Entering notify_doctor_visit_completed for visit {visit.id}")
    client = db.query(models.Client).filter(models.Client.id == visit.client_id).first() if visit.client_id else None
    doctor_label = f"Dr. {client.doctor_name}" if client and client.doctor_name else "a doctor"
    tier = f" — Class {client.class_tier}" if client and client.class_tier else ""
    msg = f"{current_user.full_name} completed a doctor visit with {doctor_label}{tier}."

    # Ensure we have the latest supervisor link from DB
    user_record = db.query(models.User).filter(models.User.id == current_user.id).first()
    sup_id = user_record.supervisor_id if user_record else None
    print(f"DEBUG: User {current_user.id} has supervisor {sup_id}")

    recipients = {sup_id} if sup_id else set()
    for gm in db.query(models.User).filter(models.User.role == "general_manager", models.User.is_active == True).all():
        recipients.add(gm.id)

    print(f"DEBUG: Notification recipients: {recipients}")

    for rid in recipients:
        if rid:
            print(f"DEBUG: Dispatching notification to {rid}")
            dispatch_notification_sync(db=db, user_id=rid, type="doctor_visit_completed",
                                       title="Doctor Visit Completed",
                                       message=msg, related_id=visit.id)

def _assert_can_manage_appointment(current_user: models.User, appt_rep_id: str, db: Session, request: Request):
    scope = auth.get_scope(current_user, db)
    rep = db.query(models.User).filter(models.User.id == appt_rep_id).first()
    if not rep:
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
    if current_user.role in ("admin", "general_manager", "overseer"):
        return
    if current_user.role == "supervisor":
        authorized = (
            rep.id in (scope.rep_ids or [])
            and rep.region_id in (scope.region_ids or [])
        )
    else:
        authorized = current_user.role == "rep" and current_user.id == appt_rep_id
    rep_brands = set(rep.brand_ids)
    if rep.brand_id:
        rep_brands.add(rep.brand_id)
    if (
        not authorized
        or (scope.region_ids is not None and rep.region_id not in scope.region_ids)
        or (scope.brand_ids is not None and not rep_brands.intersection(scope.brand_ids))
    ):
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))


def _appointment_targets_in_scope(
    appointment: models.Appointment,
    current_user: models.User,
    db: Session,
) -> bool:
    scope = auth.get_scope(current_user, db)
    targets = []
    if appointment.client_id:
        targets.append(db.query(models.Client).filter(
            models.Client.id == appointment.client_id
        ).first())
    if appointment.center_id:
        targets.append(db.query(models.Center).filter(
            models.Center.id == appointment.center_id
        ).first())
    if appointment.suggested_product_id:
        targets.append(db.query(models.Product).filter(
            models.Product.id == appointment.suggested_product_id
        ).first())
    for target in targets:
        if target is None:
            return False
        if scope.region_ids is not None and hasattr(target, "region_id"):
            if target.region_id not in scope.region_ids:
                return False
        if scope.brand_ids is not None and getattr(target, "brand_id", None) not in scope.brand_ids:
            return False
    return True


def _assert_appointment_targets_in_scope(
    appointment: models.Appointment,
    current_user: models.User,
    db: Session,
    request: Request,
):
    if not _appointment_targets_in_scope(appointment, current_user, db):
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))


def _assert_can_manage_visit(
    current_user: models.User,
    visit_or_rep_id,
    db: Session,
    request: Request,
):
    scope = auth.get_scope(current_user, db)
    if current_user.role in ("admin", "general_manager", "overseer"):
        return
    visit = visit_or_rep_id if isinstance(visit_or_rep_id, models.Visit) else None
    rep_id = visit.rep_id if visit else visit_or_rep_id
    rep = db.query(models.User).filter(models.User.id == rep_id).first()
    if not rep:
        raise HTTPException(status_code=403, detail=get_text("not_authorized_rep", request))

    if current_user.role == "rep":
        if rep_id != current_user.id:
            raise HTTPException(status_code=403, detail=get_text("not_authorized_rep", request))
    elif current_user.role == "supervisor":
        if rep_id not in (scope.rep_ids or []):
            raise HTTPException(status_code=403, detail=get_text("not_authorized_rep", request))
    else:
        raise HTTPException(status_code=403, detail=get_text("not_authorized_rep", request))

    region_id = visit.region_id if visit else None
    brand_id = visit.brand_id if visit else None
    if visit and (not region_id or not brand_id):
        target = None
        if visit.client_id:
            target = db.query(models.Client).filter(
                models.Client.id == visit.client_id
            ).first()
        if target is None and visit.center_id:
            target = db.query(models.Center).filter(
                models.Center.id == visit.center_id
            ).first()
        if target:
            region_id = region_id or target.region_id
            brand_id = brand_id or target.brand_id
    region_id = region_id or rep.region_id
    if not brand_id:
        rep_brand_ids = set(rep.brand_ids)
        if rep.brand_id:
            rep_brand_ids.add(rep.brand_id)
        if len(rep_brand_ids) == 1:
            brand_id = next(iter(rep_brand_ids))
    if scope.region_ids is not None and region_id not in scope.region_ids:
        raise HTTPException(status_code=403, detail=get_text("not_authorized_rep", request))
    if scope.brand_ids is not None and brand_id not in scope.brand_ids:
        raise HTTPException(status_code=403, detail=get_text("not_authorized_rep", request))


# ── Appointments ─────────────────────────────────────────────
@router.get("/appointments", response_model=List[schemas.AppointmentResponse])
def get_appointments(current_user: models.User = Depends(auth.require_password_set), db: Session = Depends(get_db)):
    rep_ids = get_role_scoped_rep_ids(current_user, db)

    # Optimize N+1 by joining required tables
    appointments = db.query(
        models.Appointment,
        models.Center.name.label("center_name"),
        models.Client.doctor_name,
        models.Client.facility_name,
        models.Product.name.label("product_name")
    ).outerjoin(
        models.Center, models.Appointment.center_id == models.Center.id
    ).outerjoin(
        models.Client, models.Appointment.client_id == models.Client.id
    ).outerjoin(
        models.Product, models.Appointment.suggested_product_id == models.Product.id
    )

    if rep_ids is not None:
        appointments = appointments.filter(models.Appointment.rep_id.in_(rep_ids))

    final_results = []
    for a, center_name, doc_name, fac_name, prod_name in appointments.all():
        if not _appointment_targets_in_scope(a, current_user, db):
            continue
        data = a.__dict__.copy()
        data.pop('_sa_instance_state', None)

        data["center_name"] = center_name
        data["client_name"] = doc_name or fac_name or ""
        data["suggested_product_name"] = prod_name
        final_results.append(data)

    return final_results


@router.post("/appointments", response_model=schemas.AppointmentResponse)
def create_appointment(payload: schemas.AppointmentCreate, current_user: models.User = Depends(auth.require_password_set), db: Session = Depends(get_db), request: Request = None):
    _assert_can_manage_appointment(current_user, payload.rep_id, db, request)
    
    # Idempotency check
    existing = db.query(models.Appointment).filter(models.Appointment.id == payload.id).first()
    if existing:
        _assert_can_manage_appointment(current_user, existing.rep_id, db, request)
        _assert_appointment_targets_in_scope(existing, current_user, db, request)
        if not existing.reference_code:
            existing.reference_code = generate_next_reference_code(db, "APT")
            db.commit()
            db.refresh(existing)
        center = db.query(models.Center).filter(models.Center.id == existing.center_id).first()
        client = db.query(models.Client).filter(models.Client.id == existing.client_id).first() if existing.client_id else None

        # Proper serialization
        data = schemas.AppointmentResponse.model_validate(existing).model_dump()
        data["center_name"] = center.name if center else None
        data["client_name"] = f"{client.doctor_name or client.facility_name or ''}" if client else None
        return data

    ref_code = generate_next_reference_code(db, "APT")
    a = models.Appointment(
        id=payload.id,
        reference_code=ref_code,
        rep_id=payload.rep_id, client_id=payload.client_id,
        center_id=payload.center_id, appt_date=payload.appt_date,
        appt_time=payload.appt_time, reminder_minutes_before=payload.reminder_minutes_before,
        notes=payload.notes, status=payload.status,
        supervisor_note=payload.supervisor_note,
        suggested_product_id=payload.suggested_product_id,
    )
    _assert_appointment_targets_in_scope(a, current_user, db, request)
    db.add(a)
    db.commit()
    db.refresh(a)

    center = db.query(models.Center).filter(models.Center.id == a.center_id).first()
    client = db.query(models.Client).filter(models.Client.id == a.client_id).first() if a.client_id else None
    target_name = client.doctor_name if (client and client.doctor_name) else (client.facility_name if client else (center.name if center else "Unknown"))

    create_activity_log(db, current_user.id, current_user.full_name, "log_scheduled_appt", request=request, name=target_name, date=a.appt_date)

    # Proper serialization
    data = schemas.AppointmentResponse.model_validate(a).model_dump()
    data["center_name"] = center.name if center else None
    data["client_name"] = f"{client.doctor_name or client.facility_name or ''}" if client else None
    return data


@router.put("/appointments/{id}", response_model=schemas.AppointmentResponse)
def update_appointment(id: str, payload: schemas.AppointmentUpdate, current_user: models.User = Depends(auth.require_password_set), db: Session = Depends(get_db), request: Request = None):
    a = db.query(models.Appointment).filter(models.Appointment.id == id).first()
    if not a:
        raise HTTPException(status_code=404, detail=get_text("appointment_not_found", request))
    _assert_can_manage_appointment(current_user, a.rep_id, db, request)
    _assert_appointment_targets_in_scope(a, current_user, db, request)
    proposed = models.Appointment(
        rep_id=a.rep_id,
        client_id=payload.client_id if payload.client_id is not None else a.client_id,
        center_id=a.center_id,
        suggested_product_id=(
            payload.suggested_product_id
            if payload.suggested_product_id is not None
            else a.suggested_product_id
        ),
    )
    _assert_appointment_targets_in_scope(proposed, current_user, db, request)
    if payload.client_id is not None: a.client_id = payload.client_id
    if payload.appt_date: a.appt_date = payload.appt_date
    if payload.appt_time: a.appt_time = payload.appt_time
    if payload.status: a.status = payload.status
    if payload.notes: a.notes = payload.notes
    if payload.supervisor_note is not None: a.supervisor_note = payload.supervisor_note
    if payload.suggested_product_id is not None: a.suggested_product_id = payload.suggested_product_id
    db.commit()
    db.refresh(a)
    center = db.query(models.Center).filter(models.Center.id == a.center_id).first()
    client = db.query(models.Client).filter(models.Client.id == a.client_id).first() if a.client_id else None

    # Proper serialization
    data = schemas.AppointmentResponse.model_validate(a).model_dump()
    data["center_name"] = center.name if center else None
    data["client_name"] = f"{client.doctor_name or client.facility_name or ''}" if client else None
    return data


@router.delete("/appointments/{id}")
def delete_appointment(id: str, current_user: models.User = Depends(auth.require_password_set), db: Session = Depends(get_db), request: Request = None):
    a = db.query(models.Appointment).filter(models.Appointment.id == id).first()
    if not a:
        raise HTTPException(status_code=404, detail=get_text("appointment_not_found", request))
    _assert_can_manage_appointment(current_user, a.rep_id, db, request)
    db.delete(a)
    db.commit()
    return {"message": get_text("success_delete", request)}


# ── Check-in ─────────────────────────────────────────────────
@router.post("/visits/check-in", response_model=schemas.VisitResponse)
async def check_in(
    payload: schemas.CheckInRequest,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    scope = auth.get_scope(current_user, db)
    if not payload.center_id and not payload.client_id:
        raise HTTPException(status_code=400, detail=get_text("required_field", request))

    visit_status = "arrived"
    center = None
    client = None
    center_id = payload.center_id if payload.center_id != "" else None

    if center_id:
        center = db.query(models.Center).filter(models.Center.id == center_id).first()
        if not center:
            raise HTTPException(status_code=404, detail=get_text("center_not_found", request))
        if scope.region_ids is not None and center.region_id not in scope.region_ids:
            raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
        if scope.brand_ids is not None and center.brand_id not in scope.brand_ids:
            raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
        if None not in (center.latitude, center.longitude, payload.latitude, payload.longitude):
            if haversine_distance(payload.latitude, payload.longitude, center.latitude, center.longitude) > get_setting(db, "geofence_radius_meters", 500.0):
                visit_status = "flagged"
    elif payload.client_id:
        client = db.query(models.Client).filter(models.Client.id == payload.client_id).first()
        if not client:
            raise HTTPException(status_code=404, detail=get_text("client_not_found", request))
        if scope.region_ids is not None and client.region_id not in scope.region_ids:
            raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
        if scope.brand_ids is not None and client.brand_id not in scope.brand_ids:
            raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
        if None not in (client.latitude, client.longitude, payload.latitude, payload.longitude):
            if haversine_distance(payload.latitude, payload.longitude, client.latitude, client.longitude) > get_setting(db, "geofence_radius_meters", 500.0):
                visit_status = "flagged"

    if payload.is_mocked:
        visit_status = "flagged"

    target_region_id = center.region_id if center else (client.region_id if client else None)
    target_brand_id = center.brand_id if center else (client.brand_id if client else None)
    v_region_id = current_user.region_id or target_region_id
    v_brand_id = target_brand_id or current_user.brand_id
    if scope.brand_ids is not None and v_brand_id not in scope.brand_ids:
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))

    existing_visit = db.query(models.Visit).filter(models.Visit.id == payload.visit_id).first()
    if existing_visit:
        if existing_visit.rep_id != current_user.id:
            raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
        if not existing_visit.region_id and v_region_id:
            existing_visit.region_id = v_region_id
        if not existing_visit.brand_id and v_brand_id:
            existing_visit.brand_id = v_brand_id
        existing_visit.status = visit_status
        if visit_status == "flagged" and current_user.supervisor_id:
            flagged_name = center.name if center else (
                (client.doctor_name or client.facility_name) if client else "Unknown target")
            reason_desc = "using a spoofed/mock location" if payload.is_mocked else "out-of-bounds"
            dispatch_notification_sync(
                db=db, user_id=current_user.supervisor_id, type="visit_flagged",
                title="Flagged Check-in",
                message=f"Rep {current_user.full_name} checked in {reason_desc} at {flagged_name}",
                related_id=existing_visit.id,
            )
        db.commit()
        db.refresh(existing_visit)
        return enrich_visits([existing_visit], db)[0]

    new_visit = models.Visit(
        id=payload.visit_id, rep_id=current_user.id,
        region_id=v_region_id, brand_id=v_brand_id,
        center_id=center_id, client_id=payload.client_id,
        appointment_id=payload.appointment_id,
        visit_date=datetime.datetime.now(datetime.timezone.utc),
        arrival_time=datetime.datetime.now(datetime.timezone.utc),
        status=visit_status, latitude=payload.latitude, longitude=payload.longitude,
    )
    db.add(new_visit)
    if payload.appointment_id:
        appt = db.query(models.Appointment).filter(models.Appointment.id == payload.appointment_id).first()
        if appt:
            appt.status = "done"
    if visit_status == "flagged" and current_user.supervisor_id:
        flagged_name = center.name if center else (
            (client.doctor_name or client.facility_name) if client else "Unknown target")
        reason_desc = "using a spoofed/mock location" if payload.is_mocked else "out-of-bounds"
        dispatch_notification_sync(
            db=db, user_id=current_user.supervisor_id, type="visit_flagged",
            title="Flagged Check-in",
            message=f"Rep {current_user.full_name} checked in {reason_desc} at {flagged_name}",
            related_id=new_visit.id,
        )
    db.commit()
    db.refresh(new_visit)
    enrich_visits([new_visit], db)
    target_name = client.doctor_name if (client and client.doctor_name) else (client.facility_name if client else (center.name if center else "Unknown target"))
    create_activity_log(db, current_user.id, current_user.full_name, "log_checked_in", request=request, name=target_name)
    await manager.broadcast("refresh_analytics")
    return new_visit


# ── Visits CRUD ───────────────────────────────────────────────
@router.post("/visits", response_model=schemas.VisitResponse)
async def create_visit(
    visit: schemas.VisitCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set),
):
    scope = auth.get_scope(current_user, db)
    center_id = visit.center_id if visit.center_id != "" else None
    status_to_save = visit.status

    if getattr(visit, "is_mocked", False):
        status_to_save = "flagged"

    center = db.query(models.Center).filter(models.Center.id == center_id).first() if center_id else None
    client = db.query(models.Client).filter(models.Client.id == visit.client_id).first() if visit.client_id else None
    if center and scope.region_ids is not None and center.region_id not in scope.region_ids:
        raise HTTPException(status_code=403, detail="Target is outside your assigned region")
    if client and scope.region_ids is not None and client.region_id not in scope.region_ids:
        raise HTTPException(status_code=403, detail="Target is outside your assigned region")
    target_region_id = center.region_id if center else (client.region_id if client else None)
    target_brand_id = center.brand_id if center else (client.brand_id if client else None)
    if target_brand_id and scope.brand_ids is not None and target_brand_id not in scope.brand_ids:
        raise HTTPException(status_code=403, detail="Target is outside your assigned brands")
    if visit.brand_id and target_brand_id and visit.brand_id != target_brand_id:
        raise HTTPException(status_code=403, detail="Visit brand does not match its target")
    if visit.brand_id and scope.brand_ids is not None and visit.brand_id not in scope.brand_ids:
        raise HTTPException(status_code=403, detail="Visit brand is outside your assigned brands")

    if center and None not in (center.latitude, center.longitude, visit.latitude, visit.longitude):
        if haversine_distance(visit.latitude, visit.longitude, center.latitude, center.longitude) > get_setting(db, "geofence_radius_meters", 500.0):
            status_to_save = "flagged"
    elif client and None not in (client.latitude, client.longitude, visit.latitude, visit.longitude):
        if haversine_distance(visit.latitude, visit.longitude, client.latitude, client.longitude) > get_setting(db, "geofence_radius_meters", 500.0):
            status_to_save = "flagged"

    existing = db.query(models.Visit).filter(models.Visit.id == visit.id).first()
    interested_json = (
        json.dumps(visit.interested_product_ids) if visit.interested_product_ids else None
    )
    v_region_id = current_user.region_id or target_region_id
    v_brand_id = target_brand_id or visit.brand_id or current_user.brand_id
    if scope.brand_ids is not None and v_brand_id not in scope.brand_ids:
        raise HTTPException(status_code=403, detail="Visit brand is outside your assigned brands")
    if existing:
        if existing.rep_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized to update this visit")
        if not existing.region_id and v_region_id:
            existing.region_id = v_region_id
        if not existing.brand_id and v_brand_id:
            existing.brand_id = v_brand_id
        if existing.status == "flagged":
            status_to_save = "flagged"
        existing.status = status_to_save
        if visit.notes:
            existing.notes = visit.notes
        existing.completion_time = visit.completion_time
        existing.visit_type = visit.visit_type or "center"
        existing.visit_reason = visit.visit_reason
        existing.interested_product_ids = interested_json
        if existing.latitude is None and visit.latitude is not None:
            existing.latitude = visit.latitude
            existing.longitude = visit.longitude
        if visit.save_location_lat is not None:
            existing.save_location_lat = visit.save_location_lat
            existing.save_location_lng = visit.save_location_lng
        if visit.retroactive_reason:
            existing.retroactive_reason = visit.retroactive_reason
        if not existing.reference_code:
            existing.reference_code = generate_next_reference_code(db, "VST")
        new_visit = existing
    else:
        ref_code = generate_next_reference_code(db, "VST")
        new_visit = models.Visit(
            id=visit.id, reference_code=ref_code, rep_id=current_user.id,
            region_id=v_region_id, brand_id=v_brand_id,
            center_id=center_id,
            client_id=visit.client_id, appointment_id=visit.appointment_id,
            visit_date=visit.visit_date, status=status_to_save,
            notes=visit.notes, latitude=visit.latitude, longitude=visit.longitude,
            visit_type=visit.visit_type or "center",
            visit_reason=visit.visit_reason,
            interested_product_ids=interested_json,
            arrival_time=visit.arrival_time,
            completion_time=visit.completion_time,
            retroactive_reason=visit.retroactive_reason,
            save_location_lat=visit.save_location_lat,
            save_location_lng=visit.save_location_lng,
        )
        db.add(new_visit)
    if visit.appointment_id:
        appt = db.query(models.Appointment).filter(models.Appointment.id == visit.appointment_id).first()
        if appt:
            appt.status = "done"
            
    task_to_link = None
    if hasattr(visit, 'task_id') and visit.task_id:
        task_to_link = db.query(models.Task).filter(models.Task.id == visit.task_id).first()
    elif visit.appointment_id:
        appt = db.query(models.Appointment).filter(models.Appointment.id == visit.appointment_id).first()
        if appt:
            target_id = appt.client_id or appt.center_id
            if target_id:
                task_to_link = db.query(models.Task).filter(
                    models.Task.rep_id == current_user.id,
                    models.Task.target_id == target_id,
                    models.Task.status.in_(['pending', 'accepted', 'scheduled', 'accepted_scheduled', 'in_progress'])
                ).order_by(models.Task.created_at.desc()).first()

    if task_to_link:
        task_to_link.visit_id = new_visit.id
        task_to_link.status = 'done'
        task_to_link.completed_at = datetime.datetime.now(datetime.timezone.utc)
            
    db.commit()
    db.refresh(new_visit)
    for item in visit.items:
        if db.query(models.VisitItem).filter(models.VisitItem.visit_id == new_visit.id, models.VisitItem.product_id == item.product_id).first():
            continue
        prod = db.query(models.Product).filter(models.Product.id == item.product_id).first()
        price = item.price_at_sale if item.price_at_sale is not None else (prod.price if prod else 0.0)
        db.add(models.VisitItem(visit_id=new_visit.id, product_id=item.product_id, qty_sold=item.qty_sold, qty_free=item.qty_free, price_at_sale=price))
        if prod and item.qty_sold > 0:
            prod.stock_qty = max(0, prod.stock_qty - item.qty_sold)
    db.commit()
    db.refresh(new_visit)
    for req in visit.special_requests:
        if not db.query(models.SpecialRequest).filter(models.SpecialRequest.id == req.id).first():
            db.add(models.SpecialRequest(id=req.id, visit_id=new_visit.id, request_type=req.request_type, description=req.description))
    db.commit()
    db.refresh(new_visit)
    enrich_visits([new_visit], db)
    if new_visit.status == "flagged" and current_user.supervisor_id:
        existing_notif = db.query(models.Notification).filter(
            models.Notification.user_id == current_user.supervisor_id,
            models.Notification.related_id == new_visit.id,
            models.Notification.type == "visit_flagged"
        ).first()
        if not existing_notif:
            flagged_reason = "using a spoofed/mock location" if getattr(visit, "is_mocked", False) else "violating geofencing rules"
            dispatch_notification_sync(db=db, user_id=current_user.supervisor_id, type="visit_flagged",
                                       title="Flagged Visit Alert",
                                       message=f"{current_user.full_name} submitted a visit {flagged_reason}.",
                                       related_id=new_visit.id)
    # Doctor-visit flow: inform the supervisor AND every general manager
    if (new_visit.visit_type == "doctor" and new_visit.status == "completed"):
        notify_doctor_visit_completed(db, new_visit, current_user)
    center = db.query(models.Center).filter(models.Center.id == new_visit.center_id).first() if new_visit.center_id else None
    client = db.query(models.Client).filter(models.Client.id == new_visit.client_id).first() if new_visit.client_id else None
    target_name = client.doctor_name if (client and client.doctor_name) else (client.facility_name if client else (center.name if center else "Unknown target"))
    
    action_text = f"Updated visit to {target_name} (Status: {new_visit.status})"
    if new_visit.status == "in_progress":
        action_text = f"Started visit to {target_name}"
    elif new_visit.status == "completed":
        items_sold = sum([item.qty_sold for item in visit.items])
        gifts_given = sum([item.qty_free for item in visit.items])
        action_text = f"Completed visit to {target_name}."
        if items_sold > 0 or gifts_given > 0:
            action_text += f" Sold {items_sold} items, gave {gifts_given} gifts."
            
    create_activity_log(db, current_user.id, current_user.full_name, action_text, "visit", new_visit.id)
    
    b_id = new_visit.brand_id or (center.brand_id if center and hasattr(center, 'brand_id') else None) or (client.brand_id if client and hasattr(client, 'brand_id') else None) or (current_user.brand_ids[0] if current_user.brand_ids else None)
    if b_id:
        db.add(models.BrandActivityLog(
            rep_id=current_user.id,
            brand_id=b_id,
            activity_type='visit',
            target_type=new_visit.visit_type or 'center',
            target_id=new_visit.client_id if new_visit.client_id else new_visit.center_id,
            target_name=target_name,
            notes=visit.notes,
            status=new_visit.status
        ))
        db.commit()
    await manager.broadcast("refresh_analytics")
    return new_visit


@router.get("/visits", response_model=List[schemas.VisitResponse])
def get_visits(
    status: Optional[str] = None,
    brand_id: Optional[str] = None,
    region_id: Optional[str] = None,
    client_id: Optional[str] = None,
    center_id: Optional[str] = None,
    appointment_id: Optional[str] = None,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
):
    from auth import get_scope
    scope = get_scope(current_user, db, requested_region_id=region_id)
    query = db.query(models.Visit)
    scoped_target = None
    allowed_brand_ids = None
    if scope.role not in ("admin", "general_manager", "overseer"):
        allowed_brand_ids = [brand.id for brand in current_user.brands]
        if current_user.brand_id and current_user.brand_id not in allowed_brand_ids:
            allowed_brand_ids.append(current_user.brand_id)

    if client_id:
        target = db.query(models.Client).filter(models.Client.id == client_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Client not found")
        if (
            (scope.region_ids is not None and target.region_id not in scope.region_ids)
            or (
                scope.role == "supervisor"
                and scope.rep_ids is not None
                and target.rep_id not in scope.rep_ids
            )
            or (allowed_brand_ids is not None and target.brand_id not in allowed_brand_ids)
            or (region_id is not None and target.region_id != region_id)
            or (brand_id is not None and target.brand_id != brand_id)
        ):
            raise HTTPException(status_code=403, detail="Target is outside your assigned scope")
        scoped_target = target
        query = query.filter(models.Visit.client_id == client_id)

    if center_id:
        target = db.query(models.Center).filter(models.Center.id == center_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Center not found")
        if (
            (scope.region_ids is not None and target.region_id not in scope.region_ids)
            or (
                scope.role == "supervisor"
                and scope.rep_ids is not None
                and target.assigned_rep_id not in scope.rep_ids
            )
            or (allowed_brand_ids is not None and target.brand_id not in allowed_brand_ids)
            or (region_id is not None and target.region_id != region_id)
            or (brand_id is not None and target.brand_id != brand_id)
        ):
            raise HTTPException(status_code=403, detail="Target is outside your assigned scope")
        scoped_target = target
        query = query.filter(models.Visit.center_id == center_id)

    if appointment_id:
        query = query.filter(models.Visit.appointment_id == appointment_id)

    if scoped_target:
        # Older visit rows may predate region/brand denormalization. The target
        # was checked against the caller's scope above, so accept missing values
        # while still rejecting explicit conflicting attribution.
        if scope.role == "supervisor" and scope.rep_ids is not None:
            query = query.filter(models.Visit.rep_id.in_(scope.rep_ids))
        target_region_id = scoped_target.region_id
        if target_region_id:
            query = query.filter(or_(
                models.Visit.region_id == target_region_id,
                models.Visit.region_id.is_(None),
            ))
        target_brand_id = scoped_target.brand_id
        if target_brand_id:
            query = query.filter(or_(
                models.Visit.brand_id == target_brand_id,
                models.Visit.brand_id.is_(None),
            ))
    elif scope.role == "rep":
        query = query.filter(models.Visit.rep_id == current_user.id)
    elif scope.role == "supervisor":
        query = query.filter(
            models.Visit.rep_id.in_(scope.rep_ids or []),
            models.Visit.region_id.in_(scope.region_ids or []),
        )
    else:
        if scope.region_ids is not None:
            query = query.filter(models.Visit.region_id.in_(scope.region_ids))

    if allowed_brand_ids is not None and scoped_target is None:
        query = query.filter(models.Visit.brand_id.in_(allowed_brand_ids))

    if brand_id and scoped_target is None:
        query = query.filter(models.Visit.brand_id == brand_id)
    if status:
        query = query.filter(models.Visit.status == status)
    return enrich_visits(query.order_by(models.Visit.visit_date.desc()).all(), db)



@router.get("/visits/{visit_id}", response_model=schemas.VisitResponse)
def get_visit(visit_id: str, current_user: models.User = Depends(auth.require_password_set), db: Session = Depends(get_db), request: Request = None):
    visit = db.query(models.Visit).filter(models.Visit.id == visit_id).first()
    if not visit:
        visit = db.query(models.Visit).filter(
            models.Visit.appointment_id == visit_id
        ).order_by(models.Visit.visit_date.desc()).first()
        if visit:
            scope = auth.get_scope(current_user, db)
            if (
                scope.region_ids is not None
                and visit.region_id not in scope.region_ids
            ):
                raise HTTPException(
                    status_code=404,
                    detail=get_text("visit_not_found", request),
                )
    if not visit:
        raise HTTPException(status_code=404, detail=get_text("visit_not_found", request))
    _assert_can_manage_visit(current_user, visit, db, request)
    return enrich_visits([visit], db)[0]

@router.put("/visits/{visit_id}/review", response_model=schemas.VisitResponse)
def review_visit(
    visit_id: str,
    payload: schemas.VisitReviewRequest,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    visit = db.query(models.Visit).filter(models.Visit.id == visit_id).first()
    if not visit:
        raise HTTPException(status_code=404, detail=get_text("visit_not_found", request))
    if current_user.role == "supervisor":
        _assert_can_manage_visit(current_user, visit, db, request)
    elif current_user.role not in ("admin", "general_manager", "overseer"):
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
    if visit.status != "flagged":
        raise HTTPException(status_code=409, detail=get_text("visit_no_longer_pending", request))
    if payload.status == "completed":
        visit.status = "completed"
        # Trigger doctor visit completion notification if applicable
        if visit.visit_type == "doctor":
            notify_doctor_visit_completed(db, visit, current_user)
    elif payload.status == "rejected":
        visit.status = "rejected"
        for item in visit.items:
            prod = db.query(models.Product).filter(models.Product.id == item.product_id).first()
            if prod:
                prod.stock_qty += item.qty_sold
        if visit.appointment_id:
            appt = db.query(models.Appointment).filter(models.Appointment.id == visit.appointment_id).first()
            if appt:
                appt.status = "pending"
    else:
        raise HTTPException(status_code=400, detail=get_text("invalid_status", request))
    visit.reviewed_by = current_user.id
    visit.reviewed_at = datetime.datetime.now(datetime.timezone.utc)
    visit.review_note = payload.note
    db.commit()
    db.refresh(visit)
    enrich_visits([visit], db)
    dispatch_notification_sync(db=db, user_id=visit.rep_id, type="visit_reviewed",
                               title=f"Visit {payload.status.capitalize()}",
                               message=f"Your flagged visit has been {payload.status}. Note: {payload.note}",
                               related_id=visit.id)
    return visit


@router.put("/visits/{visit_id}/note", response_model=schemas.VisitResponse)
def add_visit_note(
    visit_id: str,
    payload: schemas.ReviewNoteCreate,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    visit = db.query(models.Visit).filter(models.Visit.id == visit_id).first()
    if not visit:
        raise HTTPException(status_code=404, detail=get_text("visit_not_found", request))
    _assert_can_manage_visit(current_user, visit, db, request)
    visit.supervisor_note = payload.review_note
    db.commit()
    db.refresh(visit)
    enrich_visits([visit], db)
    dispatch_notification_sync(db=db, user_id=visit.rep_id, type="visit_note",
                               title="New supervisor note", message=payload.review_note, related_id=visit.id)
    return visit


@router.get("/visits/{visit_id}/invoice")
def get_visit_invoice(visit_id: str, current_user: models.User = Depends(auth.require_password_set), db: Session = Depends(get_db), request: Request = None):
    from fpdf import FPDF
    import tempfile, os as _os
    from starlette.background import BackgroundTask

    visit = db.query(models.Visit).filter(models.Visit.id == visit_id).first()
    if not visit:
        raise HTTPException(status_code=404, detail=get_text("visit_not_found", request))
    _assert_can_manage_visit(current_user, visit, db, request)

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, txt=f"Invoice for Visit: {visit.id}", ln=True, align="C")
    pdf.cell(200, 10, txt=f"Date: {visit.visit_date}", ln=True)
    pdf.cell(200, 10, txt=f"Status: {visit.status}", ln=True)
    for item in visit.visit_items:
        pdf.cell(200, 10, txt=f"- Product ID: {item.product_id} | Sold: {item.qty_sold} | Free: {item.qty_free}", ln=True)

    fd, temp_path = tempfile.mkstemp(suffix=".pdf")
    _os.close(fd)
    pdf.output(temp_path)

    def _cleanup():
        try:
            _os.remove(temp_path)
        except OSError:
            pass

    # Delete the temp file after the response is sent (was leaking files)
    return FileResponse(
        temp_path, media_type="application/pdf",
        filename=f"invoice_{visit_id}.pdf",
        background=BackgroundTask(_cleanup),
    )


@router.post("/visits/{visit_id}/signature")
async def upload_visit_signature(
    visit_id: str, file: UploadFile,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    visit = db.query(models.Visit).filter(models.Visit.id == visit_id).first()
    if not visit:
        raise HTTPException(status_code=404, detail=get_text("visit_not_found", request))
    if visit.rep_id != current_user.id:
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail=get_text("invalid_file_type", request))
    contents = await file.read()
    if len(contents) > MAX_SIZE:
        raise HTTPException(status_code=400, detail=get_text("file_too_large", request))
    result = await store_file_locally(contents, file.content_type, "signatures", None, db)
    visit.signature_url = result["local_path"]
    visit.signature_file_id = result["stored_file"].id
    db.commit()
    db.refresh(visit)
    return {"signature_url": visit.signature_url, "signature_file_id": visit.signature_file_id}


@router.post("/visits/{visit_id}/photos", response_model=schemas.VisitPhotoResponse)
async def upload_visit_photo(
    visit_id: str, file: UploadFile,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    visit = db.query(models.Visit).filter(models.Visit.id == visit_id).first()
    if not visit:
        raise HTTPException(status_code=404, detail=get_text("visit_not_found", request))
    if visit.rep_id != current_user.id:
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail=get_text("invalid_file_type", request))
    contents = await file.read()
    if len(contents) > MAX_SIZE:
        raise HTTPException(status_code=400, detail=get_text("file_too_large", request))
    result = await store_file_locally(contents, file.content_type, "visits", None, db)
    vp = models.VisitPhoto(visit_id=visit_id, photo_url=result["local_path"], photo_file_id=result["stored_file"].id)
    db.add(vp)
    db.commit()
    db.refresh(vp)
    return vp


# ── Stock checks ─────────────────────────────────────────────
@router.get("/visits/{visit_id}/stock-checks", response_model=List[schemas.StockCheckResponse])
def get_visit_stock_checks(
    visit_id: str,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    visit = db.query(models.Visit).filter(models.Visit.id == visit_id).first()
    if not visit:
        raise HTTPException(status_code=404, detail=get_text("visit_not_found", request))
    _assert_can_manage_visit(current_user, visit, db, request)
    return db.query(models.PharmacyStockCheck).filter(models.PharmacyStockCheck.visit_id == visit_id).all()

@router.post("/stock-checks", response_model=List[schemas.StockCheckResponse])
def create_stock_checks(
    payload: List[schemas.StockCheckCreate],
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    results = []
    for item in payload:
        visit = db.query(models.Visit).filter(models.Visit.id == item.visit_id).first()
        if not visit or visit.rep_id != current_user.id:
            raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
        _assert_can_manage_visit(current_user, visit, db, request)
        product = db.query(models.Product).filter(
            models.Product.id == item.product_id
        ).first()
        if (
            not product
            or (
                visit.brand_id
                and product.brand_id != visit.brand_id
            )
        ):
            raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
        existing = db.query(models.PharmacyStockCheck).filter_by(visit_id=item.visit_id, product_id=item.product_id).first()
        if existing:
            results.append(existing)
            continue
        new_check = models.PharmacyStockCheck(**item.model_dump())
        db.add(new_check)
        results.append(new_check)
    db.commit()
    for r in results:
        db.refresh(r)
    return results


# ── Expenses ─────────────────────────────────────────────────
@router.post("/expenses", response_model=schemas.ExpenseResponse)
def create_expense(
    payload: schemas.ExpenseCreate,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    if payload.visit_id:
        visit = db.query(models.Visit).filter(models.Visit.id == payload.visit_id).first()
        if not visit:
            raise HTTPException(status_code=404, detail=get_text("visit_not_found", request))
        _assert_can_manage_visit(current_user, visit, db, request)
    existing = db.query(models.Expense).filter(models.Expense.id == payload.id).first()
    if existing and existing.rep_id != current_user.id:
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
    threshold = float((db.query(models.SystemSetting).filter(models.SystemSetting.key == "expense_approval_threshold").first() or type("o", (), {"value": "999999"})()).value)
    requires_admin = payload.amount > threshold
    requires_gm = current_user.role == "supervisor"
    if existing:
        existing.category = payload.category
        existing.amount = payload.amount
        existing.description = payload.description
        existing.requires_admin_approval = requires_admin
        existing.requires_gm_approval = requires_gm
        db.commit()
        db.refresh(existing)
        return existing
    new_expense = models.Expense(
        id=payload.id, visit_id=payload.visit_id, rep_id=current_user.id,
        category=payload.category, amount=payload.amount, description=payload.description,
        requires_admin_approval=requires_admin, requires_gm_approval=requires_gm,
    )
    db.add(new_expense)
    db.commit()
    db.refresh(new_expense)
    create_activity_log(db, current_user.id, current_user.full_name, "log_submitted_expense", request=request, amount=f"{new_expense.amount:.2f}")
    return new_expense


@router.get("/expenses/all", response_model=List[schemas.ExpenseResponse])
def get_all_expenses(
    status: Optional[str] = None, brand_id: Optional[str] = None,
    region_id: Optional[str] = None,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    if current_user.role not in ("admin", "general_manager"):
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
    scope = auth.get_scope(current_user, db, requested_region_id=region_id)
    query = db.query(models.Expense)
    if scope.region_ids is not None or brand_id:
        query = query.join(models.User, models.Expense.rep_id == models.User.id)
    if scope.region_ids is not None:
        query = query.filter(models.User.region_id.in_(scope.region_ids))
    if brand_id:
        query = query.filter(or_(
            models.User.brand_id == brand_id,
            models.User.brands.any(models.Brand.id == brand_id),
        ))
    if status and status != "all":
        query = query.filter(models.Expense.status == status)
    return query.order_by(models.Expense.created_at.desc()).all()


@router.get("/expenses/my", response_model=List[schemas.ExpenseResponse])
def get_my_expenses(current_user: models.User = Depends(auth.require_password_set), db: Session = Depends(get_db)):
    return db.query(models.Expense).filter(models.Expense.rep_id == current_user.id).order_by(models.Expense.created_at.desc()).all()


@router.get("/expenses/team", response_model=List[schemas.ExpenseResponse])
def get_team_expenses(
    status: Optional[str] = None,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
):
    rep_ids = get_role_scoped_rep_ids(current_user, db)
    query = db.query(models.Expense)
    if status and status != "all":
        query = query.filter(models.Expense.status == status)
    if rep_ids is not None:
        query = query.filter(models.Expense.rep_id.in_(rep_ids))
    return query.order_by(models.Expense.created_at.desc()).all()


@router.get("/visits/{visit_id}/expenses", response_model=List[schemas.ExpenseResponse])
def get_visit_expenses(visit_id: str, current_user: models.User = Depends(auth.require_password_set), db: Session = Depends(get_db), request: Request = None):
    visit = db.query(models.Visit).filter(models.Visit.id == visit_id).first()
    if not visit:
        raise HTTPException(status_code=404, detail=get_text("visit_not_found", request))
    # SECURITY: role-scoped access (was readable by any authenticated user)
    _assert_can_manage_visit(current_user, visit, db, request)
    return db.query(models.Expense).filter(models.Expense.visit_id == visit_id).all()


@router.post("/expenses/{expense_id}/receipt")
async def upload_expense_receipt(
    expense_id: str, file: UploadFile,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    expense = db.query(models.Expense).filter(models.Expense.id == expense_id).first()
    if not expense:
        raise HTTPException(status_code=404, detail=get_text("expense_not_found", request))
    if expense.rep_id != current_user.id:
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail=get_text("invalid_file_type", request))
    contents = await file.read()
    if len(contents) > MAX_SIZE:
        raise HTTPException(status_code=400, detail=get_text("file_too_large", request))
    result = await store_file_locally(contents, file.content_type, "receipts", None, db)
    expense.receipt_image_url = result["local_path"]
    expense.receipt_file_id = result["stored_file"].id
    db.commit()
    db.refresh(expense)
    return expense


@router.patch("/expenses/{expense_id}/status", response_model=schemas.ExpenseResponse)
def update_expense_status(
    expense_id: str, payload: schemas.ExpenseStatusUpdate,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    if payload.status not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail=get_text("invalid_status", request))
    expense = db.query(models.Expense).filter(models.Expense.id == expense_id).first()
    if not expense:
        raise HTTPException(status_code=404, detail=get_text("expense_not_found", request))
    if expense.rep_id == current_user.id:
        raise HTTPException(status_code=403, detail=get_text("cannot_approve_own", request))
    if current_user.role == "supervisor" and expense.requires_gm_approval:
        raise HTTPException(status_code=403, detail=get_text("requires_gm_approval", request))
    elif current_user.role not in ("admin", "general_manager"):
        visit = db.query(models.Visit).filter(
            models.Visit.id == expense.visit_id
        ).first() if expense.visit_id else None
        _assert_can_manage_visit(
            current_user, visit or expense.rep_id, db, request
        )
    if expense.status not in ["pending", "escalated"]:
        raise HTTPException(status_code=409, detail=get_text("already_reviewed", request))
    if payload.status == "approved" and current_user.role not in ("admin", "general_manager"):
        expense.status = "escalated" if expense.requires_admin_approval else "approved"
    else:
        expense.status = payload.status
    expense.rejection_reason = payload.rejection_reason
    if payload.status == "approved" and expense.status == "approved":
        expense.approved_by = current_user.id
        expense.approved_at = datetime.datetime.now(datetime.timezone.utc)
    db.commit()
    db.refresh(expense)
    return expense


# ── Sync ─────────────────────────────────────────────────────
@router.post("/sync/abandon")
async def abandon_sync_item(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set),
    request: Request = None,
):
    if current_user.supervisor_id:
        import uuid
        db.add(models.Notification(
            id=str(uuid.uuid4()),
            user_id=current_user.supervisor_id,
            type="sync_abandoned",
            title=get_text("sync_abandoned_title", request),
            message=get_text("sync_abandoned_msg", request).format(rep=current_user.full_name, type=payload.get('type')),
            related_id=payload.get("id"),
            created_at=datetime.datetime.now(datetime.timezone.utc),
        ))
        db.commit()
    return {"message": get_text("success_update", request)}
