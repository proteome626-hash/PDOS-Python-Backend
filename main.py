"""
main.py — PDOS Python Backend entry point.
All business logic lives in routers/. This file handles:
  - App creation & middleware
  - Startup / shutdown lifecycle
  - Static file serving (/uploads)
  - Root endpoint
"""
import asyncio
import json
import logging
import mimetypes
import os
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy.orm import Session
from routers.deps import get_text
from contextlib import asynccontextmanager

import models
import auth
import redis_manager
from database import engine, get_db


# ── Router imports ───────────────────────────────────────────
from routers.auth_router import router as auth_router
from routers.users_router import router as users_router
from routers.products_router import router as products_router
from routers.centers_router import router as centers_router
from routers.visits_router import router as visits_router
from routers.notifications_router import router as notifications_router
from routers.misc_router import router as misc_router
from routers.chat_router import router as chat_router
from routers.tasks_router import router as tasks_router
import analytics
import reports

# ── Redis notification channel (shared with deps) ────────────
from routers.deps import NOTIFICATION_CHANNEL, manager, limiter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── App ───────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    _init_firebase()
    pubsub = await redis_manager.subscribe(NOTIFICATION_CHANNEL, None)
    redis_task = None
    if pubsub:
        redis_task = asyncio.create_task(_listen_redis(pubsub))
    for d in UPLOAD_DIRS.values():
        os.makedirs(d, exist_ok=True)

    yield

    # Shutdown
    if redis_task:
        redis_task.cancel()
    await redis_manager.close()

app = FastAPI(title="PDOS Python Backend", lifespan=lifespan)

# Rate limiter — single shared instance from routers.deps
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS
# SECURITY: "*" مع allow_credentials=True تركيبة غير صالحة وخطيرة؛
# نستخدم credentials فقط عند تحديد origins صريحة (CORS_ORIGINS=a,b,c).
cors_origins_str = os.environ.get("CORS_ORIGINS", "")
if not cors_origins_str or cors_origins_str.strip() == "*":
    cors_origins = ["*"]
    cors_allow_credentials = False
else:
    cors_origins = [o.strip() for o in cors_origins_str.split(",") if o.strip()]
    cors_allow_credentials = True
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Error logging middleware — لا نسرّب تفاصيل الاستثناء الداخلية للعميل
@app.middleware("http")
async def log_errors(request, call_next):
    try:
        return await call_next(request)
    except Exception:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": get_text("internal_server_error", request)})

# Create DB tables
from database import ensure_schema_and_regions
models.Base.metadata.create_all(bind=engine)
ensure_schema_and_regions(engine)


# ── Routers ───────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(products_router)
app.include_router(centers_router)
app.include_router(visits_router)
app.include_router(notifications_router)
app.include_router(misc_router)
app.include_router(analytics.router)
app.include_router(reports.router)
app.include_router(chat_router)
app.include_router(tasks_router)

from fastapi.staticfiles import StaticFiles

# Product images are catalog content; all visit evidence stays behind authorization.
for public_upload_dir in ("uploads/products", "uploads/profiles"):
    os.makedirs(public_upload_dir, exist_ok=True)
    app.mount(
        f"/{public_upload_dir}",
        StaticFiles(directory=public_upload_dir),
        name=public_upload_dir.replace("/", "_"),
    )


@app.get("/uploads/{file_path:path}", include_in_schema=False)
def download_private_upload(
    file_path: str,
    current_user: models.User = Depends(auth.require_password_set),
    db: Session = Depends(get_db),
):
    upload_root = Path("uploads").resolve()
    try:
        target = (upload_root / file_path).resolve()
        if os.path.commonpath((str(upload_root), str(target))) != str(upload_root):
            raise ValueError("path escapes upload directory")
    except ValueError:
        raise HTTPException(status_code=404, detail="File not found")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    url = f"/uploads/{Path(file_path).as_posix()}"
    directory = Path(file_path).parts[0] if Path(file_path).parts else ""
    scope = auth.get_scope(current_user, db)

    def can_access_rep(rep_id: str) -> bool:
        if scope.rep_ids is not None and rep_id not in scope.rep_ids:
            return False
        rep = db.query(models.User).filter(models.User.id == rep_id).first()
        if not rep:
            return False
        if scope.region_ids is not None and rep.region_id not in scope.region_ids:
            return False
        if scope.brand_ids is not None and not (
            rep.brand_id in scope.brand_ids
            or any(brand_id in scope.brand_ids for brand_id in rep.brand_ids)
        ):
            return False
        return True

    permitted = False
    if directory == "receipts":
        expense = db.query(models.Expense).filter(
            models.Expense.receipt_image_url == url
        ).first()
        permitted = bool(expense and can_access_rep(expense.rep_id))
    elif directory == "signatures":
        signature = db.query(models.UserSignature).filter(
            models.UserSignature.image_url == url
        ).first()
        permitted = bool(signature and signature.user_id == current_user.id)
        visits = db.query(models.Visit).filter(
            models.Visit.signature_url == url
        ).all()
        permitted = permitted or any(can_access_rep(v.rep_id) for v in visits)
    elif directory == "visits":
        photo_rep_ids = db.query(models.Visit.rep_id).join(
            models.VisitPhoto, models.VisitPhoto.visit_id == models.Visit.id
        ).filter(models.VisitPhoto.photo_url == url).all()
        reports_for_file = db.query(models.FieldReport).filter(
            models.FieldReport.photo_url == url
        ).all()
        permitted = any(can_access_rep(rep_id) for (rep_id,) in photo_rep_ids) or any(
            can_access_rep(report.rep_id) for report in reports_for_file
        )

    if not permitted:
        raise HTTPException(status_code=404, detail="File not found")

    stored = db.query(models.StoredFile).filter(
        models.StoredFile.local_path == url
    ).first()
    media_type = stored.mime_type if stored else mimetypes.guess_type(target.name)[0]
    return FileResponse(
        target,
        media_type=media_type or "application/octet-stream",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


# ── Lifecycle ─────────────────────────────────────────────────
UPLOAD_DIRS = {
    "products": "uploads/products",
    "signatures": "uploads/signatures",
    "receipts": "uploads/receipts",
    "visits": "uploads/visits",
}


def _init_firebase():
    """Initialize Firebase Admin SDK for FCM push notifications.
    Credentials come from FCM_SERVICE_ACCOUNT_JSON (raw JSON) or
    FIREBASE_CREDENTIALS_PATH (service-account file). Push degrades
    gracefully — a missing package or credentials never blocks startup."""
    try:
        import firebase_admin
        from firebase_admin import credentials
    except ImportError:
        logger.warning("firebase-admin not installed - push notifications disabled")
        return

    # Public API: get_app() raises ValueError when not yet initialized.
    try:
        firebase_admin.get_app()
        return  # already initialized
    except ValueError:
        pass
    sa_json = os.environ.get("FCM_SERVICE_ACCOUNT_JSON", "")
    cred_path = os.environ.get("FIREBASE_CREDENTIALS_PATH", "")
    try:
        if sa_json:
            firebase_admin.initialize_app(credentials.Certificate(json.loads(sa_json)))
            logger.info("Firebase Admin SDK initialized from FCM_SERVICE_ACCOUNT_JSON")
        elif cred_path and os.path.exists(cred_path):
            firebase_admin.initialize_app(credentials.Certificate(cred_path))
            logger.info("Firebase Admin SDK initialized from %s", cred_path)
        else:
            logger.warning("Firebase credentials not configured - push notifications disabled")
    except Exception:
        logger.exception("Failed to initialize Firebase Admin SDK - push notifications disabled")


# ── Root Endpoint ──────────────────────────────────────────────
@app.get("/")
def read_root():
    return {"message": "PDOS Python Backend is running"}



async def _listen_redis(pubsub):
    try:
        while True:
            try:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=30)
                if msg and msg["type"] == "message":
                    data = json.loads(msg["data"])
                    uid = data.get("user_id")
                    if uid:
                        await manager.send_notification(json.dumps(data), uid)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Redis notification relay error")
    except asyncio.CancelledError:
        pass


# ── Root Endpoint ──────────────────────────────────────────────
@app.get("/")
def read_root():
    return {"message": "PDOS Python Backend is running"}
