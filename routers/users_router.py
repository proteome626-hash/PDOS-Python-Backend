"""routers/users_router.py — /users/*, /settings/*"""
import datetime
import os
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from routers.deps import get_text

import models, schemas, auth
from database import get_db
from routers.deps import (
    get_role_scoped_rep_ids,
    dispatch_notification_sync,
    ALLOWED_TYPES, MAX_SIZE,
)

router = APIRouter()

LIVE_LOCATION_MAX_AGE = datetime.timedelta(minutes=10)


def _live_location_fields(user: models.User) -> dict:
    updated_at = user.last_location_update
    if updated_at is not None and updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=datetime.timezone.utc)
    is_fresh = (
        updated_at is not None
        and datetime.datetime.now(datetime.timezone.utc) - updated_at
        <= LIVE_LOCATION_MAX_AGE
        and user.last_lat is not None
        and -90 <= user.last_lat <= 90
        and user.last_lng is not None
        and -180 <= user.last_lng <= 180
    )
    return {
        "last_lat": user.last_lat if is_fresh else None,
        "last_lng": user.last_lng if is_fresh else None,
        "last_location_update": updated_at if is_fresh else None,
    }


SETTING_BOUNDS = {
    "geofence_radius_meters": (50.0, 5000.0),
    "coverage_neglect_days": (1.0, 90.0),
    "max_shelf_photos_per_visit": (1.0, 20.0),
}


# ── FCM tokens ──────────────────────────────────────────────
@router.post("/users/me/fcm-tokens")
def register_fcm_token(
    payload: schemas.FCMTokenCreate,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
    request: Request = None,
):
    existing = db.query(models.FCMToken).filter(models.FCMToken.token == payload.token).first()
    if existing:
        existing.user_id = current_user.id
        existing.updated_at = datetime.datetime.now(datetime.timezone.utc)
    else:
        db.add(models.FCMToken(user_id=current_user.id, token=payload.token))
    db.commit()
    return {"message": get_text("success_create", request)}


@router.delete("/users/me/fcm-tokens")
def unregister_fcm_token(
    token: str,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
    request: Request = None,
):
    db.query(models.FCMToken).filter(
        models.FCMToken.token == token,
        models.FCMToken.user_id == current_user.id,
    ).delete()
    db.commit()
    return {"message": get_text("success_delete", request)}


# ── Profile & Location ───────────────────────────────────────
@router.get("/users/me", response_model=schemas.UserResponse)
def read_users_me(current_user: models.User = Depends(auth.get_current_user)):
    return current_user


@router.put("/users/me/location")
def update_my_location(
    payload: schemas.LocationUpdate,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    if current_user.role != "rep":
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
    current_user.last_lat = payload.lat
    current_user.last_lng = payload.lng
    current_user.last_location_update = datetime.datetime.now(datetime.timezone.utc)
    db.commit()
    return {"message": get_text("success_update", request)}


@router.delete("/users/me/location")
def stop_my_location_sharing(
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
):
    if current_user.role != "rep":
        raise HTTPException(status_code=403, detail="Only representatives can share live location")
    current_user.last_lat = None
    current_user.last_lng = None
    current_user.last_location_update = None
    db.commit()
    return {"message": "Location sharing stopped"}


@router.put("/users/me/password")
def update_password(
    data: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
    request: Request = None,
):
    new_password = data.get("password")
    if not new_password:
        raise HTTPException(status_code=400, detail=get_text("required_field", request))
    if len(new_password) < 8:
        raise HTTPException(status_code=422, detail=get_text("password_too_short", request))
    # SECURITY: require the current password for normal changes; the
    # first-login flow (temporary password) is exempt since the user just
    # authenticated with it.
    if not current_user.must_change_password:
        current_password = data.get("current_password")
        if not current_password:
            raise HTTPException(status_code=400, detail=get_text("current_password_required", request))
        if not auth.verify_password(current_password, current_user.hashed_password):
            raise HTTPException(status_code=403, detail=get_text("incorrect_password", request))
    current_user.hashed_password = auth.get_password_hash(new_password)
    current_user.must_change_password = False
    db.commit()
    return {"message": get_text("success_update", request)}


@router.patch("/users/me/profile", response_model=schemas.UserResponse)
def update_own_profile(
    data: schemas.ProfileUpdate,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    if data.full_name is not None:
        current_user.full_name = data.full_name
    if data.phone is not None:
        current_user.phone = data.phone
    if data.region is not None:
        current_user.region = data.region
    if data.profile_image_url is not None:
        current_user.profile_image_url = data.profile_image_url
    if data.has_completed_onboarding is True:
        current_user.has_completed_onboarding = True
    if data.current_password and data.new_password:
        if not auth.verify_password(data.current_password, current_user.hashed_password):
            raise HTTPException(status_code=403, detail=get_text("incorrect_password", request))
        current_user.hashed_password = auth.get_password_hash(data.new_password)
        current_user.must_change_password = False
    db.commit()
    db.refresh(current_user)
    return current_user

@router.post("/users/{user_id}/upload-photo", response_model=schemas.UserResponse)
async def upload_user_photo(
    user_id: str,
    file: UploadFile = File(...),
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    # Authorization: Only the user themselves or an admin can upload the photo
    if current_user.role != "admin" and current_user.id != user_id:
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))

    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail=get_text("invalid_file_type", request))

    # Use the centralized store_file_locally helper for stability and deduplication
    contents = await file.read()
    if len(contents) > MAX_SIZE:
        raise HTTPException(status_code=400, detail=get_text("file_too_large", request))

    result = await store_file_locally(
        file_bytes=contents,
        mime_type=file.content_type,
        subdir="profiles",
        old_url=None, # Pass current_user.profile_image_url if you want to delete the old file
        db=db
    )

    # Update user profile image URL
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail=get_text("user_not_found", request))

    user.profile_image_url = result["local_path"]
    user.avatar_file_id = result["stored_file"].id
    db.commit()
    db.refresh(user)

    return user

# ── User Signatures ──────────────────────────────────────────
from fastapi import UploadFile, File
import shutil
from routers.deps import store_file_locally

# ... (keep other imports)

@router.post("/users/me/signatures", response_model=schemas.UserSignatureResponse)
async def upload_signature(
    file: UploadFile = File(...),
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
):
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    file_id = str(uuid4())
    ext = file.filename.split('.')[-1] if '.' in file.filename else 'png'
    filename = f"{file_id}.{ext}"
    local_path = f"uploads/signatures/{filename}"
    
    with open(local_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    url = f"/uploads/signatures/{filename}"
    
    # Check if signature exists, if so delete old file
    sig = db.query(models.UserSignature).filter(models.UserSignature.user_id == current_user.id).first()
    if sig:
        old_path = sig.image_url.lstrip("/")
        if os.path.exists(old_path):
            os.remove(old_path)
        sig.image_url = url
    else:
        sig = models.UserSignature(
            user_id=current_user.id,
            image_url=url,
        )
        db.add(sig)
        
    db.commit()
    db.refresh(sig)
    return sig

@router.get("/users/me/signatures", response_model=schemas.UserSignatureResponse)
def get_signature(
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
):
    sig = db.query(models.UserSignature).filter(models.UserSignature.user_id == current_user.id).first()
    if not sig:
        raise HTTPException(status_code=404, detail="Signature not found")
    return sig

@router.delete("/users/me/signatures")
def delete_signature(
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
):
    sig = db.query(models.UserSignature).filter(models.UserSignature.user_id == current_user.id).first()
    if not sig:
        raise HTTPException(status_code=404, detail="Signature not found")
        
    old_path = sig.image_url.lstrip("/")
    if os.path.exists(old_path):
        os.remove(old_path)
        
    db.delete(sig)
    db.commit()
    return {"status": "ok"}

# ── User CRUD ────────────────────────────────────────────────
@router.post("/users", response_model=schemas.UserResponse)
def create_user(
    payload: schemas.UserCreate,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
    if db.query(models.User).filter(models.User.email == payload.email).first():
        raise HTTPException(status_code=400, detail=get_text("email_registered", request))

    brand_ids = list(dict.fromkeys(payload.brand_ids or ([payload.brand_id] if payload.brand_id else [])))
    region_ids = list(dict.fromkeys(payload.region_ids or ([payload.region_id] if payload.region_id else [])))
    if payload.supervisor_id and payload.role != "rep":
        raise HTTPException(status_code=422, detail="Only reps can be assigned to a supervisor.")
    regions = []
    if payload.role in ("supervisor", "rep"):
        if not brand_ids:
            raise HTTPException(status_code=422, detail=get_text("brand_id_required", request))
        brands = db.query(models.Brand).filter(
            models.Brand.id.in_(brand_ids), models.Brand.is_active == True
        ).all()
        if len(brands) != len(brand_ids):
            raise HTTPException(status_code=404, detail=get_text("brand_not_found", request))
        expected_regions = 1 if payload.role == "rep" else None
        if (expected_regions and len(region_ids) != expected_regions) or (
            payload.role == "supervisor" and not region_ids
        ):
            raise HTTPException(status_code=422, detail="Assign exactly one region to a rep and at least one to a supervisor.")
        regions = db.query(models.Region).filter(
            models.Region.id.in_(region_ids), models.Region.is_active == True
        ).all()
        if len(regions) != len(region_ids):
            raise HTTPException(status_code=422, detail="Select active, existing regions.")
        if payload.role == "rep" and payload.supervisor_id:
            sup = db.query(models.User).filter(models.User.id == payload.supervisor_id).first()
            sup_brands = set(sup.brand_ids) if sup else set()
            if sup and sup.brand_id:
                sup_brands.add(sup.brand_id)
            sup_regions = set(sup.region_ids) if sup else set()
            if not sup or sup.role != "supervisor" or not sup.is_active or not (
                sup_brands.intersection(brand_ids)
                and region_ids[0] in sup_regions
            ):
                raise HTTPException(status_code=422, detail=get_text("supervisor_brand_mismatch", request))
    elif payload.role == "general_manager":
        if region_ids:
            regions = db.query(models.Region).filter(
                models.Region.id.in_(region_ids), models.Region.is_active == True
            ).all()
            if len(regions) != len(region_ids):
                raise HTTPException(status_code=422, detail="Select active, existing regions.")
    elif brand_ids or region_ids:
        raise HTTPException(status_code=422, detail="Admin and management roles do not receive team brand or region assignments.")

    primary_region = next((region for region in regions if region.id in region_ids), None) if region_ids else None
    new_user = models.User(
        id=str(uuid4()),
        email=payload.email,
        full_name=payload.full_name,
        role=payload.role,
        phone=payload.phone,
        region=primary_region.name_en if primary_region else payload.region,
        region_id=region_ids[0] if payload.role == "rep" and region_ids else None,
        supervisor_id=payload.supervisor_id,
        brand_id=brand_ids[0] if brand_ids else None,
        hashed_password=auth.get_password_hash(payload.temporary_password),
        must_change_password=True,
        created_by=current_user.id,
    )
    db.add(new_user)
    db.flush()
    if payload.role in ("supervisor", "general_manager"):
        db.add_all(models.UserRegion(user_id=new_user.id, region_id=region_id) for region_id in region_ids)
    db.add_all(models.UserBrand(user_id=new_user.id, brand_id=brand_id) for brand_id in brand_ids)
    db.commit()
    db.refresh(new_user)
    return new_user


@router.get("/users", response_model=List[schemas.UserResponse])
def get_users(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set),
    brand_id: Optional[str] = None,
    role: Optional[str] = None,
    region_id: Optional[str] = None,
):
    scope = auth.get_scope(current_user, db, requested_region_id=region_id)
    query = db.query(models.User)
    if current_user.role == "supervisor":
        query = query.filter(
            models.User.supervisor_id == current_user.id,
            models.User.region_id.in_(scope.region_ids or []),
        )
    elif current_user.role in ("general_manager", "admin"):
        if scope.region_ids is not None:
            query = query.filter(or_(
                models.User.region_id.in_(scope.region_ids),
                models.User.user_regions.any(
                    models.UserRegion.region_id.in_(scope.region_ids)
                ),
            ))
        if brand_id:
            query = query.filter(or_(
                models.User.brand_id == brand_id,
                models.User.brands.any(models.Brand.id == brand_id),
            ))
    else:
        query = query.filter(models.User.id == current_user.id)
    if role:
        query = query.filter(models.User.role == role)
    return [
        schemas.UserResponse.model_validate(user).model_copy(
            update=_live_location_fields(user)
        )
        for user in query.all()
    ]


@router.get("/users/team-directory", response_model=List[schemas.TeamDirectoryResponse])
def get_team_directory(
    brand_id: Optional[str] = None,
    region_id: Optional[str] = None,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
    request: Request = None,
):
    if current_user.role not in ("admin", "general_manager"):
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
    scope = auth.get_scope(current_user, db, requested_region_id=region_id)
    query = db.query(models.User).filter(models.User.role == "supervisor")
    if brand_id:
        query = query.filter(models.User.brand_id == brand_id)
    if scope.region_ids is not None:
        scoped_supervisor_ids = db.query(models.User.supervisor_id).filter(
            models.User.role == "rep",
            models.User.region_id.in_(scope.region_ids),
            models.User.supervisor_id.isnot(None),
        )
        query = query.filter(or_(
            models.User.region_id.in_(scope.region_ids),
            models.User.user_regions.any(
                models.UserRegion.region_id.in_(scope.region_ids)
            ),
            models.User.id.in_(scoped_supervisor_ids),
        ))
    supervisors = query.all()
    result = []
    for sup in supervisors:
        reps_query = db.query(models.User).filter(
            models.User.role == "rep",
            models.User.supervisor_id == sup.id,
        )
        if scope.region_ids is not None:
            reps_query = reps_query.filter(
                models.User.region_id.in_(scope.region_ids)
            )
        reps = [
            schemas.UserResponse.model_validate(rep).model_copy(
                update=_live_location_fields(rep)
            )
            for rep in reps_query.all()
        ]
        result.append({"supervisor": sup, "reps": reps})
    return result


@router.get("/users/team/locations", response_model=List[schemas.UserResponse])
def get_team_locations(
    brand_id: Optional[str] = None,
    region_id: Optional[str] = None,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
):
    if current_user.role not in ("admin", "general_manager", "overseer", "supervisor"):
        raise HTTPException(status_code=403, detail="Not authorized to view team locations")
    from auth import get_scope
    scope = get_scope(current_user, db, requested_region_id=region_id)
    fresh_after = (
        datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        - LIVE_LOCATION_MAX_AGE
    )
    query = db.query(models.User).filter(
        models.User.role == "rep",
        models.User.is_active.is_(True),
        models.User.last_lat.isnot(None),
        models.User.last_lng.isnot(None),
        models.User.last_lat.between(-90, 90),
        models.User.last_lng.between(-180, 180),
        models.User.last_location_update >= fresh_after,
    )
    if scope.rep_ids is not None:
        query = query.filter(models.User.id.in_(scope.rep_ids))
    if scope.region_ids is not None:
        query = query.filter(models.User.region_id.in_(scope.region_ids))
    if scope.brand_ids is not None:
        query = query.filter(or_(
            models.User.brand_id.in_(scope.brand_ids),
            models.User.brands.any(models.Brand.id.in_(scope.brand_ids)),
        ))
    if brand_id:
        query = query.filter(or_(
            models.User.brand_id == brand_id,
            models.User.brands.any(models.Brand.id == brand_id),
        ))
    return query.all()


@router.get("/users/{user_id}/public-profile", response_model=schemas.PublicProfileResponse)
def get_public_profile(
    user_id: str,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
    request: Request = None,
):
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail=get_text("user_not_found", request))
    scope = auth.get_scope(current_user, db)
    if scope.role == "rep":
        if target.id != current_user.id:
            raise HTTPException(status_code=403, detail=get_text("not_authorized_view_user", request))
    elif scope.role == "supervisor":
        if target.id != current_user.id and (
            target.id not in (scope.rep_ids or [])
            or target.region_id not in (scope.region_ids or [])
        ):
            raise HTTPException(status_code=403, detail=get_text("not_authorized_view_user", request))
    target_brand_ids = set(target.brand_ids)
    if target.brand_id:
        target_brand_ids.add(target.brand_id)
    if scope.brand_ids is not None and not target_brand_ids.intersection(scope.brand_ids):
        raise HTTPException(status_code=403, detail=get_text("cross_brand_access_denied", request))

    completed_visit_query = db.query(models.Visit).filter(
        models.Visit.rep_id == user_id, models.Visit.status == "completed"
    )
    if scope.brand_ids is not None:
        completed_visit_query = completed_visit_query.outerjoin(
            models.Client, models.Visit.client_id == models.Client.id
        ).outerjoin(
            models.Center, models.Visit.center_id == models.Center.id
        ).filter(
            func.coalesce(
                models.Visit.brand_id,
                models.Client.brand_id,
                models.Center.brand_id,
            ).in_(scope.brand_ids)
        )
    completed_visits = completed_visit_query.count()

    targets_query = db.query(models.Target).join(
        models.Product, models.Target.product_id == models.Product.id
    ).filter(models.Target.rep_id == user_id)
    if scope.brand_ids is not None:
        targets_query = targets_query.filter(
            func.coalesce(
                models.Target.brand_id, models.Product.brand_id
            ).in_(scope.brand_ids)
        )
    targets = targets_query.all()
    target_total = sum(target.target_qty or 0 for target in targets)
    achieved_total = 0
    for t in targets:
        target_product = db.query(models.Product).filter(
            models.Product.id == t.product_id
        ).first()
        target_brand_id = t.brand_id or (target_product.brand_id if target_product else None)
        achieved_query = db.query(func.sum(models.VisitItem.qty_sold)).join(models.Visit).outerjoin(
            models.Client, models.Visit.client_id == models.Client.id
        ).outerjoin(
            models.Center, models.Visit.center_id == models.Center.id
        ).filter(
            models.VisitItem.product_id == t.product_id,
            models.Visit.rep_id == user_id,
            models.Visit.visit_date.between(t.period_start, t.period_end),
            models.Visit.status.notin_(["flagged", "rejected"]),
        )
        if scope.brand_ids is not None:
            achieved_query = achieved_query.filter(
                func.coalesce(
                    models.Visit.brand_id,
                    models.Client.brand_id,
                    models.Center.brand_id,
                ) == target_brand_id
            )
        achieved_total += achieved_query.scalar() or 0
    target_achievement_pct = round((achieved_total / target_total) * 100, 1) if target_total > 0 else 0.0

    team_count = db.query(models.User).filter(
        models.User.supervisor_id == user_id, models.User.role == "rep"
    ).count()

    brand_name = None
    if target.brand_id:
        brand = db.query(models.Brand).filter(models.Brand.id == target.brand_id).first()
        if brand:
            brand_name = brand.name

    return schemas.PublicProfileResponse(
        id=target.id,
        full_name=target.full_name,
        profile_image_url=target.profile_image_url,
        role=target.role,
        brand_id=target.brand_id,
        brand_name=brand_name,
        supervisor_id=target.supervisor_id,
        completed_visits=completed_visits,
        target_achievement_pct=target_achievement_pct,
        team_count=team_count,
    )


@router.get("/users/{user_id}", response_model=schemas.UserResponse)
def get_user_by_id(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set),
    request: Request = None,
):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail=get_text("user_not_found", request))
    # SECURITY: role-scoped access (was IDOR — any rep could read any user,
    # including live GPS coordinates)
    if current_user.role == "rep":
        if user.id != current_user.id:
            raise HTTPException(status_code=403, detail=get_text("not_authorized_view_user", request))
    elif current_user.role == "supervisor":
        scope = auth.get_scope(current_user, db)
        if user.id != current_user.id and (
            user.id not in (scope.rep_ids or [])
            or user.region_id not in (scope.region_ids or [])
        ):
            raise HTTPException(status_code=403, detail=get_text("not_authorized_view_user", request))
    elif current_user.role == "general_manager":
        scope = auth.get_scope(current_user, db)
        if scope.region_ids is not None and not set(user.region_ids).intersection(
            scope.region_ids
        ):
            raise HTTPException(status_code=403, detail=get_text("not_authorized_view_user", request))
    return schemas.UserResponse.model_validate(user).model_copy(
        update=_live_location_fields(user)
    )


@router.post("/users/{user_id}/request-deactivation")
def request_deactivation(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set),
    request: Request = None,
):
    if current_user.role != "general_manager":
        raise HTTPException(status_code=403, detail=get_text("gm_only_deactivation", request))
    target_user = db.query(models.User).filter(models.User.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail=get_text("user_not_found", request))
    for admin in db.query(models.User).filter(models.User.role == "admin").all():
        dispatch_notification_sync(
            db=db, user_id=admin.id, type="deactivation_request",
            title="Account Deactivation Request",
            message=f"GM {current_user.full_name} requested to deactivate {target_user.full_name} ({target_user.email}).",
            related_id=user_id,
        )
    return {"message": get_text("success_create", request)}


@router.put("/users/{user_id}")
def update_user(
    user_id: str,
    data: schemas.UserUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set),
    request: Request = None,
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail=get_text("user_not_found", request))
    updates = data.model_dump(exclude_unset=True)
    region_ids = updates.get("region_ids")
    if region_ids is not None:
        region_ids = list(dict.fromkeys(region_ids))
    elif "region_id" in updates:
        region_ids = [updates["region_id"]] if updates["region_id"] else []

    if region_ids is not None:
        if user.role == "rep" and len(region_ids) != 1:
            raise HTTPException(status_code=422, detail="A rep must be assigned to exactly one region.")
        if user.role == "supervisor" and not region_ids:
            raise HTTPException(status_code=422, detail="A supervisor must be assigned to at least one region.")
        if user.role not in ("rep", "supervisor") and region_ids:
            raise HTTPException(status_code=422, detail="This role cannot be assigned to a team region.")
        regions = db.query(models.Region).filter(
            models.Region.id.in_(region_ids), models.Region.is_active == True
        ).all() if region_ids else []
        if len(regions) != len(region_ids):
            raise HTTPException(status_code=422, detail="Select active, existing regions.")
        if user.role == "rep":
            user.region_id = region_ids[0]
            user.region = regions[0].name_en
        else:
            user.region_id = None
            db.query(models.UserRegion).filter(models.UserRegion.user_id == user.id).delete()
            db.add_all(models.UserRegion(user_id=user.id, region_id=region_id) for region_id in region_ids)

    if "brand_ids" in updates:
        brand_ids = list(dict.fromkeys(updates["brand_ids"] or []))
        if user.role in ("rep", "supervisor") and not brand_ids:
            raise HTTPException(status_code=422, detail="A rep or supervisor must have at least one brand.")
        if user.role not in ("rep", "supervisor") and brand_ids:
            raise HTTPException(status_code=422, detail="This role cannot be assigned to team brands.")
        brands = db.query(models.Brand).filter(
            models.Brand.id.in_(brand_ids), models.Brand.is_active == True
        ).all() if brand_ids else []
        if len(brands) != len(brand_ids):
            raise HTTPException(status_code=422, detail="Select active, existing brands.")
        db.query(models.UserBrand).filter(models.UserBrand.user_id == user.id).delete()
        db.add_all(models.UserBrand(user_id=user.id, brand_id=brand_id) for brand_id in brand_ids)
        user.brand_id = brand_ids[0] if brand_ids else None

    if user.role == "supervisor" and (
        region_ids is not None or "brand_ids" in updates
    ):
        supervisor_regions = set(region_ids if region_ids is not None else user.region_ids)
        supervisor_brands = set(
            updates["brand_ids"] if "brand_ids" in updates else user.brand_ids
        )
        if "brand_ids" not in updates and user.brand_id:
            supervisor_brands.add(user.brand_id)
        for rep in db.query(models.User).filter(
            models.User.supervisor_id == user.id,
            models.User.role == "rep",
        ).all():
            rep_brands = set(rep.brand_ids)
            if rep.brand_id:
                rep_brands.add(rep.brand_id)
            if rep.region_id not in supervisor_regions or not rep_brands.intersection(supervisor_brands):
                raise HTTPException(
                    status_code=422,
                    detail="Reassign the supervisor's reps before removing their region or brand access.",
                )

    if data.full_name is not None:
        if not data.full_name.strip():
            raise HTTPException(status_code=422, detail="Full name cannot be empty.")
        user.full_name = data.full_name.strip()
    if "phone" in updates:
        user.phone = data.phone
    if data.region is not None and region_ids is None: user.region = data.region
    if user.role == "rep" and (
        "supervisor_id" in updates or region_ids is not None or "brand_ids" in updates
    ):
        supervisor_id = updates.get("supervisor_id", user.supervisor_id)
        supervisor = db.query(models.User).filter(
            models.User.id == supervisor_id,
            models.User.role == "supervisor",
            models.User.is_active == True,
        ).first() if supervisor_id else None
        rep_region_id = user.region_id if region_ids is None else (region_ids[0] if region_ids else None)
        rep_brands = set(
            updates["brand_ids"] if "brand_ids" in updates else user.brand_ids
        )
        if "brand_ids" not in updates and user.brand_id:
            rep_brands.add(user.brand_id)
        if supervisor_id is None:
            user.supervisor_id = None
        elif not supervisor:
            raise HTTPException(status_code=422, detail="Select an active supervisor.")
        else:
            supervisor_regions = set(supervisor.region_ids)
            supervisor_brands = set(supervisor.brand_ids)
            if supervisor.brand_id:
                supervisor_brands.add(supervisor.brand_id)
            if rep_region_id not in supervisor_regions or not rep_brands.intersection(supervisor_brands):
                raise HTTPException(status_code=422, detail="Supervisor must share the rep's region and brand.")
            user.supervisor_id = supervisor.id
    if data.profile_image_url is not None: user.profile_image_url = data.profile_image_url
    if data.has_completed_onboarding is not None: user.has_completed_onboarding = data.has_completed_onboarding
    if data.is_active is not None:
        if user_id == current_user.id and not data.is_active:
            raise HTTPException(status_code=400, detail=get_text("cannot_deactivate_self", request))
        user.is_active = data.is_active
    db.commit()
    return {"message": get_text("success_update", request)}


def _unreferenced_profile_image_path(
    db: Session, user: models.User, image_url: Optional[str]
):
    if not image_url or not image_url.startswith("/uploads/profiles/"):
        return None
    if db.query(models.User.id).filter(
        models.User.id != user.id,
        models.User.profile_image_url == image_url,
    ).first():
        return None

    stored_file = db.query(models.StoredFile).filter(
        models.StoredFile.local_path == image_url
    ).first()
    if stored_file:
        for table in models.Base.metadata.tables.values():
            for column in table.columns:
                if any(
                    foreign_key.column.table is models.StoredFile.__table__
                    for foreign_key in column.foreign_keys
                ) and db.query(table).filter(column == stored_file.id).first():
                    return None
        db.delete(stored_file)

    root = Path("uploads/profiles").resolve()
    target = Path(image_url.lstrip("/")).resolve()
    if os.path.commonpath((str(root), str(target))) != str(root) or target == root:
        return None
    return target


@router.delete("/users/{user_id}")
def delete_user(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set),
    request: Request = None,
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account.")

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail=get_text("user_not_found", request))

    try:
        profile_image_url = user.profile_image_url
        if not profile_image_url and user.avatar_file_id:
            stored_avatar = db.query(models.StoredFile).filter(
                models.StoredFile.id == user.avatar_file_id
            ).first()
            profile_image_url = stored_avatar.local_path if stored_avatar else None

        db.query(models.FCMToken).filter(
            models.FCMToken.user_id == user_id
        ).delete(synchronize_session=False)
        db.query(models.Notification).filter(
            models.Notification.user_id == user_id
        ).delete(synchronize_session=False)
        db.query(models.UserSignature).filter(
            models.UserSignature.user_id == user_id
        ).delete(synchronize_session=False)
        db.query(models.UserBrand).filter(
            models.UserBrand.user_id == user_id
        ).delete(synchronize_session=False)
        db.query(models.UserRegion).filter(
            models.UserRegion.user_id == user_id
        ).delete(synchronize_session=False)
        db.query(models.Center).filter(
            models.Center.assigned_rep_id == user_id
        ).update(
            {models.Center.assigned_rep_id: None}, synchronize_session=False
        )
        db.query(models.ActivityLog).filter(
            models.ActivityLog.user_id == user_id
        ).update(
            {models.ActivityLog.user_name: "Deleted user"},
            synchronize_session=False,
        )

        user.email = f"deleted-{uuid4().hex}@redacted-account.net"
        user.hashed_password = auth.get_password_hash(uuid4().hex)
        user.full_name = "Deleted user"
        user.phone = None
        user.region = None
        user.region_id = None
        user.supervisor_id = None
        user.profile_image_url = None
        user.avatar_file_id = None
        user.is_active = False
        user.has_completed_onboarding = False
        user.must_change_password = True
        user.brand_id = None
        user.created_by = None
        user.last_lat = None
        user.last_lng = None
        user.last_location_update = None
        db.flush()
        profile_image_path = _unreferenced_profile_image_path(
            db, user, profile_image_url
        )
        db.add(models.ActivityLog(
            user_id=current_user.id,
            user_name=current_user.full_name,
            action="Erased personal data while retaining user records",
            log_type="user",
        ))
        db.commit()
    except Exception:
        db.rollback()
        raise

    if profile_image_path and profile_image_path.is_file():
        profile_image_path.unlink()
    return {"message": get_text("success_delete", request)}


# ── Settings ─────────────────────────────────────────────────
@router.get("/settings", response_model=List[schemas.SystemSettingResponse])
def get_settings(
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    return db.query(models.SystemSetting).all()


@router.put("/settings/{key}", response_model=schemas.SystemSettingResponse)
def update_setting(
    key: str,
    payload: schemas.SystemSettingUpdate,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
    request: Request = None,
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
    if key in SETTING_BOUNDS:
        low, high = SETTING_BOUNDS[key]
        try:
            float_val = float(payload.value)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"{key} must be a number")
        if not (low <= float_val <= high):
            raise HTTPException(status_code=422, detail=f"{key} must be between {low} and {high}")
    setting = db.query(models.SystemSetting).filter(models.SystemSetting.key == key).first()
    if not setting:
        setting = models.SystemSetting(key=key, value=str(payload.value))
        db.add(setting)
    else:
        setting.value = str(payload.value)
    db.commit()
    db.refresh(setting)
    return setting


# ── Regions & Reps Overview ─────────────────────────────────────────

@router.get("/regions", response_model=List[schemas.RegionResponse])
def get_regions(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """List all active regions."""
    scope = auth.get_scope(current_user, db)
    query = db.query(models.Region).filter(models.Region.is_active == True)
    if scope.region_ids is not None:
        query = query.filter(models.Region.id.in_(scope.region_ids))
    return query.order_by(models.Region.name_ar.asc()).all()


@router.get("/reps", response_model=List[schemas.RepItemResponse])
def get_reps(
    region_id: Optional[str] = None,
    brand_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set),
):
    """List delegates/reps scoped to the caller's role and requested region."""
    from auth import get_scope
    scope = get_scope(current_user, db, requested_region_id=region_id)

    query = db.query(models.User).filter(
        models.User.role == "rep",
        models.User.is_active == True,
    )

    if scope.role == "rep":
        query = query.filter(models.User.id == current_user.id)
    elif scope.role == "supervisor":
        if scope.rep_ids is not None:
            query = query.filter(models.User.id.in_(scope.rep_ids))
        if scope.region_ids is not None:
            query = query.filter(models.User.region_id.in_(scope.region_ids))
    else:  # general_manager, admin
        if scope.region_ids is not None:
            query = query.filter(models.User.region_id.in_(scope.region_ids))

    if scope.brand_ids is not None:
        brand_user_ids = db.query(models.UserBrand.user_id).filter(
            models.UserBrand.brand_id.in_(scope.brand_ids)
        )
        query = query.filter(
            or_(
                models.User.brand_id.in_(scope.brand_ids),
                models.User.id.in_(brand_user_ids),
            )
        )
    if brand_id:
        query = query.filter(or_(
            models.User.brand_id == brand_id,
            models.User.brands.any(models.Brand.id == brand_id),
        ))

    reps = query.order_by(models.User.full_name.asc()).all()
    results = []
    for r in reps:
        reg_name = r.region_rel.name_ar if r.region_rel else r.region
        results.append({
            "id": r.id,
            "full_name": r.full_name,
            "email": r.email,
            "phone": r.phone,
            "profile_image_url": r.profile_image_url,
            "region": r.region,
            "region_id": r.region_id,
            "region_name": reg_name,
            "is_active": r.is_active,
            "brand_id": r.brand_id,
            "supervisor_id": r.supervisor_id,
            **_live_location_fields(r),
        })
    return results


@router.get("/reps/{rep_id}/overview", response_model=schemas.RepOverviewResponse)
def get_rep_overview(
    rep_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set),
    request: Request = None,
):
    """Get complete overview of a representative (visits, visited regions, completed tasks, sales, activity log)."""
    rep = db.query(models.User).filter(
        models.User.id == rep_id,
        models.User.role == "rep",
        models.User.is_active == True,
    ).first()

    if not rep:
        raise HTTPException(status_code=404, detail=get_text("user_not_found", request) if request else "Rep not found")

    from auth import get_scope
    scope = get_scope(current_user, db)

    # Scoping checks
    if scope.role == "rep":
        if rep.id != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized to view other reps' data")
    elif scope.role == "supervisor":
        is_supervised = (rep.id in scope.rep_ids) if scope.rep_ids is not None else False
        is_in_region = rep.region_id in (scope.region_ids or [])
        if not (is_supervised and is_in_region):
            raise HTTPException(status_code=403, detail="Not authorized to view this rep's data")
    rep_brand_ids = set(rep.brand_ids)
    if rep.brand_id:
        rep_brand_ids.add(rep.brand_id)
    if scope.brand_ids is not None and not rep_brand_ids.intersection(scope.brand_ids):
        raise HTTPException(status_code=403, detail="Rep is outside your assigned brands")

    from sqlalchemy.orm import joinedload

    # 1. Rep Item Info
    rep_data = {
        "id": rep.id,
        "full_name": rep.full_name,
        "email": rep.email,
        "phone": rep.phone,
        "profile_image_url": rep.profile_image_url,
        "region": rep.region,
        "region_id": rep.region_id,
        "region_name": rep.region_rel.name_ar if rep.region_rel else rep.region,
        "is_active": rep.is_active,
        "brand_id": rep.brand_id,
        "supervisor_id": rep.supervisor_id,
        **_live_location_fields(rep),
    }

    # 2. Visits summary and recent visits
    def scoped_visits():
        query = db.query(models.Visit).filter(models.Visit.rep_id == rep_id)
        if scope.brand_ids is not None:
            query = query.outerjoin(
                models.Client, models.Visit.client_id == models.Client.id
            ).outerjoin(
                models.Center, models.Visit.center_id == models.Center.id
            ).filter(
                func.coalesce(
                    models.Visit.brand_id,
                    models.Client.brand_id,
                    models.Center.brand_id,
                ).in_(scope.brand_ids)
            )
        return query

    total_visits = scoped_visits().count()
    completed_visits = scoped_visits().filter(models.Visit.status == "completed").count()
    planned_visits = scoped_visits().filter(models.Visit.status == "planned").count()
    cancelled_visits = scoped_visits().filter(models.Visit.status == "cancelled").count()

    recent_visits_query = scoped_visits().options(
        joinedload(models.Visit.center),
        joinedload(models.Visit.client),
        joinedload(models.Visit.region_rel),
    ).order_by(models.Visit.visit_date.desc()).limit(30).all()

    recent_visits = []
    for v in recent_visits_query:
        target_name = (v.client.doctor_name or v.client.facility_name) if v.client else (v.center.name if v.center else None)
        recent_visits.append({
            "id": v.id,
            "center_name": v.center.name if v.center else None,
            "doctor_name": v.client.doctor_name if v.client else None,
            "facility_name": v.client.facility_name if v.client else None,
            "target_name": target_name,
            "status": v.status,
            "visit_date": v.visit_date.isoformat() if v.visit_date else None,
            "notes": v.notes,
            "latitude": v.latitude,
            "longitude": v.longitude,
        })

    visits_data = {
        "total": total_visits,
        "completed": completed_visits,
        "planned": planned_visits,
        "cancelled": cancelled_visits,
        "recent": recent_visits,
    }

    # 3. Visited Regions (derived purely from completed visits and check-in locations)
    completed_visits_for_regions = scoped_visits().options(
        joinedload(models.Visit.center),
        joinedload(models.Visit.client),
        joinedload(models.Visit.region_rel),
    ).filter(
        models.Visit.status == "completed",
    ).order_by(models.Visit.visit_date.desc()).all()

    region_map = {}
    for v in completed_visits_for_regions:
        reg_id = (
            v.region_id
            or (v.client.region_id if v.client else None)
            or (v.center.region_id if v.center else None)
            or rep.region_id
        )
        reg_name = (
            (v.region_rel.name_ar if v.region_rel else None)
            or (v.client.region if v.client else None)
            or (v.center.region if v.center else None)
            or rep.region
            or "Unknown"
        )
        key = reg_id or reg_name
        if key not in region_map:
            region_map[key] = {
                "region_id": reg_id,
                "region_name": reg_name,
                "visits_count": 0,
                "locations": []
            }
        region_map[key]["visits_count"] += 1

        lat = v.latitude or v.save_location_lat or (v.client.latitude if v.client else None) or (v.center.latitude if v.center else None)
        lng = v.longitude or v.save_location_lng or (v.client.longitude if v.client else None) or (v.center.longitude if v.center else None)
        target = (v.client.doctor_name or v.client.facility_name) if v.client else (v.center.name if v.center else "Visit")
        if lat is not None and lng is not None:
            region_map[key]["locations"].append({
                "visit_id": v.id,
                "target_name": target,
                "lat": lat,
                "lng": lng,
                "visit_date": v.visit_date.isoformat() if v.visit_date else None,
            })

    visited_regions = list(region_map.values())

    # 4. Completed Tasks
    tasks_query = db.query(models.Task).options(
        joinedload(models.Task.brand),
    ).filter(
        models.Task.rep_id == rep_id,
        models.Task.status == 'completed',
        models.Task.is_deleted == False,
    )
    if scope.brand_ids is not None:
        tasks_query = tasks_query.filter(models.Task.brand_id.in_(scope.brand_ids))
    tasks_query = tasks_query.order_by(
        models.Task.completed_at.desc(), models.Task.created_at.desc()
    ).limit(50).all()

    completed_tasks_list = []
    for t in tasks_query:
        completed_tasks_list.append({
            "id": t.id,
            "title": t.purpose or t.target_name or get_text("task_completed", request),
            "target_name": t.target_name,
            "target_type": t.target_type,
            "brand_name": t.brand.name if t.brand else None,
            "priority": t.priority,
            "due_date": t.due_date.isoformat() if t.due_date else None,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
            "progress_note": t.progress_note,
        })

    completed_tasks_data = {
        "total": len(completed_tasks_list),
        "items": completed_tasks_list,
    }

    # 5. Sales / Revenue
    items = db.query(models.VisitItem).join(models.Visit).join(
        models.Product, models.VisitItem.product_id == models.Product.id
    ).options(
        joinedload(models.VisitItem.product),
    ).filter(
        models.Visit.rep_id == rep_id,
        models.Visit.status == 'completed',
    )
    if scope.brand_ids is not None:
        items = items.filter(models.Product.brand_id.in_(scope.brand_ids))
    items = items.all()

    total_revenue = 0.0
    total_units_sold = 0
    total_free_units = 0
    product_sales = {}
    for item in items:
        p_name = item.product.name if item.product else "Unknown Product"
        pid = item.product_id
        line_rev = float(item.qty_sold) * float(item.price_at_sale or 0.0)
        total_revenue += line_rev
        total_units_sold += item.qty_sold
        total_free_units += item.qty_free
        if pid not in product_sales:
            product_sales[pid] = {
                "product_id": pid,
                "product_name": p_name,
                "qty_sold": 0,
                "qty_free": 0,
                "revenue": 0.0,
            }
        product_sales[pid]["qty_sold"] += item.qty_sold
        product_sales[pid]["qty_free"] += item.qty_free
        product_sales[pid]["revenue"] += line_rev

    sales_data = {
        "total_revenue": total_revenue,
        "total_units_sold": total_units_sold,
        "total_free_units": total_free_units,
        "by_product": list(product_sales.values()),
    }

    # 6. Recent Activity / Brand Activity logs
    activity_logs = db.query(models.BrandActivityLog).options(
        joinedload(models.BrandActivityLog.brand),
    ).filter(
        models.BrandActivityLog.rep_id == rep_id,
    )
    if scope.brand_ids is not None:
        activity_logs = activity_logs.filter(
            models.BrandActivityLog.brand_id.in_(scope.brand_ids)
        )
    activity_logs = activity_logs.order_by(
        models.BrandActivityLog.logged_at.desc()
    ).limit(30).all()

    recent_activity = []
    for l in activity_logs:
        recent_activity.append({
            "id": l.id,
            "activity_type": l.activity_type,
            "target_name": l.target_name,
            "target_type": l.target_type,
            "brand_name": l.brand.name if l.brand else None,
            "notes": l.notes,
            "status": l.status,
            "logged_at": l.logged_at.isoformat() if l.logged_at else None,
        })

    return {
        "rep": rep_data,
        "visits": visits_data,
        "visited_regions": visited_regions,
        "completed_tasks": completed_tasks_data,
        "sales": sales_data,
        "recent_activity": recent_activity,
    }
