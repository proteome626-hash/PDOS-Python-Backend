"""routers/centers_router.py — /centers/*"""
import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from routers.deps import get_text

import models, schemas, auth
from database import get_db
from routers.deps import (
    get_role_scoped_rep_ids,
    dispatch_notification_sync,
    create_activity_log,
    get_setting,
)

router = APIRouter()


@router.get("/centers", response_model=List[schemas.CenterResponse])
def get_centers(
    region_id: Optional[str] = None,
    brand_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set),
):
    scope = auth.get_scope(current_user, db, requested_region_id=region_id)
    query = db.query(models.Center).filter(models.Center.is_active == True)
    
    if scope.brand_ids is not None:
        query = query.filter(models.Center.brand_id.in_(scope.brand_ids))
    if brand_id:
        query = query.filter(models.Center.brand_id == brand_id)

    if scope.region_ids is not None:
        query = query.filter(models.Center.region_id.in_(scope.region_ids))

    return query.all()


@router.post("/centers", response_model=schemas.CenterResponse)
def create_center(
    payload: schemas.CenterCreate,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    if current_user.role not in ("admin", "general_manager", "supervisor", "rep"):
        raise HTTPException(status_code=403, detail=get_text("not_authorized_create_center", request))

    brand_id = payload.brand_id or current_user.brand_id
    if current_user.role == "rep" and not brand_id and len(current_user.brand_ids) == 1:
        brand_id = current_user.brand_ids[0]
    if not brand_id:
        raise HTTPException(status_code=422, detail=get_text("account_no_brand", request))
    if not db.query(models.Brand).filter(
        models.Brand.id == brand_id,
        models.Brand.is_active == True,
    ).first():
        raise HTTPException(status_code=422, detail=get_text("account_no_brand", request))
    if current_user.role in ("rep", "supervisor"):
        allowed_brands = set(current_user.brand_ids)
        if current_user.brand_id:
            allowed_brands.add(current_user.brand_id)
        if brand_id not in allowed_brands:
            raise HTTPException(status_code=403, detail=get_text("not_authorized", request))

    if payload.latitude and payload.longitude:
        existing = db.query(models.Center).filter(
            models.Center.name == payload.name,
            models.Center.brand_id == brand_id,
            models.Center.latitude.between(payload.latitude - 0.0005, payload.latitude + 0.0005),
            models.Center.longitude.between(payload.longitude - 0.0005, payload.longitude + 0.0005),
            models.Center.is_active == True,
        ).first()
    else:
        existing = db.query(models.Center).filter(
            models.Center.name == payload.name,
            models.Center.brand_id == brand_id,
            models.Center.is_active == True,
        ).first()
    if existing:
        return existing

    assigned_rep = None
    if current_user.role == "rep":
        center_region_id = current_user.region_id
        assigned_rep_id = current_user.id
    else:
        assigned_rep_id = payload.assigned_rep_id
        if assigned_rep_id:
            assigned_rep = db.query(models.User).filter(
                models.User.id == assigned_rep_id,
                models.User.role == "rep",
                models.User.is_active == True,
            ).first()
            if not assigned_rep:
                raise HTTPException(status_code=404, detail="Assigned rep not found or inactive")
            if current_user.role == "supervisor":
                scope = auth.get_scope(current_user, db)
                if (
                    (scope.rep_ids is not None and assigned_rep.id not in scope.rep_ids)
                    or (scope.region_ids is not None and assigned_rep.region_id not in scope.region_ids)
                ):
                    raise HTTPException(status_code=403, detail="Assigned rep is outside supervisor's scope")
            center_region_id = assigned_rep.region_id
        else:
            center_region_id = payload.region_id or current_user.region_id
            if current_user.role == "supervisor":
                scope = auth.get_scope(current_user, db)
                if scope.region_ids is not None and center_region_id not in scope.region_ids:
                    raise HTTPException(status_code=403, detail="Region is outside supervisor's scope")

    if current_user.role != "admin" and not center_region_id:
        raise HTTPException(status_code=422, detail="A region is required")
    if current_user.role in ("supervisor", "general_manager"):
        scope = auth.get_scope(current_user, db)
        if scope.region_ids is not None and center_region_id not in scope.region_ids:
            raise HTTPException(status_code=403, detail="Region is outside the user's scope")
    if center_region_id and not db.query(models.Region).filter(models.Region.id == center_region_id).first():
        raise HTTPException(status_code=422, detail="Unknown region")
    region = db.query(models.Region).filter(models.Region.id == center_region_id).first() if center_region_id else None
    if assigned_rep and brand_id not in (set(assigned_rep.brand_ids) | ({assigned_rep.brand_id} if assigned_rep.brand_id else set())):
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))

    new_center = models.Center(
        name=payload.name, region=(region.name_en or region.name_ar) if region else None,
        region_id=center_region_id,
        latitude=payload.latitude, longitude=payload.longitude,
        address=payload.address, assigned_rep_id=assigned_rep_id,
        brand_id=brand_id, created_by=current_user.id,
    )
    db.add(new_center)
    db.commit()
    db.refresh(new_center)
    
    create_activity_log(db, current_user.id, current_user.full_name, "log_added_center", request=request, name=new_center.name)
    
    return new_center



@router.put("/centers/{center_id}", response_model=schemas.CenterResponse)
def update_center(
    center_id: str,
    payload: schemas.CenterUpdate,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail=get_text("not_authorized_update_centers", request))
    center = db.query(models.Center).filter(models.Center.id == center_id).first()
    if not center:
        raise HTTPException(status_code=404, detail=get_text("center_not_found", request))
    if payload.name is not None: center.name = payload.name
    if payload.region is not None: center.region = payload.region
    if payload.latitude is not None: center.latitude = payload.latitude
    if payload.longitude is not None: center.longitude = payload.longitude
    if payload.address is not None: center.address = payload.address
    if payload.assigned_rep_id is not None: center.assigned_rep_id = payload.assigned_rep_id
    if payload.is_active is not None: center.is_active = payload.is_active
    db.commit()
    db.refresh(center)
    return center


@router.delete("/centers/{center_id}")
def delete_center(
    center_id: str,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail=get_text("not_authorized_delete_centers", request))
    center = db.query(models.Center).filter(models.Center.id == center_id).first()
    if not center:
        raise HTTPException(status_code=404, detail=get_text("center_not_found", request))
    center.is_active = False
    db.commit()
    return {"message": get_text("success_delete", request)}


@router.post("/centers/{center_id}/report")
def report_center(
    center_id: str,
    payload: schemas.NoteCreate,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    center = db.query(models.Center).filter(models.Center.id == center_id).first()
    if not center:
        raise HTTPException(status_code=404, detail=get_text("center_not_found", request))
    # NOTE: centers have a plain name (doctor_name/facility_name live on Client)
    name = center.name or "Unnamed center"
    if current_user.supervisor_id:
        dispatch_notification_sync(
            db, user_id=current_user.supervisor_id, type="center_report",
            title=get_text("center_report_title", request).format(name=current_user.full_name),
            message=f"{name}: {payload.content}", related_id=center_id,
        )
    create_activity_log(
        db, user_id=current_user.id, user_name=current_user.full_name,
        action=f"Center Report — {name}: {payload.content}",
    )
    return {"message": get_text("success_create", request)}


@router.get("/centers/coverage", response_model=List[schemas.CenterCoverageResponse])
def get_coverage(
    region_id: Optional[str] = None,
    brand_id: Optional[str] = None,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
):
    scope = auth.get_scope(current_user, db, requested_region_id=region_id)
    rep_ids = scope.rep_ids
    neglect_days = int(get_setting(db, "coverage_neglect_days", 14))

    q_centers = db.query(models.Center).filter(models.Center.is_active == True)
    if scope.brand_ids is not None:
        q_centers = q_centers.filter(models.Center.brand_id.in_(scope.brand_ids))
    if brand_id:
        q_centers = q_centers.filter(models.Center.brand_id == brand_id)
    if scope.region_ids is not None:
        q_centers = q_centers.filter(models.Center.region_id.in_(scope.region_ids))


    result = []
    for c in q_centers.all():
        q = db.query(func.max(models.Visit.visit_date)).filter(
            models.Visit.center_id == c.id,
            models.Visit.status.notin_(["rejected", "flagged"]),
            or_(
                models.Visit.brand_id == c.brand_id,
                models.Visit.brand_id.is_(None),
            ),
        )
        if rep_ids is not None:
            q = q.filter(models.Visit.rep_id.in_(rep_ids))
        last_visit = q.scalar()

        status = "neglected"
        if last_visit:
            days_since = (datetime.date.today() - last_visit.date()).days
            if days_since <= neglect_days:
                status = "visited"

        note_visit = db.query(models.Visit).filter(
            models.Visit.center_id == c.id,
            models.Visit.review_note.isnot(None),
            models.Visit.review_note != "",
            or_(
                models.Visit.brand_id == c.brand_id,
                models.Visit.brand_id.is_(None),
            ),
        )
        if rep_ids is not None:
            note_visit = note_visit.filter(models.Visit.rep_id.in_(rep_ids))
        if scope.region_ids is not None:
            note_visit = note_visit.filter(models.Visit.region_id.in_(scope.region_ids))
        note_visit = note_visit.order_by(models.Visit.reviewed_at.desc()).first()

        result.append({
            "center_id": c.id, "name": c.name, "lat": c.latitude, "lng": c.longitude,
            "last_visit_date": last_visit, "assigned_rep_id": c.assigned_rep_id,
            "status": status, "has_supervisor_note": note_visit is not None,
            "supervisor_note": note_visit.review_note if note_visit else None,
        })
    return result


@router.get("/centers/{center_id}", response_model=schemas.CenterResponse)
def get_center(
    center_id: str,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    center = db.query(models.Center).filter(models.Center.id == center_id, models.Center.is_active == True).first()
    if not center:
        raise HTTPException(status_code=404, detail=get_text("center_not_found", request))
    scope = auth.get_scope(current_user, db)
    if scope.brand_ids is not None and center.brand_id not in scope.brand_ids:
        raise HTTPException(status_code=403, detail=get_text("center_brand_mismatch", request))
    if scope.region_ids is not None and center.region_id not in scope.region_ids:
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
    return center
