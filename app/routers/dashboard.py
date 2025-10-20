"""Dashboard routes."""
from __future__ import annotations

import csv
from datetime import date, datetime
from io import StringIO
from typing import Iterable

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, selectinload

from app import crud
from app.auth import User
from app.database import get_session
from app.dependencies import templates
from app.core.formatting import format_display_date, format_display_datetime
from app.routers.auth import get_current_user
from app.models import Model
from app.exporting import export_full_workbook
from fastapi.responses import Response

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
            "cycle_display": format_display_date(date(latest.target_year, latest.target_month, 1)),
        }
    recent_runs_data = []
    for run in crud.recent_schedule_runs(db):
        recent_runs_data.append(
            {
                "id": run.id,
                "target_year": run.target_year,
                "target_month": run.target_month,
                "cycle_display": format_display_date(date(run.target_year, run.target_month, 1)),
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


def _format_datetime_for_export(value: datetime | None) -> str:
    return format_display_datetime(value)


def _format_simple_date(value) -> str:
    return format_display_date(value)


def _iter_model_export_rows(models: Iterable[Model]) -> Iterable[list[str]]:
    headers = [
        "model_id",
        "model_code",
        "status",
        "real_name",
        "working_name",
        "start_date",
        "payment_method",
        "payment_frequency",
        "amount_monthly",
        "crypto_wallet",
        "model_created_at",
        "model_updated_at",
        "adhoc_id",
        "adhoc_pay_date",
        "adhoc_amount",
        "adhoc_status",
        "adhoc_description",
        "adhoc_notes",
        "adhoc_created_at",
        "adhoc_updated_at",
    ]
    yield headers

    for model in models:
        payments = sorted(model.adhoc_payments, key=lambda item: (item.pay_date, item.id)) if model.adhoc_payments else []
        base_columns = [
            str(model.id),
            model.code or "",
            model.status or "",
            model.real_name or "",
            model.working_name or "",
            _format_simple_date(model.start_date),
            model.payment_method or "",
            model.payment_frequency or "",
            f"{model.amount_monthly:.2f}" if model.amount_monthly is not None else "",
            model.crypto_wallet or "",
            _format_datetime_for_export(model.created_at),
            _format_datetime_for_export(model.updated_at),
        ]

        if not payments:
            yield base_columns + ["", "", "", "", "", "", "", ""]
            continue

        for payment in payments:
            adhoc_columns = [
                str(payment.id),
                _format_simple_date(payment.pay_date),
                f"{payment.amount:.2f}" if payment.amount is not None else "",
                payment.status or "",
                payment.description or "",
                payment.notes or "",
                _format_datetime_for_export(payment.created_at),
                _format_datetime_for_export(payment.updated_at),
            ]
            yield base_columns + adhoc_columns


@router.get("/dashboard/export")
def export_dashboard_models(
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    _ = user  # ensure the user is authenticated but not otherwise used
    models = (
        db.query(Model)
        .options(selectinload(Model.adhoc_payments))
        .order_by(Model.code)
        .all()
    )

    buffer = StringIO()
    writer = csv.writer(buffer)
    for row in _iter_model_export_rows(models):
        writer.writerow(row)

    buffer.seek(0)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"payroll_models_export_{timestamp}.csv"
    csv_bytes = buffer.getvalue().encode("utf-8-sig")
    response = StreamingResponse(iter([csv_bytes]), media_type="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response


@router.get("/dashboard/export-xlsx")
def export_dashboard_xlsx(db: Session = Depends(get_session), user: User = Depends(get_current_user)) -> Response:
    if not user.is_admin():
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Admin privileges required")
    content = export_full_workbook(db)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"payroll_full_export_{timestamp}.xlsx"
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
