"""Analytics routes providing customizable data views."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Iterable

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.auth import User
from app.database import get_session
from app.dependencies import templates
from app.core.formatting import format_display_date, format_display_datetime
from app.models import (
    AdhocPayment,
    ModelCompensationAdjustment,
    Payout,
    ScheduleRun,
)
from app.routers.auth import get_current_user

router = APIRouter(prefix="/analytics", tags=["Analytics"])


def _default_date_range() -> tuple[date, date]:
    today = date.today()
    return today - timedelta(days=30), today


def _parse_date(value: str | None, fallback: date) -> date:
    if not value:
        return fallback
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:  # pragma: no cover - safeguards invalid input
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.") from exc


@router.get("")
def analytics_home(
    request: Request,
    db: Session = Depends(get_session),  # noqa: ARG001 - parity with other views
    user: User = Depends(get_current_user),
):
    start_default, end_default = _default_date_range()
    dataset_options = [
        {"id": "payouts", "label": "Payroll Payouts"},
        {"id": "adhoc", "label": "Ad Hoc Payments"},
        {"id": "adjustments", "label": "Compensation Adjustments"},
        {"id": "runs", "label": "Schedule Runs"},
    ]
    return templates.TemplateResponse(
        "analytics/index.html",
        {
            "request": request,
            "user": user,
            "dataset_options": dataset_options,
            "default_start": start_default.strftime("%Y-%m-%d"),
            "default_end": end_default.strftime("%Y-%m-%d"),
        },
    )


def _serialize_payouts(items: Iterable[Payout]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for payout in items:
        rows.append(
            {
                "run_id": payout.schedule_run_id,
                "code": payout.code,
                "working_name": payout.working_name,
                "pay_date": format_display_date(payout.pay_date),
                "amount": float(payout.amount) if payout.amount is not None else 0.0,
                "status": payout.status,
                "payment_method": payout.payment_method,
            }
        )
    return rows


def _serialize_adhoc(items: Iterable[AdhocPayment]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for record in items:
        rows.append(
            {
                "model_id": record.model_id,
                "model_code": record.model.code if record.model else None,
                "pay_date": format_display_date(record.pay_date),
                "amount": float(record.amount) if record.amount is not None else 0.0,
                "status": record.status,
                "description": record.description,
            }
        )
    return rows


def _serialize_adjustments(items: Iterable[ModelCompensationAdjustment]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for adjustment in items:
        rows.append(
            {
                "model_id": adjustment.model_id,
                "model_code": adjustment.model.code if adjustment.model else None,
                "effective_date": format_display_date(adjustment.effective_date),
                "amount_monthly": float(adjustment.amount_monthly),
                "notes": adjustment.notes,
            }
        )
    return rows


def _serialize_runs(items: Iterable[ScheduleRun]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for run in items:
        rows.append(
            {
                "run_id": run.id,
                "cycle": format_display_date(date(run.target_year, run.target_month, 1)),
                "created_at": format_display_datetime(run.created_at),
                "currency": run.currency,
                "models_paid": run.summary_models_paid,
                "total_payout": float(run.summary_total_payout or 0),
            }
        )
    return rows


@router.get("/data")
def analytics_data(
    start: str | None = Query(default=None, description="Inclusive start date (YYYY-MM-DD)"),
    end: str | None = Query(default=None, description="Inclusive end date (YYYY-MM-DD)"),
    datasets: str = Query(default="payouts", description="Comma separated dataset identifiers"),
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),  # noqa: ARG001 - ensure auth
):
    start_default, end_default = _default_date_range()
    start_date = _parse_date(start, start_default)
    end_date = _parse_date(end, end_default)
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="End date must be on or after start date.")

    requested = {item.strip().lower() for item in datasets.split(",") if item.strip()}
    if not requested:
        requested = {"payouts"}

    response: dict[str, list[dict[str, object]]] = {}
    paid_total = Decimal("0")
    unpaid_total = Decimal("0")

    def _record_amount(amount: Decimal | None, status: str | None) -> None:
        nonlocal paid_total, unpaid_total
        if amount is None:
            return
        amt = amount if isinstance(amount, Decimal) else Decimal(str(amount))
        status_normalized = (status or "").strip().lower()
        if status_normalized in {"paid", "complete", "completed"}:
            paid_total += amt
        else:
            unpaid_total += amt

    if "payouts" in requested:
        payouts = (
            db.query(Payout)
            .filter(Payout.pay_date >= start_date, Payout.pay_date <= end_date)
            .order_by(Payout.pay_date.desc(), Payout.code)
            .all()
        )
        response["payouts"] = _serialize_payouts(payouts)
        for payout in payouts:
            _record_amount(payout.amount, payout.status)

    if "adhoc" in requested:
        adhoc_records = (
            db.query(AdhocPayment)
            .filter(AdhocPayment.pay_date >= start_date, AdhocPayment.pay_date <= end_date)
            .order_by(AdhocPayment.pay_date.desc())
            .all()
        )
        response["adhoc"] = _serialize_adhoc(adhoc_records)
        for record in adhoc_records:
            _record_amount(record.amount, record.status)

    if "adjustments" in requested:
        adjustments = (
            db.query(ModelCompensationAdjustment)
            .filter(
                ModelCompensationAdjustment.effective_date >= start_date,
                ModelCompensationAdjustment.effective_date <= end_date,
            )
            .order_by(ModelCompensationAdjustment.effective_date.desc())
            .all()
        )
        response["adjustments"] = _serialize_adjustments(adjustments)

    if "runs" in requested:
        runs = (
            db.query(ScheduleRun)
            .filter(
                ScheduleRun.created_at >= datetime.combine(start_date, datetime.min.time()),
                ScheduleRun.created_at <= datetime.combine(end_date, datetime.max.time()),
            )
            .order_by(ScheduleRun.created_at.desc())
            .all()
        )
        response["runs"] = _serialize_runs(runs)

    meta = {
        "start": format_display_date(start_date),
        "end": format_display_date(end_date),
        "start_iso": start_date.isoformat(),
        "end_iso": end_date.isoformat(),
        "datasets": sorted(response.keys()),
        "counts": {key: len(value) for key, value in response.items()},
        "totals": {
            "paid": float(paid_total),
            "unpaid": float(unpaid_total),
        },
    }

    return JSONResponse({"meta": meta, "results": response})
