"""routers/misc_router.py — notes, targets, brands, clients, field-reports, logs"""
import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, Request
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from routers.deps import get_text

import models, schemas, auth
from database import get_db
from routers.deps import (
    get_role_scoped_rep_ids,
    dispatch_notification_sync,
    create_activity_log,
    ALLOWED_TYPES, MAX_SIZE, store_file_locally,
)

router = APIRouter()


def _get_scoped_rep_ids(scope, db: Session) -> List[str]:
    query = db.query(models.User.id).filter(models.User.role == "rep")
    if scope.rep_ids is not None:
        query = query.filter(models.User.id.in_(scope.rep_ids))
    if scope.region_ids is not None:
        query = query.filter(models.User.region_id.in_(scope.region_ids))
    if scope.brand_ids is not None:
        if not scope.brand_ids:
            return []
        query = query.filter(or_(
            models.User.brand_id.in_(scope.brand_ids),
            models.User.brands.any(models.Brand.id.in_(scope.brand_ids)),
        ))
    return [rep_id for (rep_id,) in query.all()]


def _scoped_target_views(target_id: str, current_user: models.User, db: Session):
    query = db.query(models.TargetView).filter(models.TargetView.target_id == target_id)
    scope = auth.get_scope(current_user, db)
    unrestricted = (
        scope.rep_ids is None
        and scope.region_ids is None
        and scope.brand_ids is None
    )
    if not unrestricted:
        allowed_ids = _get_scoped_rep_ids(scope, db)
        if current_user.role == "rep":
            allowed_ids = [current_user.id]
        elif current_user.role == "supervisor":
            allowed_ids.append(current_user.id)
        query = query.filter(models.TargetView.rep_id.in_(allowed_ids))
    return query


# ── Notes ────────────────────────────────────────────────────
def _note_response_models(notes, db):
    cache = {}
    for n in notes:
        for uid, attr in ((n.sender_id, "sender"), (n.recipient_id, "recipient")):
            if uid not in cache:
                u = db.query(models.User).filter(models.User.id == uid).first()
                cache[uid] = u.full_name if u else None
            setattr(n, f"{attr}_name", cache[uid])
    return notes


def _can_send_note_to(current_user: models.User, target: models.User, db: Session) -> bool:
    if current_user.role == "admin":
        return True
    if current_user.role == "general_manager":
        return target.role in ("rep", "supervisor")
    if current_user.role == "supervisor":
        scope = auth.get_scope(current_user, db)
        return (
            target.role == "rep"
            and target.supervisor_id == current_user.id
            and target.id in _get_scoped_rep_ids(scope, db)
        )
    if current_user.role == "rep":
        return target.role == "supervisor" and current_user.supervisor_id == target.id
    return False


@router.post("/users/{user_id}/notes", response_model=schemas.NoteResponse)
def send_note(
    user_id: str,
    payload: schemas.NoteCreate,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail=get_text("user_not_found", request))
    if not _can_send_note_to(current_user, target, db):
        raise HTTPException(status_code=403, detail=get_text("not_authorized_notes", request))
    if payload.visit_id:
        visit = db.query(models.Visit).filter(models.Visit.id == payload.visit_id).first()
        if not visit:
            raise HTTPException(status_code=404, detail=get_text("visit_not_found", request))
        if current_user.role == "rep":
            visit_is_related = visit.rep_id == current_user.id
        elif current_user.role == "supervisor":
            visit_is_related = visit.rep_id == target.id
        else:
            visit_is_related = visit.rep_id in (current_user.id, target.id)
        scope = auth.get_scope(current_user, db)
        visit_region_id = visit.region_id
        visit_brand_id = visit.brand_id
        related_target = None
        if visit.client_id:
            related_target = db.query(models.Client).filter(
                models.Client.id == visit.client_id
            ).first()
        if related_target is None and visit.center_id:
            related_target = db.query(models.Center).filter(
                models.Center.id == visit.center_id
            ).first()
        if related_target:
            visit_region_id = visit_region_id or related_target.region_id
            visit_brand_id = visit_brand_id or related_target.brand_id
        visit_rep = db.query(models.User).filter(
            models.User.id == visit.rep_id
        ).first()
        if visit_rep:
            visit_region_id = visit_region_id or visit_rep.region_id
            rep_brands = set(visit_rep.brand_ids)
            if visit_rep.brand_id:
                rep_brands.add(visit_rep.brand_id)
            if not visit_brand_id and len(rep_brands) == 1:
                visit_brand_id = next(iter(rep_brands))
        if (
            not visit_is_related
            or (scope.region_ids is not None and visit_region_id not in scope.region_ids)
            or (scope.brand_ids is not None and visit_brand_id not in scope.brand_ids)
        ):
            raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
    note = models.Note(
        sender_id=current_user.id, recipient_id=user_id,
        content=payload.content, visit_id=payload.visit_id or None,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    dispatch_notification_sync(
        db, user_id=user_id, type="direct_note",
        title=get_text("note_from_title", request).format(name=current_user.full_name),
        message=payload.content, related_id=note.id,
    )
    _note_response_models([note], db)
    return note


@router.post("/reps/{rep_id}/notes", response_model=schemas.NoteResponse)
def send_note_to_rep(
    rep_id: str,
    payload: schemas.NoteCreate,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
):
    """Backwards-compatible wrapper."""
    return send_note(rep_id, payload, current_user, db)


@router.get("/notes", response_model=List[schemas.NoteResponse])
def list_notes(
    user_id: Optional[str] = None,
    visit_id: Optional[str] = None,
    visit_center_id: Optional[str] = None,
    limit: int = 50,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
):
    q = db.query(models.Note)
    if current_user.role == "rep":
        q = q.filter(
            (models.Note.sender_id == current_user.id) |
            (models.Note.recipient_id == current_user.id)
        )
    elif current_user.role == "supervisor":
        scope = auth.get_scope(current_user, db)
        team_ids = _get_scoped_rep_ids(scope, db) + [current_user.id]
        q = q.filter(
            models.Note.sender_id.in_(team_ids),
            models.Note.recipient_id.in_(team_ids),
        )
    if user_id:
        q = q.filter((models.Note.sender_id == user_id) | (models.Note.recipient_id == user_id))
    if visit_id:
        q = q.filter(models.Note.visit_id == visit_id)
    if visit_center_id:
        q = q.join(models.Visit, models.Note.visit_id == models.Visit.id).filter(
            models.Visit.center_id == visit_center_id
        )
    notes = q.order_by(models.Note.created_at.desc()).limit(min(limit, 200)).all()
    _note_response_models(notes, db)
    return notes


# ── Targets ──────────────────────────────────────────────────
def _assert_can_manage_target(
    current_user: models.User,
    target_rep_id: str,
    db: Session,
    request: Request,
    brand_id: Optional[str] = None,
):
    scope = auth.get_scope(current_user, db)
    if scope.brand_ids is not None and brand_id not in scope.brand_ids:
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
    if current_user.role == "admin":
        return
    if current_user.role == "supervisor":
        rep = db.query(models.User).filter(models.User.id == target_rep_id).first()
        if (
            rep
            and scope.rep_ids is not None
            and rep.id in scope.rep_ids
            and scope.region_ids is not None
            and rep.region_id in scope.region_ids
        ):
            return
    raise HTTPException(status_code=403, detail=get_text("not_authorized_rep", request))


@router.get("/targets", response_model=List[schemas.TargetResponse])
def get_targets(
    brand_id: Optional[str] = None,
    region_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set),
):
    scope = auth.get_scope(current_user, db, requested_region_id=region_id)
    query = db.query(models.Target)
    if scope.rep_ids is not None:
        query = query.filter(models.Target.rep_id.in_(scope.rep_ids))
    if scope.region_ids is not None:
        query = query.join(models.User, models.Target.rep_id == models.User.id).filter(
            models.User.region_id.in_(scope.region_ids)
        )
    if scope.brand_ids is not None:
        query = query.join(
            models.Product, models.Target.product_id == models.Product.id
        ).filter(
            func.coalesce(models.Target.brand_id, models.Product.brand_id).in_(
                scope.brand_ids
            )
        )
    if brand_id:
        query = query.filter(models.Target.brand_id == brand_id)
    results = []
    for t in query.all():
        achieved = db.query(func.sum(models.VisitItem.qty_sold)).join(models.Visit).filter(
            models.VisitItem.product_id == t.product_id,
            models.Visit.rep_id == t.rep_id,
            models.Visit.visit_date.between(t.period_start, t.period_end),
            models.Visit.status.notin_(["flagged", "rejected"]),
        ).scalar() or 0
        prod = db.query(models.Product).filter(models.Product.id == t.product_id).first()
        view_count = _scoped_target_views(t.id, current_user, db).count()
        user_viewed = db.query(models.TargetView).filter(
            models.TargetView.target_id == t.id,
            models.TargetView.rep_id == current_user.id,
        ).first() is not None
        results.append({**t.__dict__, "achieved_qty": achieved, "product_name": prod.name if prod else None,
                        "view_count": view_count, "user_viewed": user_viewed})
    return results


def _get_scoped_target(target_id: str, current_user: models.User, db: Session, request: Request):
    target = db.query(models.Target).filter(models.Target.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail=get_text("target_not_found", request))
    scope = auth.get_scope(current_user, db)
    rep = db.query(models.User).filter(models.User.id == target.rep_id).first()
    product = db.query(models.Product).filter(
        models.Product.id == target.product_id
    ).first()
    if (
        (scope.rep_ids is not None and target.rep_id not in scope.rep_ids)
        or (scope.region_ids is not None and (not rep or rep.region_id not in scope.region_ids))
        or (
            scope.brand_ids is not None
            and (target.brand_id or (product.brand_id if product else None))
            not in scope.brand_ids
        )
    ):
        raise HTTPException(status_code=403, detail=get_text("not_authorized_rep", request))
    return target


@router.post("/targets", response_model=schemas.TargetResponse)
def create_target(
    payload: schemas.TargetCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set),
    request: Request = None,
):
    product = db.query(models.Product).filter(
        models.Product.id == payload.product_id
    ).first()
    if not product:
        raise HTTPException(status_code=404, detail=get_text("product_not_found", request))
    _assert_can_manage_target(
        current_user, payload.rep_id, db, request, product.brand_id
    )
    t = models.Target(
        product_id=payload.product_id, brand_id=product.brand_id, rep_id=payload.rep_id,
        period_start=payload.period_start, period_end=payload.period_end,
        target_qty=payload.target_qty, notes=payload.notes,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    prod = db.query(models.Product).filter(models.Product.id == payload.product_id).first()
    return {**t.__dict__, "achieved_qty": 0, "product_name": prod.name if prod else None, "view_count": 0}


@router.post("/targets/bulk")
def create_bulk_targets(
    payload: schemas.TargetBulkCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set),
    request: Request = None,
):
    if current_user.role not in ("admin", "supervisor"):
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
    product = db.query(models.Product).filter(
        models.Product.id == payload.product_id
    ).first()
    if not product:
        raise HTTPException(status_code=404, detail=get_text("product_not_found", request))
    created_ids = []
    for rep_id in payload.rep_ids:
        # Same authorization as single-target creation — supervisors may
        # only assign targets to their own reps.
        _assert_can_manage_target(
            current_user, rep_id, db, request, product.brand_id
        )
        t = models.Target(
            product_id=payload.product_id, brand_id=product.brand_id, rep_id=rep_id,
            period_start=payload.period_start, period_end=payload.period_end,
            target_qty=payload.target_qty, notes=payload.notes,
        )
        db.add(t)
        db.flush()
        created_ids.append(t.id)
    db.commit()
    return {"message": get_text("success_create", request), "target_ids": created_ids}


@router.put("/targets/{target_id}", response_model=schemas.TargetResponse)
def update_target(
    target_id: str,
    payload: schemas.TargetUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set),
    request: Request = None,
):
    t = db.query(models.Target).filter(models.Target.id == target_id).first()
    if not t:
        raise HTTPException(status_code=404, detail=get_text("target_not_found", request))
    product = db.query(models.Product).filter(models.Product.id == t.product_id).first()
    _assert_can_manage_target(
        current_user, t.rep_id, db, request,
        t.brand_id or (product.brand_id if product else None),
    )
    if payload.period_start: t.period_start = payload.period_start
    if payload.period_end: t.period_end = payload.period_end
    if payload.target_qty is not None: t.target_qty = payload.target_qty
    db.commit()
    db.refresh(t)
    achieved = db.query(func.sum(models.VisitItem.qty_sold)).join(models.Visit).filter(
        models.VisitItem.product_id == t.product_id,
        models.Visit.rep_id == t.rep_id,
        models.Visit.visit_date.between(t.period_start, t.period_end),
        models.Visit.status.notin_(["flagged", "rejected"]),
    ).scalar() or 0
    view_count = _scoped_target_views(t.id, current_user, db).count()
    prod = db.query(models.Product).filter(models.Product.id == t.product_id).first()
    return {**t.__dict__, "achieved_qty": achieved, "product_name": prod.name if prod else None, "view_count": view_count}


@router.delete("/targets/{target_id}")
def delete_target(target_id: str, db: Session = Depends(get_db), current_user: models.User = Depends(auth.require_password_set), request: Request = None):
    t = db.query(models.Target).filter(models.Target.id == target_id).first()
    if not t:
        raise HTTPException(status_code=404, detail=get_text("target_not_found", request))
    product = db.query(models.Product).filter(models.Product.id == t.product_id).first()
    _assert_can_manage_target(
        current_user, t.rep_id, db, request,
        t.brand_id or (product.brand_id if product else None),
    )
    db.delete(t)
    db.commit()
    return {"message": get_text("success_delete", request)}


@router.post("/targets/{target_id}/view")
def view_target(target_id: str, db: Session = Depends(get_db), current_user: models.User = Depends(auth.require_password_set), request: Request = None):
    t = _get_scoped_target(target_id, current_user, db, request)
    if not db.query(models.TargetView).filter(models.TargetView.target_id == target_id, models.TargetView.rep_id == current_user.id).first():
        db.add(models.TargetView(target_id=target_id, rep_id=current_user.id))
        db.commit()
    return {"message": get_text("success_update", request)}


@router.get("/targets/{target_id}/views", response_model=List[schemas.TargetViewResponse])
def get_target_views(target_id: str, db: Session = Depends(get_db), current_user: models.User = Depends(auth.require_password_set), request: Request = None):
    _get_scoped_target(target_id, current_user, db, request)
    results = []
    for v in _scoped_target_views(target_id, current_user, db).all():
        rep = db.query(models.User).filter(models.User.id == v.rep_id).first()
        results.append({**v.__dict__, "rep_name": rep.full_name if rep else None})
    return results


# ── Clients ──────────────────────────────────────────────────
def _client_profile_complete(client: models.Client) -> bool:
    if client.client_type == "doctor":
        name, classification = client.doctor_name, client.specialty
    elif client.client_type == "pharmacy":
        name, classification = client.facility_name, client.pharmacy_type
    else:
        name, classification = client.facility_name, client.institution_type or client.facility_type
    return bool(name and client.phone_number and classification and (client.region_id or client.region))


def _assert_can_touch_client(
    current_user: models.User,
    client: models.Client,
    db: Session,
    request: Request,
    *,
    allow_regional_read: bool = False,
):
    """Enforce profile access against the caller's region, team, and brands."""
    if current_user.role in ("admin", "general_manager"):
        return
    scope = auth.get_scope(current_user, db)
    if allow_regional_read and current_user.role == "overseer":
        return
    if (
        client.rep_id == current_user.id
        and client.region_id in (scope.region_ids or [])
        and client.brand_id in (scope.brand_ids or [])
    ):
        return
    if allow_regional_read and current_user.role == "rep":
        if (
            client.region_id in (scope.region_ids or [])
            and client.brand_id in (scope.brand_ids or [])
        ):
            return
    if current_user.role == "supervisor":
        rep = db.query(models.User).filter(models.User.id == client.rep_id).first()
        if (
            rep
            and (scope.rep_ids is None or rep.id in scope.rep_ids)
            and (scope.region_ids is None or rep.region_id in scope.region_ids)
            and (scope.region_ids is None or client.region_id in scope.region_ids)
            and (scope.brand_ids is None or client.brand_id in scope.brand_ids)
        ):
            return
    raise HTTPException(status_code=403, detail=get_text("not_authorized", request))


@router.get("/clients/{client_id}", response_model=schemas.ClientResponse)
def get_client(
    client_id: str,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    client = db.query(models.Client).filter(models.Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail=get_text("client_not_found", request))
    _assert_can_touch_client(
        current_user, client, db, request, allow_regional_read=True
    )
    return client


@router.get("/clients", response_model=List[schemas.ClientResponse])
def get_clients(
    rep_id: Optional[str] = None,
    brand_id: Optional[str] = None,
    region_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
):
    scope = auth.get_scope(current_user, db, requested_region_id=region_id)
    query = db.query(models.Client)
    
    if current_user.role not in ("admin", "general_manager"):
        user_brand_ids = [b.id for b in current_user.brands]
        if current_user.brand_id and current_user.brand_id not in user_brand_ids:
            user_brand_ids.append(current_user.brand_id)
        query = query.filter(models.Client.brand_id.in_(user_brand_ids))
        
    if brand_id:
        query = query.filter(models.Client.brand_id == brand_id)

    if scope.region_ids is not None:
        query = query.filter(models.Client.region_id.in_(scope.region_ids))

    if rep_id:
        if scope.rep_ids is not None and rep_id not in scope.rep_ids:
            raise HTTPException(status_code=403, detail="Not authorized to access this rep's clients")
        if not (current_user.role == "rep" and rep_id == current_user.id):
            query = query.filter(models.Client.rep_id == rep_id)
        
    return query.offset(offset).limit(limit).all()


@router.get("/directory", response_model=List[schemas.DirectoryItemResponse])
def get_directory(
    type: Optional[str] = None,
    region_id: Optional[str] = None,
    brand_id: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
):
    """
    Unified directory for doctors, pharmacies, and centers with region and search filtering,
    strictly scoped by role and assigned regions.
    """
    scope = auth.get_scope(current_user, db, requested_region_id=region_id)
    results = []

    regions_cache = {r.id: (r.name_ar, r.name_en) for r in db.query(models.Region).all()}

    # 1. Doctors, pharmacies, and institution profiles (shown as centers).
    if not type or type in ("doctor", "pharmacy", "center", "institution"):
        client_q = db.query(models.Client).outerjoin(
            models.User, models.Client.rep_id == models.User.id
        )
        if current_user.role not in ("admin", "general_manager", "overseer"):
            user_brand_ids = [b.id for b in current_user.brands]
            if current_user.brand_id and current_user.brand_id not in user_brand_ids:
                user_brand_ids.append(current_user.brand_id)
            client_q = client_q.filter(models.Client.brand_id.in_(user_brand_ids))

        if scope.region_ids is not None:
            client_q = client_q.filter(models.Client.region_id.in_(scope.region_ids))

        if scope.role == "supervisor" and scope.rep_ids is not None:
            client_q = client_q.filter(models.Client.rep_id.in_(scope.rep_ids))

        if type in ("doctor", "pharmacy"):
            client_q = client_q.filter(models.Client.client_type == type)
        elif type in ("center", "institution"):
            client_q = client_q.filter(models.Client.client_type == "institution")

        if brand_id:
            client_q = client_q.filter(models.Client.brand_id == brand_id)

        if q:
            search_pattern = f"%{q}%"
            client_q = client_q.filter(
                or_(
                    models.Client.doctor_name.ilike(search_pattern),
                    models.Client.facility_name.ilike(search_pattern),
                    models.Client.phone_number.ilike(search_pattern),
                    models.Client.specialty.ilike(search_pattern),
                    models.Client.area.ilike(search_pattern),
                    models.Client.street.ilike(search_pattern),
                    models.Client.nearby_landmark.ilike(search_pattern),
                    models.User.full_name.ilike(search_pattern),
                )
            )

        clients = client_q.order_by(models.Client.id).offset(offset).limit(limit).all()
        rep_ids = [c.rep_id for c in clients if c.rep_id]
        reps_map = {u.id: u.full_name for u in db.query(models.User).filter(models.User.id.in_(rep_ids)).all()} if rep_ids else {}

        for c in clients:
            item_type = "center" if c.client_type == "institution" else (c.client_type or "doctor")
            item_name = c.doctor_name if item_type == "doctor" and c.doctor_name else (c.facility_name or "Unknown")
            r_names = regions_cache.get(c.region_id)
            r_name = r_names[0] if r_names else c.region
            results.append({
                "id": c.id,
                "type": item_type,
                "is_client": True,
                "name": item_name,
                "region_id": c.region_id,
                "region_name": r_name,
                "area": c.area,
                "address": c.street or c.nearby_landmark,
                "phone": c.phone_number,
                "specialty": c.specialty,
                "facility_name": c.facility_name,
                "latitude": c.latitude,
                "longitude": c.longitude,
                "rep_id": c.rep_id,
                "rep_name": reps_map.get(c.rep_id),
                "brand_id": c.brand_id,
                "status": c.status or "active",
                "profile_complete": c.status != "incomplete" and _client_profile_complete(c),
                "doctor_name": c.doctor_name,
                "client_type": c.client_type,
                "facility_type": c.facility_type,
                "birth_date": c.birth_date,
                "class_tier": c.class_tier,
                "relationship_type": c.relationship_type,
                "description": c.description,
                "street": c.street,
                "nearby_landmark": c.nearby_landmark,
                "photo_url": c.photo_url,
                "gender": c.gender,
                "rating": c.rating,
                "treatment_quality": c.treatment_quality,
                "scientific_interests": c.scientific_interests,
                "product_interests": c.product_interests,
                "pharmacy_type": c.pharmacy_type,
                "institution_type": c.institution_type,
                "key_contact_name": c.key_contact_name,
                "key_contact_position": c.key_contact_position,
                "key_contact_phone": c.key_contact_phone,
                "departments": c.departments,
            })

    # 2. Centers
    if not type or type == "center":
        center_q = db.query(models.Center).outerjoin(
            models.User, models.Center.assigned_rep_id == models.User.id
        ).filter(models.Center.is_active == True)
        if current_user.role not in ("admin", "general_manager", "overseer"):
            user_brand_ids = [b.id for b in current_user.brands]
            if current_user.brand_id and current_user.brand_id not in user_brand_ids:
                user_brand_ids.append(current_user.brand_id)
            center_q = center_q.filter(models.Center.brand_id.in_(user_brand_ids))

        if scope.region_ids is not None:
            center_q = center_q.filter(models.Center.region_id.in_(scope.region_ids))

        if scope.role == "supervisor" and scope.rep_ids is not None:
            center_q = center_q.filter(models.Center.assigned_rep_id.in_(scope.rep_ids))

        if brand_id:
            center_q = center_q.filter(models.Center.brand_id == brand_id)

        if q:
            search_pattern = f"%{q}%"
            center_q = center_q.filter(
                or_(
                    models.Center.name.ilike(search_pattern),
                    models.Center.address.ilike(search_pattern),
                    models.User.full_name.ilike(search_pattern),
                )
            )

        centers = center_q.order_by(models.Center.id).offset(offset).limit(limit).all()
        rep_ids = [c.assigned_rep_id for c in centers if c.assigned_rep_id]
        reps_map = {
            u.id: u.full_name
            for u in db.query(models.User).filter(
                models.User.id.in_(rep_ids), models.User.role == "rep"
            ).all()
        } if rep_ids else {}

        for c in centers:
            r_names = regions_cache.get(c.region_id)
            r_name = r_names[0] if r_names else c.region
            results.append({
                "id": c.id,
                "type": "center",
                "is_client": False,
                "name": c.name,
                "region_id": c.region_id,
                "region_name": r_name,
                "area": None,
                "address": c.address,
                "phone": None,
                "specialty": None,
                "facility_name": c.name,
                "latitude": c.latitude,
                "longitude": c.longitude,
                "rep_id": c.assigned_rep_id,
                "rep_name": reps_map.get(c.assigned_rep_id),
                "brand_id": c.brand_id,
                "status": c.status or "active",
                "profile_complete": bool(c.name and c.region_id and c.address and c.assigned_rep_id),
                "client_type": "center",
            })

    return results


@router.post("/clients", response_model=schemas.ClientResponse)
def create_client(
    payload: schemas.ClientCreate,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    if current_user.role not in ("admin", "general_manager", "rep", "supervisor"):
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
    if current_user.role == "rep" and payload.rep_id != current_user.id:
        raise HTTPException(status_code=403, detail=get_text("cannot_create_client_other_rep", request))
    assigned_rep = current_user

    chosen_brand_id = (
        assigned_rep.brand_id
        if current_user.role == "rep"
        else payload.brand_id
    )
    if current_user.role == "rep" and not chosen_brand_id and len(assigned_rep.brand_ids) == 1:
        chosen_brand_id = assigned_rep.brand_ids[0]
    if not chosen_brand_id or not db.query(models.Brand).filter(
        models.Brand.id == chosen_brand_id,
        models.Brand.is_active == True,
    ).first():
        raise HTTPException(status_code=422, detail=get_text("account_no_brand", request))

    if current_user.role in ("rep", "supervisor"):
        allowed_brand_ids = {brand.id for brand in current_user.brands}
        if current_user.brand_id:
            allowed_brand_ids.add(current_user.brand_id)
        if chosen_brand_id not in allowed_brand_ids:
            raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
    chosen_region_id = current_user.region_id if current_user.role == "rep" else payload.region_id
    if current_user.role == "rep" and not chosen_region_id and current_user.region:
        region_match = db.query(models.Region).filter(
            (models.Region.name_ar == current_user.region)
            | (models.Region.name_en.ilike(current_user.region))
        ).first()
        chosen_region_id = region_match.id if region_match else None
    if current_user.role == "supervisor":
        scope = auth.get_scope(current_user, db)
        if chosen_region_id not in (scope.region_ids or []):
            raise HTTPException(
                status_code=403,
                detail=get_text("cannot_create_client_outside_supervision", request),
            )
    if not chosen_region_id:
        raise HTTPException(status_code=422, detail="A region is required")
    if not db.query(models.Region).filter(models.Region.id == chosen_region_id).first():
        raise HTTPException(status_code=422, detail="Unknown region")
    region = db.query(models.Region).filter(models.Region.id == chosen_region_id).first()

    existing = db.query(models.Client).filter(models.Client.id == payload.id).first()
    if existing:
        if (
            existing.rep_id != current_user.id
            or existing.brand_id != chosen_brand_id
            or existing.region_id != chosen_region_id
        ):
            raise HTTPException(status_code=409, detail=get_text("not_authorized", request))
        return existing

    values = payload.model_dump()
    values.update(
        rep_id=current_user.id,
        region_id=chosen_region_id,
        region=region.name_en or region.name_ar,
        brand_id=chosen_brand_id,
    )
    new_client = models.Client(**values)
    new_client.status = "active" if _client_profile_complete(new_client) else "incomplete"
    db.add(new_client)
    db.commit()
    db.refresh(new_client)
    
    target_name = new_client.doctor_name if new_client.client_type == 'doctor' else new_client.facility_name
    create_activity_log(db, current_user.id, current_user.full_name, "log_added_client", request=request, type=new_client.client_type, name=target_name)
    
    return new_client



@router.put("/clients/{client_id}", response_model=schemas.ClientResponse)
def update_client(
    client_id: str,
    payload: schemas.ClientCreate,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    client = db.query(models.Client).filter(models.Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail=get_text("client_not_found", request))
    _assert_can_touch_client(current_user, client, db, request)
    for key, value in payload.dict(exclude_unset=True).items():
        if key in ("region", "region_id", "rep_id", "brand_id") and current_user.role != "admin":
            continue
        setattr(client, key, value)
    client.status = "active" if _client_profile_complete(client) else "incomplete"
    client.updated_at = datetime.datetime.now(datetime.timezone.utc)
    db.commit()
    db.refresh(client)
    return client


@router.delete("/clients/{client_id}")
def delete_client(
    client_id: str,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    client = db.query(models.Client).filter(models.Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail=get_text("client_not_found", request))
    _assert_can_touch_client(current_user, client, db, request)
    db.delete(client)
    db.commit()
    return {"message": get_text("success_delete", request)}


# ── Brands ───────────────────────────────────────────────────
@router.get("/brands", response_model=List[schemas.BrandResponse])
def get_brands(db: Session = Depends(get_db), current_user: models.User = Depends(auth.require_password_set)):
    query = db.query(models.Brand)
    if current_user.role not in ("admin", "general_manager", "overseer"):
        brand_ids = [brand.id for brand in current_user.brands]
        if current_user.brand_id and current_user.brand_id not in brand_ids:
            brand_ids.append(current_user.brand_id)
        query = query.filter(models.Brand.id.in_(brand_ids))
    return query.all()


@router.get("/brands/{brand_id}", response_model=schemas.BrandResponse)
def get_brand(brand_id: str, db: Session = Depends(get_db), current_user: models.User = Depends(auth.require_password_set), request: Request = None):
    b = db.query(models.Brand).filter(models.Brand.id == brand_id).first()
    if not b:
        raise HTTPException(status_code=404, detail=get_text("brand_not_found", request))
    return b

@router.post("/brands", response_model=schemas.BrandResponse)
def create_brand(payload: schemas.BrandCreate, db: Session = Depends(get_db), current_user: models.User = Depends(auth.require_password_set), request: Request = None):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
    if db.query(models.Brand).filter(models.Brand.name == payload.name).first():
        raise HTTPException(status_code=400, detail=get_text("brand_exists", request))
    b = models.Brand(name=payload.name, logo_url=payload.logo_url)
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


@router.put("/brands/{brand_id}", response_model=schemas.BrandResponse)
def update_brand(brand_id: str, payload: schemas.BrandUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(auth.require_password_set), request: Request = None):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
    b = db.query(models.Brand).filter(models.Brand.id == brand_id).first()
    if not b:
        raise HTTPException(status_code=404, detail=get_text("brand_not_found", request))
    if payload.name is not None: b.name = payload.name
    if payload.logo_url is not None: b.logo_url = payload.logo_url
    if payload.is_active is not None: b.is_active = payload.is_active
    db.commit()
    db.refresh(b)
    return b


@router.patch("/brands/{brand_id}/toggle", response_model=schemas.BrandResponse)
def toggle_brand(brand_id: str, db: Session = Depends(get_db), current_user: models.User = Depends(auth.require_password_set), request: Request = None):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
    b = db.query(models.Brand).filter(models.Brand.id == brand_id).first()
    if not b:
        raise HTTPException(status_code=404, detail=get_text("brand_not_found", request))
    b.is_active = not b.is_active
    db.commit()
    db.refresh(b)
    return b


# ── Field Reports ─────────────────────────────────────────────
@router.post("/field-reports", response_model=schemas.FieldReportResponse)
def create_field_report(report: schemas.FieldReportCreate, db: Session = Depends(get_db), current_user: models.User = Depends(auth.require_password_set), request: Request = None):
    # SECURITY: rep_id is taken from the authenticated user, never from the
    # payload (was spoofable — anyone could file reports as someone else).
    if report.rep_id != current_user.id:
        raise HTTPException(status_code=403, detail=get_text("cannot_file_report_other_user", request))
    db_report = db.query(models.FieldReport).filter(models.FieldReport.id == report.id).first()
    if db_report:
        if db_report.rep_id != current_user.id:
            raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
        db_report.content = report.content
        db_report.photo_url = report.photo_url
    else:
        db_report = models.FieldReport(id=report.id, rep_id=current_user.id, content=report.content, photo_url=report.photo_url)
        db.add(db_report)
    db.commit()
    db.refresh(db_report)
    
    create_activity_log(db, current_user.id, current_user.full_name, "log_submitted_report", request=request, content=db_report.content[:30])
    
    return db_report


@router.get("/field-reports", response_model=List[schemas.FieldReportResponse])
def get_field_reports(
    rep_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set),
    request: Request = None,
):
    scope = auth.get_scope(current_user, db)
    query = db.query(models.FieldReport)
    unrestricted = (
        scope.rep_ids is None
        and scope.region_ids is None
        and scope.brand_ids is None
    )

    # 1. Priority: If a specific rep_id is requested, filter by it (after checking authorization)
    if rep_id:
        if not unrestricted and rep_id not in _get_scoped_rep_ids(scope, db):
            raise HTTPException(status_code=403, detail=get_text("not_authorized_reports", request))
        query = query.filter(models.FieldReport.rep_id == rep_id)
    elif not unrestricted:
        query = query.filter(models.FieldReport.rep_id.in_(_get_scoped_rep_ids(scope, db)))

    return query.offset(offset).limit(limit).all()


@router.post("/field-reports/{report_id}/photo")
async def upload_field_report_photo(
    report_id: str,
    file: UploadFile,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    report = db.query(models.FieldReport).filter(models.FieldReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail=get_text("field_report_not_found", request))
    if report.rep_id != current_user.id:
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail=get_text("invalid_file_type", request))
    contents = await file.read()
    if len(contents) > MAX_SIZE:
        raise HTTPException(status_code=400, detail=get_text("file_too_large", request))
    result = await store_file_locally(contents, file.content_type, "visits", None, db)
    report.photo_url = result["local_path"]
    report.photo_file_id = result["stored_file"].id
    db.commit()
    db.refresh(report)
    return {"photo_url": report.photo_url, "photo_file_id": report.photo_file_id}


# ── Activity Logs ────────────────────────────────────────────
@router.get("/logs", response_model=List[schemas.ActivityLogResponse])
def get_logs(
    log_type: Optional[str] = None,
    brand_id: Optional[str] = None,
    region_id: Optional[str] = None,
    limit: int = 100,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    if current_user.role not in ("admin", "general_manager"):
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
    scope = auth.get_scope(current_user, db, requested_region_id=region_id)
    query = db.query(models.ActivityLog)
    if scope.region_ids is not None or brand_id:
        query = query.join(models.User, models.ActivityLog.user_id == models.User.id)
    if scope.region_ids is not None:
        query = query.filter(models.User.region_id.in_(scope.region_ids))
    if brand_id:
        query = query.filter(or_(
            models.User.brand_id == brand_id,
            models.User.brands.any(models.Brand.id == brand_id),
        ))
    if log_type and log_type != "all":
        query = query.filter(models.ActivityLog.log_type == log_type)
    return query.order_by(models.ActivityLog.created_at.desc()).limit(limit).all()

@router.get("/audit-logs", response_model=List[schemas.ActivityLogResponse])
def get_audit_logs(
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set),
    request: Request = None,
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail=get_text("admin_only", request))
    
    return db.query(models.ActivityLog).order_by(models.ActivityLog.created_at.desc()).offset(offset).limit(limit).all()
