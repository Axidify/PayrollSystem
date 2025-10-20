"""Dashboard routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app import crud
from app.auth import User
from app.database import get_session
from app.dependencies import templates
from app.routers.auth import get_current_user

router = APIRouter(tags=["Dashboard"])


@router.get("/dashboard")
def dashboard(request: Request, db: Session = Depends(get_session), user: User = Depends(get_current_user)):
    summary = crud.dashboard_summary(db)
    latest = summary.get("latest_run")
    if latest is not None:
        summary["latest_run"] = {
            "target_year": latest.target_year,
            "target_month": latest.target_month,
            "created_at": latest.created_at,
        }
    recent_runs_data = []
    for run in crud.recent_schedule_runs(db):
        recent_runs_data.append(
            {
                "id": run.id,
                "target_year": run.target_year,
                "target_month": run.target_month,
                "created_at": run.created_at,
                "currency": run.currency,
                "summary_total_payout": run.summary_total_payout,
            }
        )

    top_models_data = []
    for model, total in crud.top_paid_models(db):
        top_models_data.append(
            {
                "code": model.code,
                "working_name": model.working_name,
                "status": model.status,
                "total_paid": total,
            }
        )

    pending_adhoc_data = []
    for payment in crud.pending_adhoc_payments(db):
        pending_adhoc_data.append(
            {
                "id": payment.id,
                "pay_date": payment.pay_date,
                "amount": payment.amount,
                "status": payment.status,
                "model_code": payment.model.code if payment.model else None,
                "model_name": payment.model.working_name if payment.model else None,
            }
        )

    return templates.TemplateResponse(
        "dashboard/index.html",
        {
            "request": request,
            "user": user,
            "summary": summary,
            "recent_runs": recent_runs_data,
            "top_models": top_models_data,
            "pending_adhoc_payments": pending_adhoc_data,
        },
    )
