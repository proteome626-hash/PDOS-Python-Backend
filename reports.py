import datetime
from typing import Literal, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy import func

import models
import auth
from database import get_db
from report_builders import build_excel, build_csv

router = APIRouter(prefix="/reports", tags=["Reports"])

def get_sales_rows(db: Session, start_date: datetime.date, end_date: datetime.date, rep_ids: Optional[List[str]]) -> List[dict]:
    query = db.query(
        models.Visit.visit_date,
        models.Visit.status,
        models.User.full_name.label("rep_name"),
        models.Center.name.label("center_name"),
        models.Product.name.label("product_name"),
        models.VisitItem.qty_sold,
        models.VisitItem.price_at_sale
    ).join(models.User, models.Visit.rep_id == models.User.id) \
     .outerjoin(models.Center, models.Visit.center_id == models.Center.id) \
     .join(models.VisitItem, models.Visit.id == models.VisitItem.visit_id) \
     .join(models.Product, models.VisitItem.product_id == models.Product.id) \
     .filter(
         func.date(models.Visit.visit_date) >= start_date,
         func.date(models.Visit.visit_date) <= end_date,
         models.Visit.status.notin_(['flagged', 'rejected'])
     )

    if rep_ids is not None:
        query = query.filter(models.Visit.rep_id.in_(rep_ids))

    rows = query.order_by(models.Visit.visit_date.desc()).all()

    results = []
    for r in rows:
        results.append({
            "Date": r.visit_date.strftime("%Y-%m-%d %H:%M"),
            "Status": r.status,
            "Rep Name": r.rep_name,
            # LEFT JOIN: client-side visits may have no center
            "Center": r.center_name or "(no center)",
            "Product": r.product_name,
            "Quantity": r.qty_sold,
            "Price": float(r.price_at_sale),
            "Total": float(r.qty_sold * r.price_at_sale)
        })
    return results

@router.get("/export")
def export_report(
    type: Literal["excel", "csv"], 
    start_date: datetime.date, 
    end_date: datetime.date, 
    current_user: models.User = Depends(auth.require_password_set), 
    db: Session = Depends(get_db)
):
    rep_ids = auth.get_role_scoped_rep_ids(current_user, db)
    rows = get_sales_rows(db, start_date, end_date, rep_ids)
    
    if type == "excel":
        file_bytes = build_excel(rows)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"sales_export_{start_date}_to_{end_date}.xlsx"
    else:
        file_bytes = build_csv(rows)
        media_type = "text/csv"
        filename = f"sales_export_{start_date}_to_{end_date}.csv"
        
    return Response(
        content=file_bytes, 
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
