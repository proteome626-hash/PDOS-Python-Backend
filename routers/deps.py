"""
routers/deps.py — Shared dependencies and helper utilities used across routers.
"""
from __future__ import annotations
import asyncio
import json
import logging
import math
from typing import Dict, List, Optional
from uuid import uuid4
import hashlib
import os

from fastapi import WebSocket, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
from slowapi import Limiter
from slowapi.util import get_remote_address

import models
import auth
from database import get_db  # noqa: re-exported for convenience
import redis_manager

logger = logging.getLogger(__name__)

# Single shared rate limiter — main.py attaches it to app.state.limiter and
# routers import it from here so limits are tracked consistently.
limiter = Limiter(key_func=get_remote_address)

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_SIZE = 5 * 1024 * 1024  # 5 MB
NOTIFICATION_CHANNEL = "notifications"


# ──────────────────────────────────────────────
# Translation helper
# ──────────────────────────────────────────────
class Translator:
    def __init__(self):
        self.locales = {}

    def load_locales(self):
        path = "locales/en.json"
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                self.locales["en"] = json.load(f)

    def translate(self, key: str, request: Optional[Request] = None) -> str:
        return self.locales.get("en", {}).get(key, key)

translator = Translator()
translator.load_locales()

def get_text(key: str, request: Optional[Request] = None) -> str:
    return translator.translate(key, request)


# ──────────────────────────────────────────────
# WebSocket connection manager (singleton)
# ──────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        self.active_connections.setdefault(user_id, []).append(websocket)

    def disconnect(self, websocket: WebSocket, user_id: str):
        if user_id in self.active_connections:
            self.active_connections[user_id].remove(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]

    async def send_notification(self, message: str, user_id: str):
        for conn in self.active_connections.get(user_id, []):
            await conn.send_text(message)

    async def broadcast(self, message: str):
        for connections in self.active_connections.values():
            for conn in connections:
                await conn.send_text(message)


manager = ConnectionManager()


# ──────────────────────────────────────────────
# Role & Scope helpers
# ──────────────────────────────────────────────
from auth import get_scope, get_role_scoped_rep_ids, UserScope



# ──────────────────────────────────────────────
# Geo helpers
# ──────────────────────────────────────────────
def haversine_distance(lat1, lon1, lat2, lon2) -> float:
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_setting(db: Session, key: str, default):
    s = db.query(models.SystemSetting).filter(models.SystemSetting.key == key).first()
    if s:
        try:
            return float(s.value)
        except ValueError:
            return default
    return default


# ──────────────────────────────────────────────
# Visit enrichment
# ──────────────────────────────────────────────
def enrich_visits(visits, db: Session):
    cache: Dict[str, Optional[tuple[str, str]]] = {}
    for v in visits:
        if v.rep_id not in cache:
            rep = db.query(models.User).filter(models.User.id == v.rep_id).first()
            cache[v.rep_id] = (rep.full_name, rep.role) if rep else None
        rep_info = cache[v.rep_id]
        v.rep_name = rep_info[0] if rep_info else None
        v.rep_role = rep_info[1] if rep_info else None
    return visits


# ──────────────────────────────────────────────
# Activity log
# ──────────────────────────────────────────────
def create_activity_log(
    db: Session, user_id: str, user_name: str, action_key: str,
    log_type: str = "general", related_id: str = None,
    request: Request = None,
    **kwargs
):
    # Ensure user_name is not None to avoid IntegrityError (NOT NULL constraint)
    safe_user_name = user_name or "Unknown User"

    # Translate the action text using the provided key and dynamic arguments
    action_text = get_text(action_key, request) if request else action_key
    if kwargs:
        try:
            action_text = action_text.format(**kwargs)
        except KeyError:
            pass

    db.add(models.ActivityLog(
        user_id=user_id, user_name=safe_user_name,
        action=action_text, log_type=log_type,
        related_id=related_id,
    ))
    db.commit()

def generate_next_reference_code(db: Session, prefix: str) -> str:
    """Atomically generates the next sequence number for a global reference code."""
    # Ensure row exists
    counter = db.query(models.SystemCounter).filter(models.SystemCounter.key == 'global_reference').first()
    if not counter:
        try:
            db.execute(text("INSERT INTO system_counters (key, value) VALUES ('global_reference', 1000)"))
            db.commit()
        except Exception:
            db.rollback() # Someone else inserted it first

    # Atomic increment and return using RETURNING clause
    stmt = text("UPDATE system_counters SET value = value + 1 WHERE key = 'global_reference' RETURNING value")
    new_val = db.execute(stmt).scalar()
    db.commit()
    return f"{prefix}-{new_val}"


# ──────────────────────────────────────────────
# Notification dispatcher
# ──────────────────────────────────────────────
def dispatch_notification_sync(
    db: Session, user_id: str, type: str, title: str,
    message: str = None, related_id: str = None
):
    new_notif = models.Notification(
        user_id=user_id, type=type, title=title,
        message=message, related_id=related_id
    )
    db.add(new_notif)
    db.commit()
    db.refresh(new_notif)

    payload = {
        "id": new_notif.id, "user_id": user_id, "type": type,
        "title": title, "message": message, "related_id": related_id,
        "is_read": False, "created_at": new_notif.created_at.isoformat()
    }

    # IMPORTANT: All DB operations MUST happen before launching background tasks
    # to avoid SQLite threading errors.
    fcm_tokens = []
    try:
        import firebase_admin
        try:
            firebase_admin.get_app()
        except ValueError:
            logger.debug("FCM not initialized - skipping push notification")
        else:
            from firebase_admin import messaging
            tokens_objs = db.query(models.FCMToken).filter(models.FCMToken.user_id == user_id).all()
            fcm_tokens = [t.token for t in tokens_objs]
    except Exception as e:
        logger.error("FCM setup error: %s", e)

    try:
        loop = asyncio.get_running_loop()
        # WebSocket and Redis notifications
        loop.create_task(manager.send_notification(json.dumps(payload), user_id))
        loop.create_task(redis_manager.publish(NOTIFICATION_CHANNEL, payload))

        # FCM push notification (as a background task to avoid blocking)
        if fcm_tokens:
            async def send_fcm():
                try:
                    from firebase_admin import messaging
                    msg_fcm = messaging.MulticastMessage(
                        notification=messaging.Notification(title=title, body=message),
                        data={"related_id": str(related_id or ""), "type": type},
                        tokens=fcm_tokens,
                    )
                    response = messaging.send_each_for_multicast(msg_fcm)
                    # Note: Clean-up of UNREGISTERED tokens here would require a new DB session
                    # since the request session might be closed.
                except Exception as e:
                    logger.error("FCM send error: %s", e)

            loop.create_task(send_fcm())
    except RuntimeError:
        pass


# ──────────────────────────────────────────────
# File storage helper
# ──────────────────────────────────────────────
async def store_file_locally(file_bytes: bytes, mime_type: str, subdir: str, old_url: Optional[str], db: Session) -> dict:
    sha256 = hashlib.sha256(file_bytes).hexdigest()
    existing = db.query(models.StoredFile).filter(models.StoredFile.sha256 == sha256).first()
    if existing:
        return {"stored_file": existing, "local_path": existing.local_path}

    ext = mime_type.split("/")[-1].replace("jpeg", "jpg")
    filename = f"{uuid4().hex}.{ext}"

    # Store path relative to the app root for StaticFiles mount
    local_path = f"/uploads/{subdir}/{filename}"

    # Actual path on disk
    disk_path = os.path.join("uploads", subdir, filename)

    os.makedirs(os.path.dirname(disk_path), exist_ok=True)
    with open(disk_path, "wb") as f:
        f.write(file_bytes)

    sf = models.StoredFile(
        local_path=local_path,
        original_name=filename, mime_type=mime_type,
        size=len(file_bytes), sha256=sha256,
    )
    db.add(sf)
    db.flush()

    if old_url:
        old_fn = old_url.lstrip("/")
        if os.path.exists(old_fn):
            try:
                os.remove(old_fn)
            except OSError:
                pass

    return {"stored_file": sf, "local_path": local_path}
