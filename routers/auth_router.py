"""routers/auth_router.py — /health, /auth/login, /auth/invite"""
import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from routers.deps import get_text

import models, schemas, auth
from database import get_db
from routers.deps import limiter

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/auth/login", response_model=schemas.Token)
@limiter.limit("10/minute")
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
    request: Request = None,
):
    user = db.query(models.User).filter(models.User.email == form_data.username).first()
    if not user or not auth.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=get_text("incorrect_credentials", request),
            headers={"WWW-Authenticate": "Bearer"},
        )
    expires = datetime.timedelta(minutes=auth.ACCESS_TOKEN_EXPIRE_MINUTES)
    token = auth.create_access_token(data={"sub": user.id, "role": user.role}, expires_delta=expires)
    return {"access_token": token, "token_type": "bearer", "user": user}


@router.post("/auth/invite", response_model=schemas.UserResponse)
def invite_user(
    user: schemas.UserCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set),
    request: Request = None,
):
    from routers.users_router import create_user
    return create_user(user, current_user, db, request)
