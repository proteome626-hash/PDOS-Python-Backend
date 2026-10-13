from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func
from typing import List, Optional
import datetime
from datetime import timedelta
from routers.deps import get_text

from database import get_db
import models
import schemas
from auth import get_current_user, get_scope

router = APIRouter(prefix="/tasks", tags=["Tasks & Brand Activity Logs"])


# ─── Helpers ────────────────────────────────────────────────────────────────

def _get_brand_color(brand) -> str:
    """Return the brand's own color, or fallback to a computed one."""
    if brand and hasattr(brand, 'color') and brand.color:
        return brand.color
    if brand and brand.name:
        return "#9C27B0" if "cebelia" in brand.name.lower() else "#4CAF50"
    return "#757575"


def _enrich_task(task) -> dict:
    """Convert a Task ORM object to a dict enriched with names/colors."""
    d = {c.name: getattr(task, c.name) for c in task.__table__.columns}
    # Using a placeholder for "Unknown" since enrichment doesn't have Request context.
    # These are often handled by the frontend or we could pass request down.
    d["rep_name"] = task.rep.full_name if task.rep else get_text("unknown", None)
    d["brand_name"] = task.brand.name if task.brand else get_text("unknown", None)
    d["brand_color"] = _get_brand_color(task.brand)
    d["supervisor_name"] = task.supervisor.full_name if task.supervisor else None
    return d


def _get_scoped_query(db: Session, current_user: models.User, region_id: Optional[str] = None):
    """Return a Task query scoped by the user's role and region."""
    from auth import get_scope
    scope = get_scope(current_user, db, requested_region_id=region_id)

    query = db.query(models.Task).options(
        joinedload(models.Task.rep),
        joinedload(models.Task.brand),
        joinedload(models.Task.supervisor),
    ).filter(models.Task.is_deleted == False)

    if scope.role == "rep":
        query = query.filter(models.Task.rep_id == current_user.id)
    elif scope.role == "supervisor":
        query = query.filter(
            models.Task.rep_id.in_(scope.rep_ids or []),
            models.Task.region_id.in_(scope.region_ids or []),
        )
    else:
        if scope.region_ids is not None:
            query = query.filter(models.Task.region_id.in_(scope.region_ids))

    if scope.brand_ids is not None:
        query = query.filter(models.Task.brand_id.in_(scope.brand_ids))

    return query


def _log_history(db: Session, task_id: str, action: str, changed_by: str,
                 old_status: str = None, new_status: str = None, note: str = None):
    """Write an audit record to task_history."""
    entry = models.TaskHistory(
        task_id=task_id,
        action=action,
        old_status=old_status,
        new_status=new_status,
        changed_by=changed_by,
        note=note,
    )
    db.add(entry)


def _assert_can_access_task(task: models.Task, current_user: models.User, db: Session):
    scope = get_scope(current_user, db)
    if scope.brand_ids is not None and task.brand_id not in scope.brand_ids:
        raise HTTPException(403, "Not authorized to access this task")
    if current_user.role == "general_manager":
        if scope.region_ids is not None and task.region_id not in scope.region_ids:
            raise HTTPException(403, "Not authorized to access this task")
        return
    if current_user.role in ("admin", "overseer"):
        return
    if current_user.role == "rep" and task.rep_id == current_user.id:
        return
    if current_user.role == "supervisor":
        if task.rep_id in (scope.rep_ids or []) and task.region_id in (scope.region_ids or []):
            return
    raise HTTPException(403, "Not authorized to access this task")


# ─── CRUD Endpoints ────────────────────────────────────────────────────────

@router.post("/", response_model=schemas.TaskResponse)
def create_task(
    task: schemas.TaskCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
    request: Request = None,
):
    """Create a new task. Supervisor/GM only."""
    if current_user.role not in ("supervisor", "general_manager", "admin"):
        raise HTTPException(403, get_text("not_authorized_tasks", request))

    scope = get_scope(current_user, db)

    # Verify target rep
    target_rep = db.query(models.User).filter(
        models.User.id == task.rep_id,
        models.User.is_active == True,
    ).first()
    if not target_rep:
        raise HTTPException(404, "Target rep not found or inactive")

    # Supervision and region check for supervisors
    if current_user.role == "supervisor":
        if scope.rep_ids is not None and target_rep.id not in scope.rep_ids and target_rep.supervisor_id != current_user.id:
            raise HTTPException(403, "Target rep is not in supervisor's team")
        if target_rep.region_id not in (scope.region_ids or []):
            raise HTTPException(403, "Target rep is not in supervisor's assigned regions")
    elif (
        current_user.role == "general_manager"
        and scope.region_ids is not None
        and target_rep.region_id not in scope.region_ids
    ):
        raise HTTPException(403, "Target rep is outside manager's assigned regions")

    db_task = models.Task(**task.model_dump())
    if not db_task.supervisor_id:
        db_task.supervisor_id = current_user.id
    target_brand_ids = set(target_rep.brand_ids)
    if target_rep.brand_id:
        target_brand_ids.add(target_rep.brand_id)
    if scope.brand_ids is not None:
        target_brand_ids.intersection_update(scope.brand_ids)
    if not db_task.brand_id:
        if current_user.brand_id in target_brand_ids:
            db_task.brand_id = current_user.brand_id
        elif len(target_brand_ids) == 1:
            db_task.brand_id = next(iter(target_brand_ids))
        else:
            raise HTTPException(422, "A brand is required for this task")
    if db_task.brand_id not in target_brand_ids:
        raise HTTPException(403, "Task brand is outside the rep's assigned brands")
    if not db.query(models.Brand).filter(
        models.Brand.id == db_task.brand_id,
        models.Brand.is_active == True,
    ).first():
        raise HTTPException(422, "Task brand is inactive or unknown")
    db_task.region_id = target_rep.region_id
    if db_task.status == 'new':
        db_task.status = 'pending'
    db.add(db_task)
    db.flush()

    _log_history(db, db_task.id, "created", current_user.id,
                 new_status=db_task.status,
                 note=get_text("log_task_assigned", request) if request else "Task assigned to rep")

    db.commit()

    # Reload with relationships
    db_task = db.query(models.Task).options(
        joinedload(models.Task.rep),
        joinedload(models.Task.brand),
        joinedload(models.Task.supervisor),
    ).populate_existing().get(db_task.id)

    return _enrich_task(db_task)


@router.get("/", response_model=List[schemas.TaskResponse])
def get_tasks(
    rep_id: Optional[str] = None,
    brand_id: Optional[str] = None,
    status: Optional[str] = None,
    region_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get tasks scoped by role. Supports filtering by rep, brand, status, region."""
    query = _get_scoped_query(db, current_user, region_id=region_id)

    if rep_id:
        query = query.filter(models.Task.rep_id == rep_id)
    if brand_id:
        query = query.filter(models.Task.brand_id == brand_id)
    if status:
        query = query.filter(models.Task.status == status)

    tasks = query.order_by(models.Task.created_at.desc()).all()
    return [_enrich_task(t) for t in tasks]


@router.patch("/{task_id}", response_model=schemas.TaskResponse)
def update_task(
    task_id: str,
    task_update: schemas.TaskUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
    request: Request = None,
):
    """Update a task's status, notes, or priority."""
    db_task = db.query(models.Task).filter(
        models.Task.id == task_id,
        models.Task.is_deleted == False
    ).first()

    if not db_task:
        raise HTTPException(404, get_text("task_not_found", request))

    _assert_can_access_task(db_task, current_user, db)

    old_status = db_task.status
    if old_status == 'new':
        old_status = 'pending'
    elif old_status == 'done':
        old_status = 'completed'
    elif old_status == 'accepted':
        old_status = 'accepted_scheduled'

    if task_update.status is not None:
        new_status = task_update.status
        if new_status == 'done':
            new_status = 'completed'
            task_update.status = 'completed'
        elif new_status == 'accepted':
            new_status = 'accepted_scheduled'
            task_update.status = 'accepted_scheduled'

        if new_status != old_status:
            # Valid state transitions
            valid_transitions = {
                'pending': ['accepted_scheduled', 'rejected'],
                'accepted_scheduled': ['in_progress', 'upcoming', 'overdue', 'completed'],
                'scheduled': ['in_progress', 'upcoming', 'overdue', 'completed'],
                'upcoming': ['in_progress', 'overdue', 'completed'],
                'in_progress': ['completed', 'abandoned'],
                'abandoned': ['in_progress'],
                'overdue': ['in_progress', 'abandoned', 'completed'],
                'rejected': [],
                'completed': [],
            }

            if old_status in valid_transitions and new_status not in valid_transitions[old_status]:
                raise HTTPException(422, get_text("invalid_task_transition", request).format(old=old_status, new=new_status))

        if new_status == 'rejected' and not task_update.rejection_report:
            raise HTTPException(422, get_text("rejection_report_required", request))
            
        if new_status == 'in_progress':
            db_task.visit_started_at = datetime.datetime.now(datetime.timezone.utc)

        if new_status == "completed":
            if old_status not in ('in_progress', 'completed'):
                raise HTTPException(422, "Task must be in_progress before it can be completed")
                    
            db_task.visit_completed_at = task_update.visit_completed_at or db_task.visit_completed_at or datetime.datetime.now(datetime.timezone.utc)

            if not db_task.completed_at:
                db_task.completed_at = datetime.datetime.now(datetime.timezone.utc)
                if db_task.task_type != 'visit':
                    db.add(models.BrandActivityLog(
                        rep_id=db_task.rep_id,
                        brand_id=db_task.brand_id,
                        task_id=db_task.id,
                        activity_type='task_completed',
                        target_type=db_task.target_type,
                        target_id=db_task.target_id,
                        target_name=db_task.target_name,
                        notes=task_update.progress_note or db_task.progress_note
                    ))
        else:
            db_task.completed_at = None
            
        if new_status == "accepted_scheduled":
            db_task.accepted_at = datetime.datetime.now(datetime.timezone.utc)

        db_task.status = task_update.status
        _log_history(db, task_id, "status_changed", current_user.id,
                     old_status=old_status, new_status=task_update.status)

    if task_update.progress_note is not None and task_update.progress_note != db_task.progress_note:
        db_task.progress_note = task_update.progress_note
        _log_history(db, task_id, "updated", current_user.id,
                     note=get_text("log_progress_updated", request) if request else "Progress note updated")

    if task_update.priority is not None and task_update.priority != db_task.priority:
        db_task.priority = task_update.priority
        _log_history(db, task_id, "updated", current_user.id,
                     note=get_text("log_priority_changed", request, priority=task_update.priority) if request else f"Priority changed to {task_update.priority}")

    if task_update.notes is not None:
        db_task.notes = task_update.notes
        
    if task_update.rejection_report is not None:
        db_task.rejection_report = task_update.rejection_report
    if task_update.reminder_offset is not None:
        db_task.reminder_offset = task_update.reminder_offset
    if task_update.visit_id is not None:
        db_task.visit_id = task_update.visit_id
    if task_update.accepted_at is not None:
        db_task.accepted_at = task_update.accepted_at

    db.commit()
    
    # Reload with relationships explicitly to avoid lazy-load issues
    db_task = db.query(models.Task).options(
        joinedload(models.Task.rep),
        joinedload(models.Task.brand),
        joinedload(models.Task.supervisor),
    ).populate_existing().get(task_id)

    return _enrich_task(db_task)


@router.delete("/{task_id}")
def delete_task(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
    request: Request = None,
):
    """Soft-delete a task. Supervisor/GM/Admin only."""
    task = db.query(models.Task).filter(
        models.Task.id == task_id,
        models.Task.is_deleted == False,
    ).first()
    if not task:
        raise HTTPException(404, get_text("task_not_found", request))

    if current_user.role not in ("supervisor", "general_manager", "admin"):
        raise HTTPException(403, get_text("not_authorized_tasks", request))
    _assert_can_access_task(task, current_user, db)

    task.is_deleted = True
    _log_history(db, task_id, "deleted", current_user.id,
                 old_status=task.status, note=get_text("log_task_deleted", request) if request else "Task deleted")
    db.commit()
    return {"ok": True}


# ─── Task History ───────────────────────────────────────────────────────────

@router.get("/{task_id}/history", response_model=List[schemas.TaskHistoryResponse])
def get_task_history(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get the change history of a specific task."""
    task = db.query(models.Task).filter(
        models.Task.id == task_id,
        models.Task.is_deleted == False
    ).first()
    if not task:
        raise HTTPException(404, "Task not found")

    _assert_can_access_task(task, current_user, db)

    entries = db.query(models.TaskHistory).options(
        joinedload(models.TaskHistory.user),
    ).filter(
        models.TaskHistory.task_id == task_id,
    ).order_by(models.TaskHistory.created_at.asc()).all()

    result = []
    for e in entries:
        d = {c.name: getattr(e, c.name) for c in e.__table__.columns}
        d["changed_by_name"] = e.user.full_name if e.user else None
        result.append(d)
    return result


# ─── Search Targets (Autocomplete) ─────────────────────────────────────────

@router.get("/search-targets", response_model=List[schemas.SearchTargetResponse])
def search_targets(
    q: str = Query(..., min_length=1, description="Search query"),
    type: Optional[str] = Query(None, description="Filter by type: doctor, pharmacy, institution, center"),
    region_id: Optional[str] = Query(None, description="Filter by region ID"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Autocomplete search across clients (doctors/pharmacies) and centers."""
    from auth import get_scope
    scope = get_scope(current_user, db, requested_region_id=region_id)

    results = []
    search = f"%{q}%"

    if type is None or type in ("doctor", "pharmacy", "institution"):
        # Search clients
        client_query = db.query(models.Client).filter(
            or_(
                models.Client.doctor_name.ilike(search),
                models.Client.facility_name.ilike(search),
            )
        )
        if type:
            client_query = client_query.filter(models.Client.client_type == type)

        # Regional scoping
        if scope.region_ids is not None:
            client_query = client_query.filter(models.Client.region_id.in_(scope.region_ids))
        if scope.brand_ids is not None:
            client_query = client_query.filter(models.Client.brand_id.in_(scope.brand_ids))
        if scope.role == "supervisor":
            client_query = client_query.filter(
                models.Client.rep_id.in_(scope.rep_ids or [])
            )

        for c in client_query.limit(10).all():
            name = c.doctor_name or c.facility_name or get_text("unknown", None)
            results.append({
                "id": c.id,
                "name": name,
                "type": c.client_type or get_text("doctor", None),
                "region": c.region,
                "region_id": c.region_id,
            })

    if type is None or type == "center":
        # Search centers
        center_query = db.query(models.Center).filter(
            models.Center.name.ilike(search),
            models.Center.is_active == True,
        )
        if scope.region_ids is not None:
            center_query = center_query.filter(models.Center.region_id.in_(scope.region_ids))
        if scope.brand_ids is not None:
            center_query = center_query.filter(models.Center.brand_id.in_(scope.brand_ids))

        for c in center_query.limit(10).all():
            results.append({
                "id": c.id,
                "name": c.name,
                "type": "center",
                "region": c.region,
                "region_id": c.region_id,
            })

    return results[:10]


# ─── Brand Activity Logs ───────────────────────────────────────────────────

@router.post("/logs", response_model=schemas.BrandActivityLogResponse)
def create_activity_log(
    log: schemas.BrandActivityLogCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
    request: Request = None,
):
    scope = get_scope(current_user, db)
    if scope.brand_ids is not None and log.brand_id not in scope.brand_ids:
        raise HTTPException(403, "Activity brand is outside your assigned scope")
    if log.rep_id and log.rep_id != current_user.id:
        if current_user.role != "supervisor":
            raise HTTPException(403, "Not authorized to log activity for this rep")
        if log.rep_id not in (scope.rep_ids or []):
            raise HTTPException(403, "Rep is outside your assigned team")
        target_rep = db.query(models.User).filter(models.User.id == log.rep_id).first()
        if not target_rep or target_rep.region_id not in (scope.region_ids or []):
            raise HTTPException(403, "Rep is outside your assigned regions")
    else:
        target_rep = current_user
    target_brand_ids = set(target_rep.brand_ids)
    if target_rep.brand_id:
        target_brand_ids.add(target_rep.brand_id)
    if log.brand_id not in target_brand_ids:
        raise HTTPException(403, "Activity brand is outside the rep's assigned brands")
    db_log = models.BrandActivityLog(**log.model_dump())
    if not db_log.rep_id:
        db_log.rep_id = current_user.id
    db.add(db_log)

    # Auto-complete linked task
    if db_log.task_id:
        task = db.query(models.Task).filter(models.Task.id == db_log.task_id).first()
        if not task:
            raise HTTPException(404, "Task not found")
        _assert_can_access_task(task, current_user, db)
        if task.rep_id != db_log.rep_id or task.brand_id != db_log.brand_id:
            raise HTTPException(403, "Activity task does not belong to the selected rep")
        if task.status != 'done':
            old = task.status
            task.status = 'done'
            task.completed_at = datetime.datetime.now(datetime.timezone.utc)
            _log_history(db, task.id, "status_changed", current_user.id,
                         old_status=old, new_status="done",
                         note=get_text("log_auto_completed", request) if request else "Auto-completed via activity log")

    db.commit()
    db.refresh(db_log)

    d = {c.name: getattr(db_log, c.name) for c in db_log.__table__.columns}
    rep = db.query(models.User).get(db_log.rep_id) if db_log.rep_id else None
    brand = db.query(models.Brand).get(db_log.brand_id) if db_log.brand_id else None
    d["rep_name"] = rep.full_name if rep else None
    d["brand_name"] = brand.name if brand else None
    return d


@router.get("/logs", response_model=List[schemas.BrandActivityLogResponse])
def get_activity_logs(
    brand_id: Optional[str] = None,
    rep_id: Optional[str] = None,
    date_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    scope = get_scope(current_user, db)
    query = db.query(models.BrandActivityLog).options(
        joinedload(models.BrandActivityLog.rep),
        joinedload(models.BrandActivityLog.brand),
    )

    # Role scoping
    if current_user.role == "rep":
        query = query.filter(models.BrandActivityLog.rep_id == current_user.id)
    elif current_user.role == "supervisor":
        query = query.join(
            models.User, models.BrandActivityLog.rep_id == models.User.id
        ).filter(
            models.BrandActivityLog.rep_id.in_(scope.rep_ids or []),
            models.User.region_id.in_(scope.region_ids or []),
        )

    if scope.brand_ids is not None:
        query = query.filter(models.BrandActivityLog.brand_id.in_(scope.brand_ids))

    if brand_id:
        query = query.filter(models.BrandActivityLog.brand_id == brand_id)
    if rep_id:
        query = query.filter(models.BrandActivityLog.rep_id == rep_id)

    if date_filter:
        now = datetime.datetime.now(datetime.timezone.utc)
        if date_filter == 'today':
            query = query.filter(models.BrandActivityLog.logged_at >= now.date())
        elif date_filter == 'week':
            query = query.filter(models.BrandActivityLog.logged_at >= now - timedelta(days=7))
        elif date_filter == 'month':
            query = query.filter(models.BrandActivityLog.logged_at >= now - timedelta(days=30))

    logs = query.order_by(models.BrandActivityLog.logged_at.desc()).all()
    result = []
    for l in logs:
        d = {c.name: getattr(l, c.name) for c in l.__table__.columns}
        d["rep_name"] = l.rep.full_name if l.rep else None
        d["brand_name"] = l.brand.name if l.brand else None
        result.append(d)
    return result
