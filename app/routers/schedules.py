"""Routes for managing schedule runs."""
from __future__ import annotations

import calendar
import csv
import io
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import crud
from app.auth import User
from app.database import get_session
from app.dependencies import templates
from app.models import PAYOUT_STATUS_ENUM, Payout
from app.routers.auth import get_current_user, get_admin_user
from app.services import PayrollService

router = APIRouter(prefix="/schedules", tags=["Schedules"])

DEFAULT_EXPORT_DIR = Path("exports")


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
    cycle_label = datetime(run_obj.target_year, run_obj.target_month, 1).strftime("%b %Y")

    return {
        "id": run_obj.id,
        "cycle": cycle_label,
        "created": run_obj.created_at.strftime("%b %d, %Y"),
        "models_paid": getattr(run_obj, "summary_models_paid", 0) or 0,
        "total": total_value,
        "paid": paid_total_value,
        "outstanding": outstanding,
        "status": status,
        "status_variant": status_variant,
        "frequency_counts": frequency_counts,
        "currency": getattr(run_obj, "currency", "USD"),
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

    zero = Decimal("0")
    runs_for_year: list = []
    available_years = sorted({run.target_year for run in all_runs}, reverse=True)

    for run in all_runs:
        if run.target_year != target_year:
            continue

        try:
            run.frequency_counts = json.loads(run.summary_frequency_counts)
        except json.JSONDecodeError:
            run.frequency_counts = {}

        summary = crud.run_payment_summary(db, run.id)
        run.summary_models_paid = summary.get("paid_models", 0)
        run.paid_total = summary.get("paid_total", Decimal("0"))
        run.unpaid_total = summary.get("unpaid_total", Decimal("0"))
        run.frequency_counts = _compute_frequency_counts(db, run.id)
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


@router.get("/")
def list_runs(
    request: Request,
    month: str | None = None,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
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

    selected_key = None
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
        label = datetime(year_value, month_value, 1).strftime("%B %Y")
        month_options.append(
            {
                "value": value,
                "label": label,
                "run_count": len(grouped_runs[(year_value, month_value)]),
            }
        )

    if month_candidate and month_candidate not in grouped_runs:
        value = f"{month_candidate[0]:04d}-{month_candidate[1]:02d}"
        label = datetime(month_candidate[0], month_candidate[1], 1).strftime("%B %Y")
        month_options.insert(0, {"value": value, "label": label, "run_count": 0})

    selected_month_value = ""
    selected_month_label = ""
    if selected_key:
        selected_month_value = f"{selected_key[0]:04d}-{selected_key[1]:02d}"
        selected_month_label = datetime(selected_key[0], selected_key[1], 1).strftime("%B %Y")

    for option in month_options:
        option["is_selected"] = option["value"] == selected_month_value

    today = date.today()
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
        month_label = datetime(current_year, month_index, 1).strftime("%b")
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

    return templates.TemplateResponse(
        "schedules/list.html",
        {
            "request": request,
            "user": user,
            "runs": selected_runs,
            "filters": {
                "month": selected_month_value,
            },
            "month_options": month_options,
            "selected_month_label": selected_month_label,
            "monthly_summary": monthly_summary,
            "monthly_frequency": monthly_frequency,
            "has_runs": bool(all_runs),
            "recent_runs": recent_cards,
            "selected_run_cards": selected_run_cards,
            "year_overview": year_overview,
            "view_all_url": f"/schedules/all?year={current_year}",
            "table_view_url": f"/schedules/all-table?year={current_year}",
        },
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
        label = datetime(target_year, month_index, 1).strftime("%b %Y")
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
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    today = date.today()
    target_year = year or today.year

    runs_for_year, available_years, all_runs = _prepare_runs_by_year(db, target_year)

    zero = Decimal("0")

    run_ids = [run.id for run in runs_for_year]
    total_payout = sum((getattr(run, "summary_total_payout", zero) or zero) for run in runs_for_year)
    paid_total = sum((getattr(run, "paid_total", zero) or zero) for run in runs_for_year)
    unpaid_total = sum((getattr(run, "unpaid_total", zero) or zero) for run in runs_for_year)
    models_paid = _count_unique_models(db, run_ids)

    currency = None
    if runs_for_year:
        currency = getattr(runs_for_year[0], "currency", None)
    elif all_runs:
        currency = getattr(all_runs[0], "currency", None)

    year_summary = {
        "run_count": len(runs_for_year),
        "total_payout": total_payout,
        "paid_total": paid_total,
        "unpaid_total": unpaid_total,
        "models_paid": models_paid,
        "currency": currency or "USD",
    }

    year_buttons = []
    for yr in available_years:
        year_buttons.append(
            {
                "label": yr,
                "url": f"/schedules/all-table?year={yr}",
                "is_selected": yr == target_year,
            }
        )

    return templates.TemplateResponse(
        "schedules/all_table.html",
        {
            "request": request,
            "user": user,
            "year": target_year,
            "runs": runs_for_year,
            "year_summary": year_summary,
            "year_buttons": year_buttons,
            "has_previous_years": len(available_years) > 1,
            "card_view_url": f"/schedules/all?year={target_year}",
            "export_url": f"/schedules/all-table/export?year={target_year}",
        },
    )


@router.get("/all-table/export")
def export_runs_all_table(
    year: int = Query(default=None, description="Target year to export"),
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    today = date.today()
    target_year = year or today.year

    runs_for_year, _, all_runs = _prepare_runs_by_year(db, target_year)

    zero = Decimal("0")

    run_ids = [run.id for run in runs_for_year]

    currency = None
    if runs_for_year:
        currency = getattr(runs_for_year[0], "currency", None)
    elif all_runs:
        currency = getattr(all_runs[0], "currency", None)
    currency = currency or "USD"

    rows: list[dict[str, object]] = []
    for run in runs_for_year:
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
        dataframe.to_excel(writer, sheet_name=f"Runs_{target_year}", index=False)

    buffer.seek(0)
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
        value = candidate_date.strftime("%m/%d/%Y")
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
            "validations": validations,
            "frequency_counts": frequency_counts,
            "base_filename": base_filename,
            "export_dir": export_path,
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
            "Notes & Actions",
        ])
        
        # Write data rows
        for payout in payouts:
            writer.writerow([
                payout.pay_date.strftime("%m/%d/%Y") if payout.pay_date else "",
                payout.code or "",
                payout.working_name or "",
                payout.payment_method or "",
                payout.payment_frequency.title() if payout.payment_frequency else "",
                f"{payout.amount:.2f}" if payout.amount else "",
                payout.status.replace("_", " ").title() if payout.status else "",
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

