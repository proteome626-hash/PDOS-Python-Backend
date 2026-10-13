"""routers/notifications_router.py — /notifications/*, /ws/notifications/*"""
from typing import List
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status, Request
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from routers.deps import get_text

import models, schemas, auth
from database import SessionLocal, get_db
from routers.deps import manager

router = APIRouter()


@router.get("/notifications", response_model=List[schemas.NotificationResponse])
def get_notifications(
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set),
):
    return (
        db.query(models.Notification)
        .filter(models.Notification.user_id == current_user.id)
        .order_by(models.Notification.created_at.desc())
        .limit(limit)
        .all()
    )


@router.put("/notifications/{notification_id}/read", response_model=schemas.NotificationResponse)
def mark_notification_read(
    notification_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set),
    request: Request = None,
):
    notif = db.query(models.Notification).filter(
        models.Notification.id == notification_id,
        models.Notification.user_id == current_user.id,
    ).first()
    if not notif:
        raise HTTPException(status_code=404, detail=get_text("notification_not_found", request))
    notif.is_read = True
    db.commit()
    db.refresh(notif)
    return notif


@router.put("/notifications/users/{user_id}/read-all")
def mark_all_notifications_read(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set),
    request: Request = None,
):
    if current_user.id != user_id and current_user.role not in ("admin", "overseer"):
        raise HTTPException(status_code=403, detail=get_text("not_authorized", request))
    db.query(models.Notification).filter(
        models.Notification.user_id == user_id,
        models.Notification.is_read == False,
    ).update({"is_read": True})
    db.commit()
    return {"message": get_text("success_update", request)}


@router.delete("/notifications/{notification_id}")
def delete_notification(
    notification_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set),
    request: Request = None,
):
    notif = db.query(models.Notification).filter(
        models.Notification.id == notification_id,
        models.Notification.user_id == current_user.id,
    ).first()
    if not notif:
        raise HTTPException(status_code=404, detail=get_text("notification_not_found", request))
    db.delete(notif)
    db.commit()
    return {"message": get_text("success_delete", request)}


@router.delete("/notifications")
def delete_all_notifications(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set),
    request: Request = None,
):
    deleted = db.query(models.Notification).filter(models.Notification.user_id == current_user.id).delete()
    db.commit()
    return {"message": get_text("success_delete", request)}


@router.websocket("/ws/notifications/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    auth_header = websocket.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    token = auth_header.split(" ")[1]
    try:
        payload = jwt.decode(token, auth.SECRET_KEY, algorithms=[auth.ALGORITHM])
        token_user_id = payload.get("sub")
    except JWTError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    if token_user_id != user_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    db = SessionLocal()
    try:
        user = db.query(models.User).filter(models.User.id == token_user_id).first()
        if not user or not user.is_active or user.must_change_password:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
    finally:
        db.close()
    await manager.connect(websocket, user_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_id)
