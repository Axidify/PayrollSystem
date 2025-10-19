"""Routes for managing models."""
from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from app import crud
from app.auth import User
from app.database import get_session
from app.dependencies import templates
from app.models import FREQUENCY_ENUM, STATUS_ENUM, Payout
from app.routers.auth import get_current_user, get_admin_user
from app.schemas import ModelCreate, ModelUpdate
from app.importers.excel_importer import ImportOptions, RunOptions, import_from_excel

router = APIRouter(prefix="/models", tags=["Models"])


def _normalize_filters(
    code: str | None,
    status: str | None,
    frequency: str | None,
    payment_method: str | None,
) -> tuple[str | None, str | None, str | None, str | None]:
    code_filter = code.strip() if code else None
    status_filter = status.title() if status else None
    frequency_filter = frequency.lower() if frequency else None
    method_filter = payment_method.strip() if payment_method else None
    return code_filter, status_filter, frequency_filter, method_filter


def _build_model_list_context(
    request: Request,
    user: User,
    db: Session,
    code: str | None,
    status: str | None,
    frequency: str | None,
    payment_method: str | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    code_filter, status_filter, frequency_filter, method_filter = _normalize_filters(
        code, status, frequency, payment_method
    )

    models = crud.list_models(
        db,
        code=code_filter,
        status=status_filter,
        frequency=frequency_filter,
        payment_method=method_filter,
    )

    totals_map = crud.total_paid_by_model(db, [model.id for model in models])
    total_paid_sum = sum(totals_map.values(), Decimal("0")) if totals_map else Decimal("0")
    payment_methods = crud.list_payment_methods(db)

    export_params: dict[str, str] = {}
    if code_filter:
        export_params["code"] = code_filter
    if status_filter:
        export_params["status"] = status_filter
    if frequency_filter:
        export_params["frequency"] = frequency_filter
    if method_filter:
        export_params["payment_method"] = method_filter

    export_url = "/models/export"
    if export_params:
        export_url = f"{export_url}?{urlencode(export_params)}"

    context: dict[str, Any] = {
        "request": request,
        "user": user,
        "models": models,
        "filters": {
            "code": code_filter or "",
            "status": status_filter or "",
            "frequency": frequency_filter or "",
            "payment_method": method_filter or "",
        },
        "payment_methods": payment_methods,
        "status_options": STATUS_ENUM,
        "frequency_options": FREQUENCY_ENUM,
        "totals_map": totals_map,
        "total_paid_sum": total_paid_sum,
        "export_url": export_url,
    }
    if extra:
        context.update(extra)
    return context


@router.get("/")
def list_models(
    request: Request,
    code: str | None = None,
    status: str | None = None,
    frequency: str | None = None,
    payment_method: str | None = None,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    context = _build_model_list_context(request, user, db, code, status, frequency, payment_method)
    return templates.TemplateResponse("models/list.html", context)


@router.get("/export")
def export_models_csv(
    code: str | None = None,
    status: str | None = None,
    frequency: str | None = None,
    payment_method: str | None = None,
    include_payments: str | None = None,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    Export models to CSV.
    If include_payments=true, includes payment history (paid payouts) for each model.
    """
    code_filter, status_filter, frequency_filter, method_filter = _normalize_filters(
        code, status, frequency, payment_method
    )

    models = crud.list_models(
        db,
        code=code_filter,
        status=status_filter,
        frequency=frequency_filter,
        payment_method=method_filter,
    )

    totals_map = crud.total_paid_by_model(db, [model.id for model in models])
    
    # Check if user wants to include payment history
    include_payment_history = include_payments and include_payments.lower() == "true"

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    
    # Header row - always include payment columns to match /schedules/ view
    writer.writerow(
        [
            "Code",
            "Status",
            "Real Name",
            "Working Name",
            "Start Date",
            "Payment Method",
            "Payment Frequency",
            "Monthly Amount",
            "Crypto Wallet",
            "Pay Date",
            "Amount",
            "Status (Payment)",
            "Notes",
        ]
    )

    for model in models:
        start_date_value = model.start_date.strftime("%m/%d/%Y") if model.start_date else ""
        
        # Get paid payouts for this model
        paid_payouts = crud.get_paid_payouts_for_model(db, model.id)
        
        if paid_payouts:
            # Write one row per payment
            for payout in paid_payouts:
                pay_date_value = payout.pay_date.strftime("%m/%d/%Y") if payout.pay_date else ""
                writer.writerow(
                    [
                        model.code,
                        model.status,
                        model.real_name,
                        model.working_name,
                        start_date_value,
                        model.payment_method,
                        model.payment_frequency,
                        f"{model.amount_monthly:.2f}",
                        model.crypto_wallet or "",
                        pay_date_value,
                        f"{payout.amount:.2f}",
                        payout.status,
                        payout.notes or "",
                    ]
                )
        else:
            # Write model row with empty payment fields if no payouts
            writer.writerow(
                [
                    model.code,
                    model.status,
                    model.real_name,
                    model.working_name,
                    start_date_value,
                    model.payment_method,
                    model.payment_frequency,
                    f"{model.amount_monthly:.2f}",
                    model.crypto_wallet or "",
                    "",
                    "",
                    "",
                    "",
                ]
            )

    buffer.seek(0)
    filename_parts = ["models_export"]
    if code_filter:
        filename_parts.append(code_filter.replace(" ", "_"))
    if include_payment_history:
        filename_parts.append("with_payments")
    filename = "_".join(filename_parts) + ".csv"

    headers = {
        "Content-Disposition": f"attachment; filename={filename}",
    }

    return StreamingResponse(iter([buffer.getvalue()]), media_type="text/csv", headers=headers)


@router.get("/new")
def new_model_form(request: Request, user: User = Depends(get_admin_user)):
    return templates.TemplateResponse(
        "models/form.html",
        {
            "request": request,
            "user": user,
            "action": "create",
        },
    )


@router.post("/new")
def create_model(
    request: Request,
    status: str = Form(...),
    code: str = Form(...),
    real_name: str = Form(...),
    working_name: str = Form(...),
    start_date: str = Form(...),
    payment_method: str = Form(...),
    payment_frequency: str = Form(...),
    amount_monthly: str = Form(...),
    crypto_wallet: str = Form(None),
    db: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    payload = ModelCreate(
        status=status,
        code=code,
        real_name=real_name,
        working_name=working_name,
        start_date=start_date,
        payment_method=payment_method,
        payment_frequency=payment_frequency,
        amount_monthly=amount_monthly,
        crypto_wallet=crypto_wallet if crypto_wallet else None,
    )
    if crud.get_model_by_code(db, payload.code):
        raise HTTPException(status_code=400, detail="Model code already exists.")
    crud.create_model(db, payload)
    return RedirectResponse(url="/models", status_code=303)


@router.get("/{model_id}")
def view_model(model_id: int, request: Request, db: Session = Depends(get_session), user: User = Depends(get_current_user)):
    """View model details in read-only mode."""
    model = crud.get_model(db, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    
    # Get total paid amount for this model (from scheduled payouts)
    total_paid = crud.total_paid_by_model(db, [model.id]).get(model.id, Decimal("0"))
    
    # Get paid payouts (unified source of truth for payment history)
    paid_payouts = crud.get_paid_payouts_for_model(db, model_id)
    
    return templates.TemplateResponse(
        "models/view.html",
        {
            "request": request,
            "user": user,
            "model": model,
            "total_paid": total_paid,
            "paid_payouts": paid_payouts,
        },
    )


@router.get("/{model_id}/edit")
def edit_model_form(model_id: int, request: Request, db: Session = Depends(get_session), user: User = Depends(get_admin_user)):
    model = crud.get_model(db, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    return templates.TemplateResponse(
        "models/form.html",
        {
            "request": request,
            "user": user,
            "action": "edit",
            "model": model,
        },
    )


@router.post("/{model_id}/edit")
def update_model(
    model_id: int,
    request: Request,
    status: str = Form(...),
    code: str = Form(...),
    real_name: str = Form(...),
    working_name: str = Form(...),
    start_date: str = Form(...),
    payment_method: str = Form(...),
    payment_frequency: str = Form(...),
    amount_monthly: str = Form(...),
    crypto_wallet: str = Form(None),
    db: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    model = crud.get_model(db, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    payload = ModelUpdate(
        status=status,
        code=code,
        real_name=real_name,
        working_name=working_name,
        start_date=start_date,
        payment_method=payment_method,
        payment_frequency=payment_frequency,
        amount_monthly=amount_monthly,
        crypto_wallet=crypto_wallet if crypto_wallet else None,
    )

    existing = crud.get_model_by_code(db, payload.code)
    if existing and existing.id != model.id:
        raise HTTPException(status_code=400, detail="Another model already uses this code.")

    crud.update_model(db, model, payload)
    return RedirectResponse(url="/models", status_code=303)



@router.post("/{model_id}/delete")
def delete_model(model_id: int, db: Session = Depends(get_session), user: User = Depends(get_admin_user)):
    model = crud.get_model(db, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    crud.delete_model(db, model)
    return RedirectResponse(url="/models", status_code=303)


@router.post("/import")
async def import_models_excel(
    request: Request,
    excel_file: UploadFile = File(...),
    target_month: str | None = Form(None),
    schedule_run_id: str | None = Form(None),
    currency: str = Form("USD"),
    export_dir: str = Form("exports"),
    update_existing: str | None = Form(None),
    model_sheet: str = Form("Models"),
    payout_sheet: str = Form("Payouts"),
    auto_runs: str | None = Form(None),
    db: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    extra_context: dict[str, Any] = {}
    try:
        contents = await excel_file.read()
        if not contents:
            raise ValueError("The uploaded file is empty.")

        filename = (excel_file.filename or "").lower()
        if not filename.endswith((".xlsx", ".xlsm", ".xls")):
            raise ValueError("Upload an Excel file with the .xlsx extension.")

        auto_generate_runs = auto_runs is not None
        extra_context["import_auto_runs"] = auto_generate_runs

        run_id: int | None = None
        create_schedule_run = False
        target_year_int: int | None = None
        target_month_int: int | None = None

        if auto_generate_runs:
            create_schedule_run = True
        else:
            if schedule_run_id:
                try:
                    run_id = int(schedule_run_id)
                except ValueError as exc:
                    raise ValueError("Schedule run id must be a number.") from exc

            create_schedule_run = run_id is None
            if create_schedule_run:
                if not target_month:
                    raise ValueError("Select a target month to create a schedule run.")
                try:
                    year_str, month_str = target_month.split("-")
                    target_year_int = int(year_str)
                    target_month_int = int(month_str)
                except ValueError as exc:
                    raise ValueError("Target month must be in YYYY-MM format.") from exc

        import_options = ImportOptions(
            model_sheet=model_sheet or "Models",
            payout_sheet=payout_sheet or "Payouts",
            update_existing=update_existing is not None,
        )
        run_options = RunOptions(
            schedule_run_id=run_id,
            create_schedule_run=create_schedule_run,
            target_year=target_year_int,
            target_month=target_month_int,
            currency=(currency or "USD").strip() or "USD",
            export_dir=(export_dir or "exports").strip() or "exports",
            auto_generate_runs=auto_generate_runs,
        )

        summary = import_from_excel(db, contents, import_options, run_options)
        db.commit()
        db.expire_all()
        extra_context["import_summary"] = summary
    except Exception as exc:
        db.rollback()
        extra_context["import_error"] = str(exc)

    context = _build_model_list_context(request, user, db, None, None, None, None, extra_context)
    return templates.TemplateResponse("models/list.html", context)

