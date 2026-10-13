import os
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from dotenv import load_dotenv
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from database import get_db
import models
import schemas

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
if (
    not SECRET_KEY
    or len(SECRET_KEY.encode("utf-8")) < 32
    or len(set(SECRET_KEY)) < 16
    or SECRET_KEY.lower() in {
        "change-me-in-production",
        "change-this-to-a-secure-random-key",
    }
):
    raise RuntimeError("SECRET_KEY must be a securely generated value of at least 32 bytes.")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 # 7 days

import bcrypt

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

def verify_password(plain_password, hashed_password):
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def get_password_hash(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
        token_data = schemas.TokenData(id=user_id)
    except JWTError:
        raise credentials_exception
        
    user = db.query(models.User).filter(models.User.id == token_data.id).first()
    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Inactive user account"
        )
    return user

def require_password_set(current_user: models.User = Depends(get_current_user)):
    if current_user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Must change password first"
        )
    return current_user

class UserScope:
    def __init__(
        self,
        rep_ids: Optional[List[str]],
        region_ids: Optional[List[str]],
        role: str,
        user_id: str,
        brand_id: Optional[str] = None,
        brand_ids: Optional[List[str]] = None
    ):
        self.rep_ids = rep_ids          # None = all reps (admin / GM)
        self.region_ids = region_ids    # None = all regions (admin / GM without filter)
        self.role = role
        self.user_id = user_id
        self.brand_id = brand_id
        self.brand_ids = brand_ids     # None = all brands (admin / GM / overseer)

    @property
    def is_unrestricted_reps(self) -> bool:
        return self.rep_ids is None

    @property
    def is_unrestricted_regions(self) -> bool:
        return self.region_ids is None

def get_scope(
    current_user: models.User,
    db: Session,
    requested_region_id: Optional[str] = None
) -> UserScope:
    role = current_user.role
    user_id = current_user.id
    brand_id = current_user.brand_id
    assigned_brand_ids = list(dict.fromkeys([
        *current_user.brand_ids,
        *([brand_id] if brand_id else []),
    ]))

    if role == "rep":
        rep_region_id = current_user.region_id
        if not rep_region_id and current_user.region:
            r_obj = db.query(models.Region).filter(
                (models.Region.name_ar == current_user.region) |
                (models.Region.name_en.ilike(current_user.region))
            ).first()
            if r_obj:
                rep_region_id = r_obj.id

        scoped_regions = [rep_region_id] if rep_region_id else []
        if requested_region_id and requested_region_id not in scoped_regions:
            scoped_regions = []

        return UserScope(
            rep_ids=[user_id],
            region_ids=scoped_regions,
            role=role,
            user_id=user_id,
            brand_id=brand_id,
            brand_ids=assigned_brand_ids,
        )

    elif role == "supervisor":
        reps = db.query(models.User).filter(models.User.supervisor_id == user_id).all()
        rep_ids = [r.id for r in reps]
        rep_ids.append(user_id)

        assigned_user_regions = db.query(models.UserRegion).filter(models.UserRegion.user_id == user_id).all()
        supervisor_region_ids = [ur.region_id for ur in assigned_user_regions]
        if current_user.region_id and current_user.region_id not in supervisor_region_ids:
            supervisor_region_ids.append(current_user.region_id)

        if not supervisor_region_ids:
            for r in reps:
                if r.region_id and r.region_id not in supervisor_region_ids:
                    supervisor_region_ids.append(r.region_id)

        if requested_region_id:
            if requested_region_id not in supervisor_region_ids:
                scoped_regions = []
            else:
                scoped_regions = [requested_region_id]
        else:
            scoped_regions = supervisor_region_ids

        return UserScope(
            rep_ids=rep_ids,
            region_ids=scoped_regions,
            role=role,
            user_id=user_id,
            brand_id=brand_id,
            brand_ids=assigned_brand_ids,
        )

    elif role == "general_manager":
        assigned_regions = list(dict.fromkeys(current_user.region_ids))
        if assigned_regions:
            scoped_regions = (
                [requested_region_id]
                if requested_region_id in assigned_regions
                else ([] if requested_region_id else assigned_regions)
            )
        else:
            scoped_regions = [requested_region_id] if requested_region_id else None
        return UserScope(
            rep_ids=None,
            region_ids=scoped_regions,
            role=role,
            user_id=user_id,
            brand_id=brand_id,
            brand_ids=None,
        )

    elif role in ("admin", "overseer"):
        scoped_regions = [requested_region_id] if requested_region_id else None
        return UserScope(
            rep_ids=None,
            region_ids=scoped_regions,
            role=role,
            user_id=user_id,
            brand_id=brand_id,
            brand_ids=None,
        )

    return UserScope(
        rep_ids=[user_id],
        region_ids=[current_user.region_id] if current_user.region_id else [],
        role=role,
        user_id=user_id,
        brand_id=brand_id,
        brand_ids=assigned_brand_ids,
    )

def get_role_scoped_rep_ids(current_user: models.User, db: Session) -> Optional[List[str]]:
    scope = get_scope(current_user, db)
    if scope.role == "supervisor":
        return [
            rep.id for rep in db.query(models.User).filter(
                models.User.id.in_(scope.rep_ids or []),
                models.User.role == "rep",
                models.User.region_id.in_(scope.region_ids or []),
            ).all()
        ]
    return scope.rep_ids
