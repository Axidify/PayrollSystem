"""Routes for managing schedule runs."""
from __future__ import annotations

import calendar
import json
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import crud
from app.auth import User
from app.database import get_session
from app.dependencies import templates
from app.models import PAYOUT_STATUS_ENUM
from app.routers.auth import get_current_user, get_admin_user
from app.services import PayrollService

router = APIRouter(prefix="/schedules", tags=["Schedules"])

DEFAULT_EXPORT_DIR = Path("exports")


@router.get("/")
def list_runs(
    request: Request,
    month: str | None = None,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    normalized_month = month.strip() if month else ""
    year_filter: int | None = None
    month_filter: int | None = None

    if normalized_month:
        try:
            year_str, month_str = normalized_month.split("-")
            year_filter = int(year_str)
            month_filter = int(month_str)
        except ValueError:
            raise HTTPException(status_code=400, detail="Month must be in YYYY-MM format.")

    runs = crud.list_schedule_runs(db, target_year=year_filter, target_month=month_filter)
    for run in runs:
        try:
            run.frequency_counts = json.loads(run.summary_frequency_counts)
        except json.JSONDecodeError:
            run.frequency_counts = {}
        # Recalculate models_paid dynamically to reflect actual paid status in database
        # (the stored summary_models_paid counts all scheduled models, not just paid ones)
        summary = crud.run_payment_summary(db, run.id)
        run.summary_models_paid = summary.get("paid_models", 0)
    return templates.TemplateResponse(
        "schedules/list.html",
        {
            "request": request,
            "user": user,
            "runs": runs,
            "filters": {
                "month": normalized_month,
            },
        },
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

    file_mapping = {
        "xlsx": export_dir / f"{base_filename}.xlsx",
        "schedule_csv": export_dir / f"{base_filename}.csv",
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
