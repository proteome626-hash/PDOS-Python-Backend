import io
import os
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, extract, cast, Date
from typing import List, Optional
import datetime

from fpdf import FPDF

import models
import schemas
import auth
from database import get_db

router = APIRouter(prefix="/analytics", tags=["Analytics"])

def _region_scope(scope, requested_region_id: Optional[str] = None) -> Optional[List[str]]:
    if scope.role == "rep" and not scope.region_ids and requested_region_id is None:
        return None
    return scope.region_ids


def calculate_revenue(db: Session, start_date: datetime.date, end_date: datetime.date, rep_ids: Optional[List[str]] = None, brand_id: Optional[str] = None, region_ids: Optional[List[str]] = None) -> float:
    query = db.query(func.sum(models.VisitItem.qty_sold * models.VisitItem.price_at_sale)).join(models.Visit).filter(
        func.date(models.Visit.visit_date) >= start_date,
        func.date(models.Visit.visit_date) <= end_date,
        models.Visit.status.notin_(['flagged', 'rejected'])
    )
    if brand_id:
        query = query.join(models.User, models.Visit.rep_id == models.User.id)\
                     .join(models.Product, models.VisitItem.product_id == models.Product.id)\
                     .filter(models.User.brand_id == brand_id, models.Product.brand_id == brand_id)
    if rep_ids is not None:
        query = query.filter(models.Visit.rep_id.in_(rep_ids))
    if region_ids is not None:
        query = query.filter(models.Visit.region_id.in_(region_ids))
    return float(query.scalar() or 0.0)

def get_base_visit_query(db: Session, current_user: models.User, start_date: datetime.date, end_date: datetime.date, effective_rep_id: Optional[str] = None, brand_id: Optional[str] = None, region_id: Optional[str] = None):
    query = db.query(models.Visit).filter(
        func.date(models.Visit.visit_date) >= start_date,
        func.date(models.Visit.visit_date) <= end_date,
        models.Visit.status.notin_(['flagged', 'rejected'])
    )
    if brand_id:
        query = query.join(models.User, models.Visit.rep_id == models.User.id)\
                     .filter(models.User.brand_id == brand_id)
    scope = auth.get_scope(current_user, db, requested_region_id=region_id)
    region_ids = _region_scope(scope, region_id)
    if scope.rep_ids is not None:
        query = query.filter(models.Visit.rep_id.in_([effective_rep_id] if effective_rep_id else scope.rep_ids))
    elif effective_rep_id:
        query = query.filter(models.Visit.rep_id == effective_rep_id)
    if region_ids is not None:
        query = query.filter(models.Visit.region_id.in_(region_ids))
    return query

def get_base_visit_item_query(db: Session, current_user: models.User, start_date: datetime.date, end_date: datetime.date, effective_rep_id: Optional[str] = None, brand_id: Optional[str] = None, region_id: Optional[str] = None):
    query = db.query(models.VisitItem)\
              .join(models.Visit)\
              .join(models.Product, models.VisitItem.product_id == models.Product.id)\
              .join(models.User, models.Visit.rep_id == models.User.id)\
              .filter(
                  func.date(models.Visit.visit_date) >= start_date,
                  func.date(models.Visit.visit_date) <= end_date,
                  models.Visit.status.notin_(['flagged', 'rejected'])
              )
    if brand_id:
        query = query.filter(models.User.brand_id == brand_id, models.Product.brand_id == brand_id)
    scope = auth.get_scope(current_user, db, requested_region_id=region_id)
    region_ids = _region_scope(scope, region_id)
    if scope.rep_ids is not None:
        query = query.filter(models.Visit.rep_id.in_([effective_rep_id] if effective_rep_id else scope.rep_ids))
    elif effective_rep_id:
        query = query.filter(models.Visit.rep_id == effective_rep_id)
    if region_ids is not None:
        query = query.filter(models.Visit.region_id.in_(region_ids))
    return query

@router.get("", response_model=schemas.AnalyticsResponse)
@router.get("/", response_model=schemas.AnalyticsResponse, include_in_schema=False)
def get_analytics(
    start_date: datetime.date,
    end_date: datetime.date,
    rep_id: Optional[str] = None,
    brand_id: Optional[str] = None,
    region_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set)
):
    scope = auth.get_scope(current_user, db, requested_region_id=region_id)
    if rep_id and scope.rep_ids is not None and rep_id not in scope.rep_ids:
        raise HTTPException(status_code=403, detail="Not authorized for this rep")
    effective_rep_id = current_user.id if scope.role == "rep" else rep_id

    # 1. Total Visits & Completed Visits
    visit_q = get_base_visit_query(db, current_user, start_date, end_date, effective_rep_id, brand_id, region_id)
    total_visits = visit_q.count()
    visits_completed = visit_q.filter(models.Visit.status == 'completed').count()

    # 2. Total Revenue
    region_ids = _region_scope(scope, region_id)
    rep_ids = [effective_rep_id] if effective_rep_id else scope.rep_ids
        
    total_revenue = calculate_revenue(db, start_date, end_date, rep_ids, brand_id, region_ids)
    
    item_q = get_base_visit_item_query(db, current_user, start_date, end_date, effective_rep_id, brand_id, region_id)

    # 3. Top Products
    products_results = item_q.with_entities(
        models.Product.id,
        models.Product.name,
        func.sum(models.VisitItem.qty_sold).label('units_sold'),
        func.sum(models.VisitItem.qty_sold * models.VisitItem.price_at_sale).label('total_revenue')
    ).group_by(models.Product.id).order_by(func.sum(models.VisitItem.qty_sold * models.VisitItem.price_at_sale).desc()).limit(10).all()

    top_products = []
    for rank, row in enumerate(products_results, 1):
        top_products.append(schemas.ProductPerformanceResponse(
            product_id=row[0],
            product_name=row[1],
            units_sold=row[2] or 0,
            total_revenue=float(row[3] or 0.0),
            rank=rank
        ))

    # 4. Top Reps
    reps_results = item_q.with_entities(
        models.User.id,
        models.User.full_name,
        func.count(func.distinct(models.Visit.id)).label('total_visits'),
        func.sum(models.VisitItem.qty_sold * models.VisitItem.price_at_sale).label('total_revenue')
    ).group_by(models.User.id).order_by(func.sum(models.VisitItem.qty_sold * models.VisitItem.price_at_sale).desc()).limit(10).all()

    top_reps = []
    for rank, row in enumerate(reps_results, 1):
        top_reps.append(schemas.RepPerformanceResponse(
            rep_id=row[0],
            rep_name=row[1],
            total_visits=row[2] or 0,
            total_revenue=float(row[3] or 0.0),
            rank=rank
        ))
    
    # 5. Target Completion and Avg Visits
    unique_reps = visit_q.with_entities(func.count(func.distinct(models.Visit.rep_id))).scalar() or 1
    avg_visits = total_visits / unique_reps if unique_reps > 0 else 0.0

    target_q = db.query(models.Target).filter(
        models.Target.period_start <= end_date,
        models.Target.period_end >= start_date
    )
    if brand_id:
        target_q = target_q.join(models.Product, models.Target.product_id == models.Product.id)\
                           .outerjoin(models.User, models.Target.rep_id == models.User.id)\
                           .filter(models.Product.brand_id == brand_id)\
                           .filter((models.Target.rep_id == None) | (models.User.brand_id == brand_id))
    
    if effective_rep_id:
        target_q = target_q.filter(models.Target.rep_id == effective_rep_id)
    elif scope.rep_ids is not None:
        target_q = target_q.filter(models.Target.rep_id.in_(scope.rep_ids))
    if region_ids is not None:
        target_q = target_q.filter(
            models.Target.rep_id.in_(
                db.query(models.User.id).filter(models.User.region_id.in_(region_ids))
            )
        )
    
    total_target_qty = target_q.with_entities(func.sum(models.Target.target_qty)).scalar() or 0
    
    achieved_q = db.query(func.sum(models.VisitItem.qty_sold)).join(models.Visit).filter(
        func.date(models.Visit.visit_date) >= start_date,
        func.date(models.Visit.visit_date) <= end_date,
        models.Visit.status.notin_(['flagged', 'rejected'])
    )
    if brand_id:
        achieved_q = achieved_q.join(models.User, models.Visit.rep_id == models.User.id)\
                               .join(models.Product, models.VisitItem.product_id == models.Product.id)\
                               .filter(models.User.brand_id == brand_id, models.Product.brand_id == brand_id)

    if effective_rep_id:
        achieved_q = achieved_q.filter(models.Visit.rep_id == effective_rep_id)
    elif scope.rep_ids is not None:
        achieved_q = achieved_q.filter(models.Visit.rep_id.in_(scope.rep_ids))
    if region_ids is not None:
        achieved_q = achieved_q.filter(models.Visit.region_id.in_(region_ids))
        
    total_achieved_qty = achieved_q.scalar() or 0
    target_completion = (total_achieved_qty / total_target_qty) * 100 if total_target_qty > 0 else 100.0

    return schemas.AnalyticsResponse(
        total_revenue=total_revenue,
        total_visits=total_visits,
        visits_completed=visits_completed,
        avg_visits_per_rep=avg_visits,
        target_completion_percent=target_completion,
        top_products=top_products,
        top_reps=top_reps
    )

@router.get("/daily-revenue", response_model=List[schemas.DailyRevenueResponse])
def get_daily_revenue(
    start_date: datetime.date,
    end_date: datetime.date,
    rep_id: Optional[str] = None,
    region_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set)
):
    scope = auth.get_scope(current_user, db, requested_region_id=region_id)
    region_ids = _region_scope(scope, region_id)
    if rep_id and scope.rep_ids is not None and rep_id not in scope.rep_ids:
        raise HTTPException(status_code=403, detail="Not authorized for this rep")

    # NOTE: func.strftime is SQLite-only; use portable expressions so the
    # same code runs on PostgreSQL (docker-compose deployment) too.
    query = db.query(
        func.date(models.Visit.visit_date).label('day'),
        func.sum(models.VisitItem.qty_sold * models.VisitItem.price_at_sale).label('revenue')
    ).join(models.VisitItem).filter(
        func.date(models.Visit.visit_date) >= start_date,
        func.date(models.Visit.visit_date) <= end_date,
        models.Visit.status.notin_(['flagged', 'rejected'])
    )

    if rep_id:
        query = query.filter(models.Visit.rep_id == rep_id)
    elif scope.rep_ids is not None:
        query = query.filter(models.Visit.rep_id.in_(scope.rep_ids))
    if region_ids is not None:
        query = query.filter(models.Visit.region_id.in_(region_ids))

    query = query.group_by(func.date(models.Visit.visit_date)).order_by(func.date(models.Visit.visit_date))

    results = []
    for row in query.all():
        results.append(schemas.DailyRevenueResponse(
            date=str(row.day),
            revenue=float(row.revenue or 0.0)
        ))
    return results

@router.get("/monthly-revenue", response_model=List[schemas.MonthlyRevenueResponse])
def get_monthly_revenue(
    year: int,
    rep_id: Optional[str] = None,
    region_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set)
):
    scope = auth.get_scope(current_user, db, requested_region_id=region_id)
    region_ids = _region_scope(scope, region_id)
    if rep_id and scope.rep_ids is not None and rep_id not in scope.rep_ids:
        raise HTTPException(status_code=403, detail="Not authorized for this rep")

    # NOTE: portable across SQLite and PostgreSQL (extract() is compiled to
    # strftime by SQLAlchemy's SQLite dialect, natively supported on PG).
    yr = extract('year', models.Visit.visit_date)
    mo = extract('month', models.Visit.visit_date)
    query = db.query(
        yr.label('yr'),
        mo.label('mo'),
        func.sum(models.VisitItem.qty_sold * models.VisitItem.price_at_sale).label('revenue')
    ).join(models.VisitItem).filter(
        extract('year', models.Visit.visit_date) == year,
        models.Visit.status.notin_(['flagged', 'rejected'])
    )

    if rep_id:
        query = query.filter(models.Visit.rep_id == rep_id)
    elif scope.rep_ids is not None:
        query = query.filter(models.Visit.rep_id.in_(scope.rep_ids))
    if region_ids is not None:
        query = query.filter(models.Visit.region_id.in_(region_ids))

    query = query.group_by(yr, mo).order_by(yr, mo)

    results = []
    for row in query.all():
        results.append(schemas.MonthlyRevenueResponse(
            month=f"{int(row.yr)}-{int(row.mo):02d}",
            revenue=float(row.revenue or 0.0)
        ))
    return results

@router.get("/system-overview", response_model=schemas.SystemOverviewResponse)
def get_system_overview(
    brand_id: Optional[str] = None,
    region_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set)
):
    if current_user.role not in ("admin", "general_manager", "supervisor", "overseer", "rep"):
        raise HTTPException(status_code=403, detail="Not authorized")

    scope = auth.get_scope(current_user, db, requested_region_id=region_id)
    region_ids = _region_scope(scope, region_id)
    if current_user.role in ("supervisor", "rep"):
        assigned_brands = set(current_user.brand_ids)
        if current_user.brand_id:
            assigned_brands.add(current_user.brand_id)
        if assigned_brands:
            if brand_id and brand_id not in assigned_brands:
                brand_id = current_user.brand_id or next(iter(assigned_brands))
            elif not brand_id and len(assigned_brands) == 1:
                brand_id = next(iter(assigned_brands))

    today = datetime.date.today()
    first_of_month = today.replace(day=1)
    
    total_revenue = calculate_revenue(
        db, first_of_month, today, rep_ids=scope.rep_ids,
        brand_id=brand_id, region_ids=region_ids,
    )
    
    users_q = db.query(models.User)
    centers_q = db.query(models.Center).filter(models.Center.is_active == True)
    products_q = db.query(models.Product)
    low_stock_q = db.query(models.Product).filter(models.Product.stock_qty <= models.Product.min_threshold)
    
    if brand_id:
        users_q = users_q.filter(models.User.brand_id == brand_id)
        centers_q = centers_q.filter(models.Center.brand_id == brand_id)
        products_q = products_q.filter(models.Product.brand_id == brand_id)
        low_stock_q = low_stock_q.filter(models.Product.brand_id == brand_id)
    if scope.rep_ids is not None:
        users_q = users_q.filter(models.User.id.in_(scope.rep_ids))
    if scope.region_ids is not None:
        users_q = users_q.filter(models.User.region_id.in_(scope.region_ids))
        centers_q = centers_q.filter(models.Center.region_id.in_(scope.region_ids))
        
    total_users = users_q.count()
    total_centers = centers_q.count()
    total_products = products_q.count()
    
    visits_today_q = db.query(models.Visit).filter(func.date(models.Visit.visit_date) == today)
    if brand_id:
        visits_today_q = visits_today_q.filter(
            (models.Visit.brand_id == brand_id) |
            (models.Visit.rep_id.in_(db.query(models.User.id).filter(models.User.brand_id == brand_id)))
        )
    if scope.rep_ids is not None:
        visits_today_q = visits_today_q.filter(models.Visit.rep_id.in_(scope.rep_ids))
    if scope.region_ids is not None:
        visits_today_q = visits_today_q.filter(models.Visit.region_id.in_(scope.region_ids))
    visits_today = visits_today_q.count()
    
    low_stock_count = low_stock_q.count()

    pending_appts_q = db.query(models.Appointment).filter(models.Appointment.status == "pending")
    if scope.rep_ids is not None:
        pending_appts_q = pending_appts_q.filter(models.Appointment.rep_id.in_(scope.rep_ids))
    if scope.region_ids is not None or brand_id:
        user_query = db.query(models.User.id)
        if scope.region_ids is not None:
            user_query = user_query.filter(models.User.region_id.in_(scope.region_ids))
        if brand_id:
            user_query = user_query.filter(models.User.brand_id == brand_id)
        pending_appts_q = pending_appts_q.filter(models.Appointment.rep_id.in_(user_query))
    pending_appointments = pending_appts_q.count()

    region_coverage = []
    region_query = db.query(models.Center.region_id, models.Region.name_ar).join(
        models.Region, models.Center.region_id == models.Region.id
    ).filter(models.Center.is_active == True).distinct()
    if brand_id:
        region_query = region_query.filter(models.Center.brand_id == brand_id)
    if scope.region_ids is not None:
        region_query = region_query.filter(models.Center.region_id.in_(scope.region_ids))
    for region_id_value, region_name in region_query:
        if not region_name:
            continue
            
        region_total_centers_q = db.query(models.Center).filter(
            models.Center.region_id == region_id_value, models.Center.is_active == True
        )
        if brand_id:
            region_total_centers_q = region_total_centers_q.filter(models.Center.brand_id == brand_id)
        region_total_centers = region_total_centers_q.count()

        visited_q = db.query(func.count(func.distinct(models.Visit.center_id))).join(models.Center).filter(
            models.Center.region_id == region_id_value,
            models.Center.is_active == True,
            models.Visit.status == "completed",
            func.date(models.Visit.visit_date) >= today.replace(day=1)
        )
        if brand_id:
            visited_q = visited_q.filter(models.Center.brand_id == brand_id)
        visited = visited_q.scalar() or 0
        
        pct = round((visited / region_total_centers) * 100, 1) if region_total_centers else 0.0
        region_coverage.append(schemas.RegionCoverage(
            region=region_name,
            visits_completed=visited,
            total_centers=region_total_centers,
            coverage_percent=pct
        ))

    return schemas.SystemOverviewResponse(
        total_revenue=total_revenue,
        total_users=total_users,
        total_centers=total_centers,
        total_products=total_products,
        visits_today=visits_today,
        low_stock_count=low_stock_count,
        pending_appointments=pending_appointments,
        region_coverage=region_coverage
    )

def _check_rep_access(current_user: models.User, rep_id: str, db: Session) -> models.User:
    rep = db.query(models.User).filter(models.User.id == rep_id).first()
    if not rep:
        raise HTTPException(status_code=404, detail="Rep not found")
    if current_user.role == "supervisor":
        scope = auth.get_scope(current_user, db)
        if rep.id not in (scope.rep_ids or []) or rep.region_id not in (scope.region_ids or []):
            raise HTTPException(status_code=403, detail="Not authorized for this rep")
    elif current_user.role == "rep":
        if rep.id != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized")
    elif current_user.role not in ("admin", "general_manager", "overseer"):
        raise HTTPException(status_code=403, detail="Not authorized")
    return rep

@router.get("/rep/{rep_id}/report", response_model=schemas.RepReportResponse)
def get_rep_report(
    rep_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set)
):
    rep = _check_rep_access(current_user, rep_id, db)
    
    today = datetime.date.today()
    if start_date:
        sd = datetime.date.fromisoformat(start_date)
    else:
        sd = today.replace(day=1)
    if end_date:
        ed = datetime.date.fromisoformat(end_date)
    else:
        ed = today
    
    # Visits
    visits_q = db.query(models.Visit).filter(
        models.Visit.rep_id == rep_id,
        func.date(models.Visit.visit_date) >= sd,
        func.date(models.Visit.visit_date) <= ed
    )
    total_visits = visits_q.count()
    visits_completed = visits_q.filter(models.Visit.status == "completed").count()
    visits_flagged = visits_q.filter(models.Visit.status == "flagged").count()
    visits_rejected = visits_q.filter(models.Visit.status == "rejected").count()
    
    # Revenue & Sales
    items_q = db.query(models.VisitItem).join(models.Visit).filter(
        models.Visit.rep_id == rep_id,
        models.Visit.status.notin_(["flagged", "rejected"]),
        func.date(models.Visit.visit_date) >= sd,
        func.date(models.Visit.visit_date) <= ed
    )
    revenue = sum((i.qty_sold * (i.price_at_sale or 0)) for i in items_q.all())
    total_sold = sum(i.qty_sold for i in items_q.all())
    
    # Top Products
    product_results = db.query(
        models.Product.id,
        models.Product.name,
        func.sum(models.VisitItem.qty_sold).label('units_sold'),
        func.sum(models.VisitItem.qty_sold * models.VisitItem.price_at_sale).label('total_revenue')
    ).join(models.VisitItem, models.VisitItem.product_id == models.Product.id).join(models.Visit).filter(
        models.Visit.rep_id == rep_id,
        models.Visit.status.notin_(["flagged", "rejected"]),
        func.date(models.Visit.visit_date) >= sd,
        func.date(models.Visit.visit_date) <= ed
    ).group_by(models.Product.id).order_by(func.sum(models.VisitItem.qty_sold * models.VisitItem.price_at_sale).desc()).limit(5).all()
    
    top_products = []
    for rank, row in enumerate(product_results, 1):
        top_products.append(schemas.ProductPerformanceResponse(
            product_id=row[0],
            product_name=row[1],
            units_sold=row[2] or 0,
            total_revenue=float(row[3] or 0.0),
            rank=rank
        ))
    
    # Targets
    target_q = db.query(func.sum(models.Target.target_qty)).filter(
        models.Target.rep_id == rep_id,
        models.Target.period_start <= ed,
        models.Target.period_end >= sd
    ).scalar() or 0
    
    achieved_q = db.query(func.sum(models.VisitItem.qty_sold)).join(models.Visit).filter(
        models.Visit.rep_id == rep_id,
        models.Visit.status.notin_(["flagged", "rejected"]),
        func.date(models.Visit.visit_date) >= sd,
        func.date(models.Visit.visit_date) <= ed
    ).scalar() or 0
    
    completion = (achieved_q / target_q) * 100 if target_q > 0 else 100.0
    
    # Recent visits
    recent = visits_q.order_by(models.Visit.visit_date.desc()).limit(10).all()
    rep_cache = {}
    for v in recent:
        if v.rep_id not in rep_cache:
            rep = db.query(models.User).filter(models.User.id == v.rep_id).first()
            rep_cache[v.rep_id] = rep.role if rep else None
        v.rep_role = rep_cache[v.rep_id]
    recent_visits = []
    for v in recent:
        center = db.query(models.Center).filter(models.Center.id == v.center_id).first()
        recent_visits.append(schemas.VisitResponse(
            id=v.id,
            rep_id=v.rep_id,
            rep_role=v.rep_role,
            center_id=v.center_id,
            visit_date=v.visit_date,
            arrival_time=v.arrival_time,
            completion_time=v.completion_time,
            status=v.status,
            notes=v.notes,
            latitude=v.latitude,
            longitude=v.longitude,
            synced=v.synced,
            created_at=v.created_at,
            updated_at=v.updated_at,
            review_note=v.review_note,
        ))
    
    return schemas.RepReportResponse(
        rep_id=rep.id,
        rep_name=rep.full_name,
        rep_email=rep.email,
        rep_phone=rep.phone,
        rep_region=rep.region,
        total_visits=total_visits,
        total_revenue=revenue,
        total_sold_qty=total_sold,
        visits_completed=visits_completed,
        visits_flagged=visits_flagged,
        visits_rejected=visits_rejected,
        target_qty=target_q,
        achieved_qty=achieved_q,
        target_completion_percent=round(completion, 1),
        top_products=top_products,
        recent_visits=recent_visits,
        period_start=sd.isoformat(),
        period_end=ed.isoformat()
    )

class PDFReport(FPDF):
    def header(self):
        import os
        logo_path = "static/report_logo.png"
        if os.path.exists(logo_path):
            self.image(logo_path, x=10, y=8, w=20)
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 10, "PDOS - Rep Performance Report", new_x="LMARGIN", new_y="NEXT", align="C")
        self.ln(4)
    
    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

@router.get("/rep/{rep_id}/report/pdf")
def get_rep_report_pdf(
    rep_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set)
):
    report = get_rep_report(rep_id, start_date, end_date, db, current_user)
    
    pdf = PDFReport()
    pdf.alias_nb_pages()
    pdf.add_page()
    
    # Rep Info
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, f"Rep: {report.rep_name}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Email: {report.rep_email}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Region: {report.rep_region or 'N/A'}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Period: {report.period_start} to {report.period_end}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)
    
    # Summary KPI
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "Summary", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    for label, value in [
        ("Total Visits", str(report.total_visits)),
        ("Completed Visits", str(report.visits_completed)),
        ("Flagged Visits", str(report.visits_flagged)),
        ("Total Revenue (IQD)", f"{report.total_revenue:,.0f}"),
        ("Total Items Sold", str(report.total_sold_qty)),
        ("Target Completion", f"{report.target_completion_percent}%"),
    ]:
        pdf.cell(80, 6, label)
        pdf.cell(0, 6, value, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)
    
    # Top Products
    if report.top_products:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "Top Products", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(10, 6, "#")
        pdf.cell(70, 6, "Product")
        pdf.cell(30, 6, "Units Sold")
        pdf.cell(0, 6, "Revenue", new_x="LMARGIN", new_y="NEXT")
        for p in report.top_products:
            pdf.cell(10, 6, str(p.rank))
            pdf.cell(70, 6, p.product_name)
            pdf.cell(30, 6, str(p.units_sold))
            pdf.cell(0, 6, f"{p.total_revenue:,.0f}", new_x="LMARGIN", new_y="NEXT")
    
    # Recent Visits
    if report.recent_visits:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "Recent Visits", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        for v in report.recent_visits:
            pdf.cell(0, 6, f"{v.visit_date.date()} - {v.status}", new_x="LMARGIN", new_y="NEXT")
            pdf.set_x(20)
            pdf.cell(0, 5, f"Notes: {v.notes or '—'}", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
    
    buf = io.BytesIO()
    pdf.output(buf)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename=rep_report_{rep_id}.pdf"})
@router.get("/team-summary")
def get_team_summary(
    brand_id: Optional[str] = None,
    region_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_password_set)
):
    if current_user.role != "supervisor":
        raise HTTPException(status_code=403, detail="Only supervisors can view team summary")
    
    from routers.deps import get_role_scoped_rep_ids
    scope = auth.get_scope(current_user, db, requested_region_id=region_id)
    rep_ids = get_role_scoped_rep_ids(current_user, db) or []
    if region_id and scope.region_ids is not None:
        rep_ids = [
            rep.id for rep in db.query(models.User).filter(
                models.User.id.in_(rep_ids),
                models.User.region_id.in_(scope.region_ids),
            ).all()
        ]

    now = datetime.datetime.now()
    start_of_month = datetime.date(now.year, now.month, 1)
    end_of_month = (datetime.date(now.year, now.month + 1, 1) - datetime.timedelta(days=1)) if now.month < 12 else datetime.date(now.year, 12, 31)

    # Team size
    team_size = len(rep_ids)

    # Total visits this month
    visit_q = db.query(models.Visit).filter(
        models.Visit.rep_id.in_(rep_ids),
        func.date(models.Visit.visit_date) >= start_of_month,
        func.date(models.Visit.visit_date) <= end_of_month,
        models.Visit.status.notin_(['flagged', 'rejected'])
    )
    if brand_id:
        visit_q = visit_q.filter(models.Visit.brand_id == brand_id)
    if scope.region_ids is not None:
        visit_q = visit_q.filter(models.Visit.region_id.in_(scope.region_ids))
    total_visits = visit_q.count()

    # Total revenue this month
    rev_q = db.query(func.sum(models.VisitItem.qty_sold * models.VisitItem.price_at_sale)).join(models.Visit).filter(
        models.Visit.rep_id.in_(rep_ids),
        func.date(models.Visit.visit_date) >= start_of_month,
        func.date(models.Visit.visit_date) <= end_of_month,
        models.Visit.status.notin_(['flagged', 'rejected'])
    )
    if brand_id:
        rev_q = rev_q.filter(models.Visit.brand_id == brand_id)
    if scope.region_ids is not None:
        rev_q = rev_q.filter(models.Visit.region_id.in_(scope.region_ids))
    total_revenue = rev_q.scalar() or 0.0

    # Top performing rep this month
    top_rep_q = db.query(
        models.User.full_name,
        func.count(models.Visit.id.distinct()).label("visits"),
        func.sum(models.VisitItem.qty_sold * models.VisitItem.price_at_sale).label("revenue")
    ).join(models.Visit, models.Visit.rep_id == models.User.id)\
     .outerjoin(models.VisitItem, models.VisitItem.visit_id == models.Visit.id)\
     .filter(
        models.User.id.in_(rep_ids),
        func.date(models.Visit.visit_date) >= start_of_month,
        func.date(models.Visit.visit_date) <= end_of_month,
        models.Visit.status.notin_(['flagged', 'rejected'])
     )
    
    if brand_id:
        top_rep_q = top_rep_q.filter(models.Visit.brand_id == brand_id)
    if scope.region_ids is not None:
        top_rep_q = top_rep_q.filter(models.Visit.region_id.in_(scope.region_ids))
        
    top_rep_query = top_rep_q.group_by(models.User.id)\
     .order_by(func.sum(models.VisitItem.qty_sold * models.VisitItem.price_at_sale).desc())\
     .first()

    top_rep_data = None
    if top_rep_query:
        top_rep_data = {
            "name": top_rep_query.full_name,
            "visits": top_rep_query.visits,
            "revenue": float(top_rep_query.revenue or 0.0)
        }

    return {
        "team_size": team_size,
        "total_visits_month": total_visits,
        "total_revenue_month": float(total_revenue),
        "top_rep": top_rep_data,
    }
