"""Routes for managing schedule runs."""
from __future__ import annotations

import calendar
import csv
import io
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Sequence
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from urllib.parse import urlencode

from app import crud
from app.auth import User
from app.database import get_session
from app.dependencies import templates
from app.core.formatting import format_display_date
from app.models import AdhocPayment, PAYOUT_STATUS_ENUM, Payout
from app.routers.auth import get_current_user, get_admin_user
from app.services import PayrollService

router = APIRouter(prefix="/schedules", tags=["Schedules"])

DEFAULT_EXPORT_DIR = Path("exports")

QUICK_RANGE_OPTIONS = [
    {"id": "past_7_days", "label": "Past 7 Days", "days": 7},
    {"id": "past_30_days", "label": "Past 30 Days", "days": 30},
    {"id": "past_3_months", "label": "Past 3 Months", "months": 3},
    {"id": "past_6_months", "label": "Past 6 Months", "months": 6},
    {"id": "past_1_year", "label": "Past 1 Year", "months": 12},
]


def _parse_date_param(value: str | None, field_label: str) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field_label} must be YYYY-MM-DD.") from exc


def _subtract_months(anchor: date, months: int) -> date:
    year = anchor.year
    month = anchor.month - months
    day = anchor.day
    while month <= 0:
        month += 12
        year -= 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last_day))


def _resolve_quick_range(identifier: str | None, today: date) -> tuple[date | None, date | None, str | None]:
    if not identifier:
        return None, None, None
    for option in QUICK_RANGE_OPTIONS:
        if option["id"] != identifier:
            continue
        if "days" in option:
            days = option["days"]
            start = today - timedelta(days=days - 1)
            return start, today, option["id"]
        months = option.get("months", 0)
        start = _subtract_months(today, months)
        return start, today, option["id"]
    return None, None, None


def _run_cycle_date(run) -> date:
    return date(run.target_year, run.target_month, 1)


def _within_range(candidate: date, start: date | None, end: date | None) -> bool:
    if start and candidate < start:
        return False
    if end and candidate > end:
        return False
    return True


def _filter_runs_by_range(runs: Sequence, start: date | None, end: date | None) -> list:
    filtered: list = []
    for run in runs:
        cycle_date = _run_cycle_date(run)
        if _within_range(cycle_date, start, end):
            filtered.append(run)
    return filtered


def _format_range_label(start: date | None, end: date | None, fallback: str) -> str:
    if start and end:
        return f"{format_display_date(start)} – {format_display_date(end)}"
    if start:
        return f"Since {format_display_date(start)}"
    if end:
        return f"Through {format_display_date(end)}"
    return fallback


def _build_run_card(run_obj, zero: Decimal) -> dict[str, object]:
    frequency_counts = getattr(run_obj, "frequency_counts", None)
    if not isinstance(frequency_counts, dict):
        try:
            frequency_counts = json.loads(run_obj.summary_frequency_counts)
        except (json.JSONDecodeError, AttributeError):
            frequency_counts = {}

    outstanding = getattr(run_obj, "unpaid_total", zero) or zero
    paid_total_value = getattr(run_obj, "paid_total", zero) or zero
    total_value = getattr(run_obj, "summary_total_payout", zero) or zero
    status = "Completed" if outstanding <= zero else "Needs Attention"
    status_variant = "success" if status == "Completed" else "warning"
    cycle_label = format_display_date(date(run_obj.target_year, run_obj.target_month, 1))

    return {
        "id": run_obj.id,
        "cycle": cycle_label,
        "created": format_display_date(run_obj.created_at),
        "models_paid": getattr(run_obj, "summary_models_paid", 0) or 0,
        "total": total_value,
        "paid": paid_total_value,
        "outstanding": outstanding,
        "status": status,
        "status_variant": status_variant,
        "frequency_counts": frequency_counts,
        "currency": getattr(run_obj, "currency", "USD"),
    }


def _summarize_adhoc_payments(payments: Sequence[AdhocPayment]) -> dict[str, object]:
    zero = Decimal("0")
    status_keys = ("pending", "paid", "cancelled")
    status_counts: dict[str, int] = {key: 0 for key in status_keys}
    amount_by_status: dict[str, Decimal] = {key: zero for key in status_keys}
    latest_pay_date: date | None = None
    impacted_models: set[int] = set()

    for payment in payments:
        status = (payment.status or "pending").lower()
        if status not in status_counts:
            status_counts[status] = 0
            amount_by_status[status] = zero
        status_counts[status] += 1
        amount_by_status[status] = amount_by_status[status] + payment.amount
        if latest_pay_date is None or payment.pay_date > latest_pay_date:
            latest_pay_date = payment.pay_date
        impacted_models.add(payment.model_id)

    pending_total = amount_by_status.get("pending", zero)
    paid_total = amount_by_status.get("paid", zero)
    cancelled_total = amount_by_status.get("cancelled", zero)
    total_amount = pending_total + paid_total

    return {
        "count": len(payments),
        "models_impacted": len(impacted_models),
        "status_counts": status_counts,
        "amount_by_status": amount_by_status,
        "total_amount": total_amount,
        "pending_total": pending_total,
        "paid_total": paid_total,
        "cancelled_total": cancelled_total,
        "latest_pay_date": latest_pay_date,
        "has_pending": pending_total > zero,
    }


def _compute_frequency_counts(db: Session, run_id: int) -> dict[str, int]:
    rows = (
        db.query(Payout.payment_frequency, func.count(func.distinct(Payout.code)))
        .filter(Payout.schedule_run_id == run_id)
        .group_by(Payout.payment_frequency)
        .all()
    )
    counts: dict[str, int] = {}
    for frequency, count in rows:
        label = frequency or "unspecified"
        counts[label] = int(count or 0)
    return counts


def _count_unique_models(db: Session, run_ids: list[int]) -> int:
    if not run_ids:
        return 0
    return (
        db.query(func.count(func.distinct(Payout.code)))
        .filter(Payout.schedule_run_id.in_(run_ids))
        .scalar()
        or 0
    )


def _prepare_runs_by_year(db: Session, target_year: int) -> tuple[list, list[int], list]:
    all_runs = crud.list_schedule_runs(db)

    runs_for_year: list = []
    available_years = sorted({run.target_year for run in all_runs}, reverse=True)

    for run in all_runs:
        try:
            run.frequency_counts = json.loads(run.summary_frequency_counts)
        except json.JSONDecodeError:
            run.frequency_counts = {}

        summary = crud.run_payment_summary(db, run.id)
        run.summary_models_paid = summary.get("paid_models", 0)
        run.paid_total = summary.get("paid_total", Decimal("0"))
        run.unpaid_total = summary.get("unpaid_total", Decimal("0"))
        run.frequency_counts = _compute_frequency_counts(db, run.id)
        if run.target_year == target_year:
            runs_for_year.append(run)

    runs_for_year.sort(key=lambda r: (r.target_month, r.created_at), reverse=True)

    return runs_for_year, available_years, all_runs


def _format_frequency_summary(frequency_counts: dict[str, int] | None) -> str:
    if not frequency_counts:
        return ""
    parts = []
    for name, count in sorted(frequency_counts.items()):
        label = (name or "unspecified").replace("_", " ").title()
        parts.append(f"{label} {count}")
    return ", ".join(parts)


def _gather_dashboard_data(db: Session, month: str | None) -> dict[str, object]:
    """Collect the datasets needed to render or export the schedules dashboard."""

    today = date.today()
    normalized_month = month.strip() if month else ""
    month_candidate: tuple[int, int] | None = None

    if normalized_month:
        try:
            year_str, month_str = normalized_month.split("-")
            year_value = int(year_str)
            month_value = int(month_str)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Month must be in YYYY-MM format.") from exc
        if not 1 <= month_value <= 12:
            raise HTTPException(status_code=400, detail="Month must be in YYYY-MM format.")
        month_candidate = (year_value, month_value)

    all_runs = crud.list_schedule_runs(db)

    grouped_runs: dict[tuple[int, int], list] = {}
    for run in all_runs:
        try:
            run.frequency_counts = json.loads(run.summary_frequency_counts)
        except json.JSONDecodeError:
            run.frequency_counts = {}

        summary = crud.run_payment_summary(db, run.id)
        run.summary_models_paid = summary.get("paid_models", 0)
        run.paid_total = summary.get("paid_total", Decimal("0"))
        run.unpaid_total = summary.get("unpaid_total", Decimal("0"))
        run.frequency_counts = _compute_frequency_counts(db, run.id)

        key = (run.target_year, run.target_month)
        grouped_runs.setdefault(key, []).append(run)

    sorted_keys = sorted(grouped_runs.keys(), reverse=True)

    selected_key: tuple[int, int] | None = None
    if month_candidate and month_candidate in grouped_runs:
        selected_key = month_candidate
    elif month_candidate and month_candidate not in grouped_runs:
        selected_key = month_candidate
    elif sorted_keys:
        selected_key = sorted_keys[0]

    zero = Decimal("0")

    selected_runs = grouped_runs.get(selected_key, []) if selected_key else []
    selected_run_ids = [run.id for run in selected_runs]

    monthly_frequency: dict[str, int] = {}
    if selected_run_ids:
        frequency_rows = (
            db.query(Payout.payment_frequency, func.count(func.distinct(Payout.code)))
            .filter(Payout.schedule_run_id.in_(selected_run_ids))
            .group_by(Payout.payment_frequency)
            .order_by(Payout.payment_frequency)
            .all()
        )
        for frequency, count in frequency_rows:
            label = frequency or "unspecified"
            monthly_frequency[label] = int(count or 0)

    unique_models = _count_unique_models(db, selected_run_ids)

    total_payout = sum(
        ((getattr(run, "summary_total_payout", zero) or zero) for run in selected_runs),
        zero,
    )
    paid_total = sum(((getattr(run, "paid_total", zero) or zero) for run in selected_runs), zero)
    unpaid_total = sum(((getattr(run, "unpaid_total", zero) or zero) for run in selected_runs), zero)

    monthly_summary = {
        "run_count": len(selected_runs),
        "models_paid": unique_models,
        "total_payout": total_payout,
        "paid_total": paid_total,
        "unpaid_total": unpaid_total,
    }

    month_options = []
    for year_value, month_value in sorted_keys:
        value = f"{year_value:04d}-{month_value:02d}"
        label = format_display_date(date(year_value, month_value, 1))
        month_options.append(
            {
                "value": value,
                "label": label,
                "run_count": len(grouped_runs[(year_value, month_value)]),
            }
        )

    if month_candidate and month_candidate not in grouped_runs:
        value = f"{month_candidate[0]:04d}-{month_candidate[1]:02d}"
        label = format_display_date(date(month_candidate[0], month_candidate[1], 1))
        month_options.insert(0, {"value": value, "label": label, "run_count": 0})

    selected_month_value = ""
    selected_month_label = ""
    if selected_key:
        selected_month_value = f"{selected_key[0]:04d}-{selected_key[1]:02d}"
        selected_month_label = format_display_date(date(selected_key[0], selected_key[1], 1))

    if selected_key:
        adhoc_year, adhoc_month = selected_key
    elif month_candidate:
        adhoc_year, adhoc_month = month_candidate
    else:
        adhoc_year, adhoc_month = today.year, today.month

    monthly_adhoc_payments = crud.list_adhoc_payments_for_month(db, adhoc_year, adhoc_month)
    monthly_adhoc_single = monthly_adhoc_payments[0] if len(monthly_adhoc_payments) == 1 else None
    monthly_adhoc_summary = _summarize_adhoc_payments(monthly_adhoc_payments)
    adhoc_month_label = format_display_date(date(adhoc_year, adhoc_month, 1))
    adhoc_month_value = f"{adhoc_year:04d}-{adhoc_month:02d}"
    latest_pay_date = monthly_adhoc_summary.get("latest_pay_date")
    monthly_adhoc_summary.update(
        {
            "month_label": adhoc_month_label,
            "month_value": adhoc_month_value,
            "currency": monthly_summary.get("currency") or "USD",
            "latest_pay_date_display": format_display_date(latest_pay_date),
        }
    )
    status_display = []
    for status_key, status_label in (
        ("pending", "Pending"),
        ("paid", "Paid"),
        ("cancelled", "Cancelled"),
    ):
        status_display.append(
            {
                "key": status_key,
                "label": status_label,
                "count": monthly_adhoc_summary["status_counts"].get(status_key, 0),
                "amount": monthly_adhoc_summary["amount_by_status"].get(status_key, zero),
            }
        )
    monthly_adhoc_summary["status_display"] = status_display
    monthly_adhoc_summary["has_payments"] = monthly_adhoc_summary["count"] > 0

    for option in month_options:
        option["is_selected"] = option["value"] == selected_month_value

    today_key = (today.year, today.month)

    sorted_runs = sorted(
        all_runs,
        key=lambda item: (item.target_year, item.target_month, item.created_at),
        reverse=True,
    )

    recent_runs = [run for run in sorted_runs if (run.target_year, run.target_month) < today_key][:4]

    recent_cards = [_build_run_card(run, zero) for run in recent_runs]
    selected_run_cards = [_build_run_card(run, zero) for run in selected_runs]

    if selected_runs:
        primary_currency = getattr(selected_runs[0], "currency", None)
    elif all_runs:
        primary_currency = getattr(all_runs[0], "currency", None)
    else:
        primary_currency = None

    if not primary_currency and selected_run_cards:
        primary_currency = selected_run_cards[0].get("currency")
    if not primary_currency and recent_cards:
        primary_currency = recent_cards[0].get("currency")

    monthly_summary["currency"] = primary_currency or "USD"

    current_year = today.year
    year_overview = []
    for month_index in range(1, 13):
        key = (current_year, month_index)
        month_label = format_display_date(date(current_year, month_index, 1))
        count = len(grouped_runs.get(key, []))
        year_overview.append(
            {
                "label": month_label,
                "count": count,
                "value": f"{current_year:04d}-{month_index:02d}",
                "is_current": key == today_key,
                "has_runs": bool(count),
            }
        )

    return {
        "today": today,
        "current_year": current_year,
        "current_month_label": format_display_date(today),
        "all_runs": all_runs,
        "selected_runs": selected_runs,
        "selected_run_cards": selected_run_cards,
        "recent_run_cards": recent_cards,
        "monthly_summary": monthly_summary,
        "monthly_frequency": monthly_frequency,
        "month_options": month_options,
        "selected_month_value": selected_month_value,
        "selected_month_label": selected_month_label,
        "monthly_adhoc_summary": monthly_adhoc_summary,
        "monthly_adhoc_payments": monthly_adhoc_payments,
        "monthly_adhoc_single": monthly_adhoc_single,
        "year_overview": year_overview,
        "filters": {"month": selected_month_value},
        "selected_key": selected_key,
        "month_candidate": month_candidate,
        "normalized_month": normalized_month,
    }


@router.get("/")
def list_runs(
    request: Request,
    month: str | None = None,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    dashboard = _gather_dashboard_data(db, month)

    export_defaults = {
        "monthly_summary": True,
        "run_details": bool(dashboard["selected_runs"]),
        "adhoc_summary": dashboard["monthly_adhoc_summary"].get("count", 0) > 0,
        "adhoc_details": dashboard["monthly_adhoc_summary"].get("count", 0) > 0,
        "recent_runs": bool(dashboard["recent_run_cards"]),
    }

    return templates.TemplateResponse(
        "schedules/list.html",
        {
            "request": request,
            "user": user,
            "runs": dashboard["selected_runs"],
            "filters": dashboard["filters"],
            "month_options": dashboard["month_options"],
            "selected_month_label": dashboard["selected_month_label"],
            "current_month_label": dashboard["current_month_label"],
            "monthly_summary": dashboard["monthly_summary"],
            "monthly_frequency": dashboard["monthly_frequency"],
            "has_runs": bool(dashboard["all_runs"]),
            "recent_runs": dashboard["recent_run_cards"],
            "selected_run_cards": dashboard["selected_run_cards"],
            "monthly_adhoc_summary": dashboard["monthly_adhoc_summary"],
            "monthly_adhoc_payments": dashboard["monthly_adhoc_payments"],
            "monthly_adhoc_single": dashboard["monthly_adhoc_single"],
            "year_overview": dashboard["year_overview"],
            "view_all_url": f"/schedules/all?year={dashboard['current_year']}",
            "table_view_url": f"/schedules/all-table?year={dashboard['current_year']}",
            "export_defaults": export_defaults,
            "export_month_value": dashboard["selected_month_value"],
        },
    )


@router.post("/export")
def export_dashboard_excel(
    month: str | None = Form(None),
    include_monthly_summary: bool = Form(False),
    include_run_details: bool = Form(False),
    include_adhoc_summary: bool = Form(False),
    include_adhoc_details: bool = Form(False),
    include_recent_runs: bool = Form(False),
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    dashboard = _gather_dashboard_data(db, month)

    options = {
        "monthly_summary": include_monthly_summary,
        "run_details": include_run_details,
        "adhoc_summary": include_adhoc_summary,
        "adhoc_details": include_adhoc_details,
        "recent_runs": include_recent_runs,
    }

    if not any(options.values()):
        raise HTTPException(status_code=400, detail="Select at least one dataset to export.")

    def _decimal_to_float(value: Decimal | float | int | None) -> float:
        if value is None:
            return 0.0
        if isinstance(value, Decimal):
            return float(value)
        return float(value)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        if options["monthly_summary"]:
            summary = dashboard["monthly_summary"]
            summary_rows = [
                {"Metric": "Month", "Value": dashboard["selected_month_label"] or "Latest Visible"},
                {"Metric": "Run Count", "Value": summary.get("run_count", 0)},
                {"Metric": "Models Paid", "Value": summary.get("models_paid", 0)},
                {"Metric": "Total Payout", "Value": _decimal_to_float(summary.get("total_payout"))},
                {"Metric": "Paid Total", "Value": _decimal_to_float(summary.get("paid_total"))},
                {"Metric": "Outstanding", "Value": _decimal_to_float(summary.get("unpaid_total"))},
                {"Metric": "Currency", "Value": summary.get("currency", "USD")},
            ]
            summary_df = pd.DataFrame(summary_rows)
            summary_df.to_excel(writer, sheet_name="Monthly Summary", index=False)

            frequency = dashboard["monthly_frequency"] or {}
            if frequency:
                freq_rows = [
                    {
                        "Frequency": (label or "unspecified").replace("_", " ").title(),
                        "Models": count,
                    }
                    for label, count in sorted(frequency.items())
                ]
                freq_df = pd.DataFrame(freq_rows)
                freq_df.to_excel(
                    writer,
                    sheet_name="Monthly Summary",
                    index=False,
                    startrow=len(summary_rows) + 2,
                )

        if options["run_details"]:
            run_rows: list[dict[str, object]] = []
            for card in dashboard["selected_run_cards"]:
                run_rows.append(
                    {
                        "Run ID": card.get("id"),
                        "Cycle": card.get("cycle"),
                        "Created": card.get("created"),
                        "Status": card.get("status"),
                        "Currency": card.get("currency"),
                        "Models Paid": card.get("models_paid"),
                        "Total Payout": _decimal_to_float(card.get("total")),
                        "Paid": _decimal_to_float(card.get("paid")),
                        "Outstanding": _decimal_to_float(card.get("outstanding")),
                        "Frequency Mix": _format_frequency_summary(card.get("frequency_counts")),
                    }
                )
            run_columns = [
                "Run ID",
                "Cycle",
                "Created",
                "Status",
                "Currency",
                "Models Paid",
                "Total Payout",
                "Paid",
                "Outstanding",
                "Frequency Mix",
            ]
            runs_df = pd.DataFrame(run_rows, columns=run_columns)
            runs_df.to_excel(writer, sheet_name="Runs", index=False)

        if options["adhoc_summary"]:
            adhoc_summary = dashboard["monthly_adhoc_summary"]
            adhoc_rows = [
                {"Metric": "Month", "Value": adhoc_summary.get("month_label", "")},
                {"Metric": "Payments", "Value": adhoc_summary.get("count", 0)},
                {"Metric": "Models Impacted", "Value": adhoc_summary.get("models_impacted", 0)},
                {"Metric": "Total Amount", "Value": _decimal_to_float(adhoc_summary.get("total_amount"))},
                {"Metric": "Pending Amount", "Value": _decimal_to_float(adhoc_summary.get("pending_total"))},
                {"Metric": "Paid Amount", "Value": _decimal_to_float(adhoc_summary.get("paid_total"))},
                {"Metric": "Cancelled Amount", "Value": _decimal_to_float(adhoc_summary.get("cancelled_total"))},
                {"Metric": "Latest Pay Date", "Value": adhoc_summary.get("latest_pay_date_display", "")},
            ]
            adhoc_df = pd.DataFrame(adhoc_rows)
            adhoc_df.to_excel(writer, sheet_name="Adhoc Summary", index=False)

            status_rows = [
                {
                    "Status": item.get("label"),
                    "Count": item.get("count", 0),
                    "Amount": _decimal_to_float(item.get("amount")),
                }
                for item in adhoc_summary.get("status_display", [])
            ]
            if status_rows:
                status_df = pd.DataFrame(status_rows)
                status_df.to_excel(
                    writer,
                    sheet_name="Adhoc Summary",
                    index=False,
                    startrow=len(adhoc_rows) + 2,
                )

        if options["adhoc_details"]:
            adhoc_detail_rows: list[dict[str, object]] = []
            for payment in dashboard["monthly_adhoc_payments"]:
                model_code = getattr(payment.model, "code", "") if getattr(payment, "model", None) else ""
                model_name = getattr(payment.model, "working_name", "") if getattr(payment, "model", None) else ""
                adhoc_detail_rows.append(
                    {
                        "Model Code": model_code,
                        "Model Name": model_name,
                        "Pay Date": format_display_date(payment.pay_date),
                        "Amount": _decimal_to_float(payment.amount if hasattr(payment, "amount") else 0),
                        "Status": (payment.status or "").replace("_", " ").title(),
                        "Description": payment.description or "",
                        "Notes": payment.notes or "",
                    }
                )
            adhoc_detail_columns = [
                "Model Code",
                "Model Name",
                "Pay Date",
                "Amount",
                "Status",
                "Description",
                "Notes",
            ]
            adhoc_details_df = pd.DataFrame(adhoc_detail_rows, columns=adhoc_detail_columns)
            adhoc_details_df.to_excel(writer, sheet_name="Adhoc Payments", index=False)

        if options["recent_runs"]:
            recent_rows: list[dict[str, object]] = []
            for card in dashboard["recent_run_cards"]:
                recent_rows.append(
                    {
                        "Run ID": card.get("id"),
                        "Cycle": card.get("cycle"),
                        "Created": card.get("created"),
                        "Status": card.get("status"),
                        "Currency": card.get("currency"),
                        "Models Paid": card.get("models_paid"),
                        "Total Payout": _decimal_to_float(card.get("total")),
                        "Paid": _decimal_to_float(card.get("paid")),
                        "Outstanding": _decimal_to_float(card.get("outstanding")),
                    }
                )
            recent_columns = [
                "Run ID",
                "Cycle",
                "Created",
                "Status",
                "Currency",
                "Models Paid",
                "Total Payout",
                "Paid",
                "Outstanding",
            ]
            recent_df = pd.DataFrame(recent_rows, columns=recent_columns)
            recent_df.to_excel(writer, sheet_name="Recent Runs", index=False)

    buffer.seek(0)

    month_slug = dashboard["selected_month_value"] or dashboard["selected_month_label"]
    if not month_slug:
        month_slug = dashboard["today"].strftime("%Y-%m")
    safe_slug = month_slug.replace(" ", "_").replace("/", "-")
    filename = f"schedules_dashboard_{safe_slug}.xlsx"

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/all")
def list_runs_all(
    request: Request,
    year: int = Query(default=None, description="Target year to display"),
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    today = date.today()
    target_year = year or today.year

    runs_for_year, available_years, all_runs = _prepare_runs_by_year(db, target_year)

    zero = Decimal("0")
    run_cards = [_build_run_card(run, zero) for run in runs_for_year]

    month_totals_map: dict[str, int] = {}
    for run in run_cards:
        month_totals_map[run["cycle"]] = month_totals_map.get(run["cycle"], 0) + 1

    month_totals: list[dict[str, object]] = []
    for month_index in range(1, 13):
        label = format_display_date(date(target_year, month_index, 1))
        month_value = f"{target_year:04d}-{month_index:02d}"
        count = month_totals_map.get(label, 0)
        month_totals.append(
            {
                "label": label,
                "count": count,
                "month_value": month_value,
                "has_runs": bool(count),
            }
        )

    return templates.TemplateResponse(
        "schedules/all.html",
        {
            "request": request,
            "user": user,
            "year": target_year,
            "runs": run_cards,
            "available_years": available_years,
            "month_totals": month_totals,
            "table_view_url": f"/schedules/all-table?year={target_year}",
        },
    )


@router.get("/all-table")
def list_runs_all_table(
    request: Request,
    year: int = Query(default=None, description="Target year to display"),
    start: str | None = Query(default=None, description="Filter start date (YYYY-MM-DD)"),
    end: str | None = Query(default=None, description="Filter end date (YYYY-MM-DD)"),
    range: str | None = Query(default=None, description="Quick range identifier"),
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    today = date.today()
    target_year = year or today.year

    runs_for_year, available_years, all_runs = _prepare_runs_by_year(db, target_year)

    start_date = _parse_date_param(start, "Start date")
    end_date = _parse_date_param(end, "End date")
    preset_start, preset_end, active_preset = _resolve_quick_range(range, today)

    if active_preset:
        start_date = preset_start
        end_date = preset_end

    if start_date and end_date and end_date < start_date:
        raise HTTPException(status_code=400, detail="End date must be on or after start date.")

    filter_active = bool(start_date or end_date)

    if filter_active:
        display_runs = _filter_runs_by_range(all_runs, start_date, end_date)
    else:
        display_runs = runs_for_year

    display_runs = sorted(display_runs, key=lambda run: run.created_at, reverse=True)

    zero = Decimal("0")
    run_ids = [run.id for run in display_runs]
    total_payout = sum((getattr(run, "summary_total_payout", zero) or zero) for run in display_runs)
    paid_total = sum((getattr(run, "paid_total", zero) or zero) for run in display_runs)
    unpaid_total = sum((getattr(run, "unpaid_total", zero) or zero) for run in display_runs)
    models_paid = _count_unique_models(db, run_ids)

    currency = None
    for run in display_runs:
        currency = getattr(run, "currency", None)
        if currency:
            break
    if not currency:
        if runs_for_year:
            currency = getattr(runs_for_year[0], "currency", None)
        elif all_runs:
            currency = getattr(all_runs[0], "currency", None)

    year_summary = {
        "run_count": len(display_runs),
        "total_payout": total_payout,
        "paid_total": paid_total,
        "unpaid_total": unpaid_total,
        "models_paid": models_paid,
        "currency": currency or "USD",
    }

    base_params: dict[str, object] = {}
    if target_year:
        base_params["year"] = target_year

    quick_ranges = []
    for option in QUICK_RANGE_OPTIONS:
        params = base_params.copy()
        params["range"] = option["id"]
        quick_ranges.append(
            {
                "id": option["id"],
                "label": option["label"],
                "url": f"/schedules/all-table?{urlencode(params)}",
                "is_active": option["id"] == active_preset,
            }
        )

    year_buttons = []
    for yr in available_years:
        params = {"year": yr}
        year_buttons.append(
            {
                "label": yr,
                "url": f"/schedules/all-table?{urlencode(params)}",
                "is_selected": yr == target_year,
            }
        )

    filter_start_value = start_date.isoformat() if start_date else ""
    filter_end_value = end_date.isoformat() if end_date else ""

    scope_label = _format_range_label(start_date, end_date, str(target_year))

    export_params = base_params.copy()
    if start_date:
        export_params["start"] = start_date.isoformat()
    if end_date:
        export_params["end"] = end_date.isoformat()
    if active_preset:
        export_params["range"] = active_preset
    export_query = urlencode(export_params)
    export_url = "/schedules/all-table/export"
    if export_query:
        export_url = f"{export_url}?{export_query}"

    clear_url = f"/schedules/all-table?{urlencode(base_params)}" if base_params else "/schedules/all-table"

    return templates.TemplateResponse(
        "schedules/all_table.html",
        {
            "request": request,
            "user": user,
            "year": target_year,
            "runs": display_runs,
            "year_summary": year_summary,
            "year_buttons": year_buttons,
            "has_previous_years": len(available_years) > 1,
            "card_view_url": f"/schedules/all?year={target_year}",
            "export_url": export_url,
            "quick_ranges": quick_ranges,
            "filter_active": filter_active,
            "filter_start_value": filter_start_value,
            "filter_end_value": filter_end_value,
            "active_preset": active_preset,
            "clear_url": clear_url,
            "scope_label": scope_label,
        },
    )


@router.get("/all-table/export")
def export_runs_all_table(
    year: int = Query(default=None, description="Target year to export"),
    start: str | None = Query(default=None, description="Filter start date (YYYY-MM-DD)"),
    end: str | None = Query(default=None, description="Filter end date (YYYY-MM-DD)"),
    range: str | None = Query(default=None, description="Quick range identifier"),
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    today = date.today()
    target_year = year or today.year

    runs_for_year, _, all_runs = _prepare_runs_by_year(db, target_year)

    start_date = _parse_date_param(start, "Start date")
    end_date = _parse_date_param(end, "End date")
    preset_start, preset_end, active_preset = _resolve_quick_range(range, today)

    if active_preset:
        start_date = preset_start
        end_date = preset_end

    if start_date and end_date and end_date < start_date:
        raise HTTPException(status_code=400, detail="End date must be on or after start date.")

    if start_date or end_date:
        export_runs = _filter_runs_by_range(all_runs, start_date, end_date)
    else:
        export_runs = runs_for_year

    export_runs = sorted(export_runs, key=lambda run: run.created_at, reverse=True)

    zero = Decimal("0")

    currency = None
    for run in export_runs:
        currency = getattr(run, "currency", None)
        if currency:
            break
    if not currency:
        if runs_for_year:
            currency = getattr(runs_for_year[0], "currency", None)
        elif all_runs:
            currency = getattr(all_runs[0], "currency", None)
    currency = currency or "USD"

    rows: list[dict[str, object]] = []
    for run in export_runs:
        card = _build_run_card(run, zero)
        frequency_display = _format_frequency_summary(card.get("frequency_counts"))
        rows.append(
            {
                "Run ID": card["id"],
                "Cycle": card["cycle"],
                "Created": card["created"],
                "Status": card["status"],
                "Currency": card["currency"],
                "Models Paid": card["models_paid"],
                "Total Payout": float(card["total"] or zero),
                "Paid": float(card["paid"] or zero),
                "Outstanding": float(card["outstanding"] or zero),
                "Frequency Mix": frequency_display,
            }
        )

    columns = [
        "Run ID",
        "Cycle",
        "Created",
        "Status",
        "Currency",
        "Models Paid",
        "Total Payout",
        "Paid",
        "Outstanding",
        "Frequency Mix",
    ]

    dataframe = pd.DataFrame(rows, columns=columns)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        sheet_name = f"Runs_{target_year}" if not (start_date or end_date or active_preset) else "Runs_Filtered"
        dataframe.to_excel(writer, sheet_name=sheet_name, index=False)

    buffer.seek(0)

    if start_date or end_date or active_preset:
        filename_label = _format_range_label(start_date, end_date, str(target_year)).replace(" ", "_").replace("/", "-")
        filename = f"payroll_runs_{filename_label}.xlsx"
    else:
        filename = f"payroll_runs_{target_year}.xlsx"

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/new")
def new_schedule_form(request: Request, user: User = Depends(get_admin_user)):
    today = date.today()
    default_month = f"{today.year:04d}-{today.month:02d}"
    return templates.TemplateResponse(
        "schedules/form.html",
        {
            "request": request,
            "user": user,
            "default_month": default_month,
            "default_currency": "USD",
            "default_output": str(DEFAULT_EXPORT_DIR),
        },
    )


@router.post("/new")
def run_schedule(
    request: Request,
    month: str = Form(...),
    currency: str = Form("USD"),
    include_inactive: str | None = Form(None),
    output_dir: str = Form(str(DEFAULT_EXPORT_DIR)),
    db: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    try:
        year_str, month_str = month.split("-")
        target_year = int(year_str)
        target_month = int(month_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Month must be in YYYY-MM format.")

    currency = currency.upper()

    export_path = Path(output_dir)
    export_path.mkdir(parents=True, exist_ok=True)

    service = PayrollService(db)
    _, _, _, _, run_id = service.run_payroll(
        target_year=target_year,
        target_month=target_month,
        currency=currency,
        include_inactive=bool(include_inactive),
        output_dir=export_path,
    )

    return RedirectResponse(url=f"/schedules/{run_id}", status_code=303)


@router.get("/{run_id}")
def view_schedule(
    run_id: int,
    request: Request,
    code: str | None = None,
    frequency: str | None = None,
    payment_method: str | None = None,
    status: str | None = None,
    pay_date: str | None = None,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    run = crud.get_schedule_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Schedule run not found")

    # Auto-refresh: if the run corresponds to the current month, re-run payroll
    # so newly added models for this month appear without requiring manual "Run Payroll".
    today = date.today()
    if run.target_year == today.year and run.target_month == today.month:
        # Re-run payroll for this cycle. The PayrollService will reuse the existing
        # ScheduleRun and preserve existing payout status/notes when refreshing.
        service = PayrollService(db)
        try:
            # Use the existing run's currency and export path when refreshing
            export_path = Path(run.export_path) if run.export_path else Path("exports")
            _, _, _, _, refreshed_run_id = service.run_payroll(
                target_year=run.target_year,
                target_month=run.target_month,
                currency=run.currency if getattr(run, "currency", None) else "USD",
                include_inactive=False,
                output_dir=export_path,
            )
            # If a different run record was returned, load that one instead
            if refreshed_run_id and refreshed_run_id != run.id:
                run = crud.get_schedule_run(db, refreshed_run_id)
        except Exception:
            # If refresh fails, continue to render the existing run rather than failing the page.
            # Errors are intentionally swallowed here to avoid blocking the user from viewing the run.
            pass

    run.cycle_display = format_display_date(date(run.target_year, run.target_month, 1))

    code_filter = code.strip() if code else None
    frequency_filter = frequency if frequency else None
    method_filter = payment_method if payment_method else None
    status_filter = status if status else None
    pay_date_filter: date | None = None

    if pay_date:
        pay_date_value = pay_date.strip()
        if pay_date_value:
            try:
                pay_date_filter = datetime.strptime(pay_date_value, "%m/%d/%Y").date()
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date format. Use MM/DD/YYYY.")

    code_options = crud.payout_codes_for_run(db, run_id)
    existing_pay_dates = set(crud.payout_dates_for_run(db, run_id))

    def ordinal(day_value: int) -> str:
        if 10 <= day_value % 100 <= 20:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(day_value % 10, "th")
        return f"{day_value}{suffix}"

    last_day = calendar.monthrange(run.target_year, run.target_month)[1]
    candidate_days = [7, 14, 21, last_day]
    pay_date_options = []
    for day in candidate_days:
        candidate_date = date(run.target_year, run.target_month, day)
        value = format_display_date(candidate_date)
        if day == last_day:
            label = f"End of Month ({value})"
        else:
            label = f"{ordinal(day)} ({value})"
        pay_date_options.append(
            {
                "value": value,
                "label": label,
                "available": candidate_date in existing_pay_dates,
            }
        )

    payouts = crud.list_payouts_for_run(
        db,
        run_id,
        code=code_filter,
        frequency=frequency_filter,
        payment_method=method_filter,
        status=status_filter,
        pay_date=pay_date_filter,
    )
    payout_total = sum((payout.amount or Decimal("0")) for payout in payouts)
    validations = crud.list_validation_for_run(db, run_id)
    try:
        frequency_counts = json.loads(run.summary_frequency_counts)
    except json.JSONDecodeError:
        frequency_counts = {}

    base_filename = f"pay_schedule_{run.target_year:04d}_{run.target_month:02d}_run{run.id}"
    export_path = Path(run.export_path)

    summary = crud.run_payment_summary(db, run_id)
    status_counts = crud.payout_status_counts(db, run_id)
    method_options = crud.payment_methods_for_run(db, run_id)
    frequency_options = crud.frequencies_for_run(db, run_id)

    return templates.TemplateResponse(
        "schedules/detail.html",
        {
            "request": request,
            "user": user,
            "run": run,
            "payouts": payouts,
            "payout_total": payout_total,
            "validations": validations,
            "frequency_counts": frequency_counts,
            "base_filename": base_filename,
            "summary": summary,
            "status_counts": status_counts,
            "filters": {
                "code": code_filter or "",
                "frequency": frequency_filter or "",
                "payment_method": method_filter or "",
                "status": status_filter or "",
                "pay_date": pay_date.strip() if pay_date else "",
            },
            "status_options": PAYOUT_STATUS_ENUM,
            "payment_methods": method_options,
            "frequency_options": frequency_options,
            "code_options": code_options,
            "pay_date_options": pay_date_options,
        },
    )


@router.post("/{run_id}/delete")
def delete_schedule_run(run_id: int, db: Session = Depends(get_session), user: User = Depends(get_admin_user)):
    run = crud.get_schedule_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Schedule run not found")

    crud.delete_schedule_run(db, run)
    return RedirectResponse(url="/schedules", status_code=303)


@router.get("/{run_id}/download/{file_type}")
def download_export(run_id: int, file_type: str, db: Session = Depends(get_session), user: User = Depends(get_current_user)):
    run = crud.get_schedule_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Schedule run not found")

    base_filename = f"pay_schedule_{run.target_year:04d}_{run.target_month:02d}_run{run.id}"
    export_dir = Path(run.export_path)

    # For schedule_csv, generate from database payouts to include status
    if file_type == "schedule_csv":
        # Build CSV from payouts in database (includes status)
        payouts = sorted(run.payouts, key=lambda p: (p.pay_date, p.code))
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow([
            "Pay Date",
            "Code",
            "Working Name",
            "Method",
            "Frequency",
            "Amount",
            "Status",
            "Crypto Wallet",
            "Notes & Actions",
        ])
        
        # Write data rows
        for payout in payouts:
            model_wallet = ""
            if payout.model and getattr(payout.model, "crypto_wallet", None):
                model_wallet = payout.model.crypto_wallet

            writer.writerow([
                format_display_date(payout.pay_date),
                payout.code or "",
                payout.working_name or "",
                payout.payment_method or "",
                payout.payment_frequency.title() if payout.payment_frequency else "",
                f"{payout.amount:.2f}" if payout.amount else "",
                payout.status.replace("_", " ").title() if payout.status else "",
                model_wallet,
                payout.notes or "",
            ])
        
        # Return as streaming response
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={base_filename}.csv"},
        )

    # For other file types, use the pre-generated exports
    file_mapping = {
        "xlsx": export_dir / f"{base_filename}.xlsx",
        "models_csv": export_dir / f"{base_filename}_models.csv",
        "validation_csv": export_dir / f"{base_filename}_validation.csv",
    }

    path = file_mapping.get(file_type)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Requested file not available")

    return FileResponse(path, filename=path.name)


@router.post("/{run_id}/payouts/{payout_id}/note")
def update_payout_record(
    run_id: int,
    payout_id: int,
    notes: str = Form(""),
    status: str = Form("not_paid"),
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    payout = crud.get_payout(db, payout_id)
    if not payout or payout.schedule_run_id != run_id:
        raise HTTPException(status_code=404, detail="Payout not found")

    status_value = status.strip().lower()
    if status_value not in PAYOUT_STATUS_ENUM:
        raise HTTPException(status_code=400, detail="Invalid payout status")

    trimmed = notes.strip()
    crud.update_payout(db, payout, trimmed if trimmed else None, status_value)
    return RedirectResponse(url=f"/schedules/{run_id}", status_code=303)


@router.post("/{run_id}/payouts/bulk-update")
def bulk_update_payouts(
    run_id: int,
    payout_ids: str = Form(""),
    status: str = Form("not_paid"),
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Bulk update status for multiple payouts."""
    run = crud.get_schedule_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Schedule run not found")

    status_value = status.strip().lower()
    if status_value not in PAYOUT_STATUS_ENUM:
        raise HTTPException(status_code=400, detail="Invalid payout status")

    # Parse comma-separated payout IDs
    if not payout_ids.strip():
        return RedirectResponse(url=f"/schedules/{run_id}", status_code=303)
    
    try:
        ids = [int(id.strip()) for id in payout_ids.split(",") if id.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payout IDs")

    # Update each payout with new status, preserving existing notes
    for payout_id in ids:
        payout = crud.get_payout(db, payout_id)
        if payout and payout.schedule_run_id == run_id:
            # Preserve existing notes, only update status
            crud.update_payout(db, payout, payout.notes, status_value)
    
    return RedirectResponse(url=f"/schedules/{run_id}", status_code=303)

