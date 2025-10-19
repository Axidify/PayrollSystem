"""Routes for managing models."""
from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

from dateutil import parser as date_parser
from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from app import crud
from app.auth import User
from app.database import get_session
from app.dependencies import templates
from app.models import FREQUENCY_ENUM, STATUS_ENUM
from app.routers.auth import get_current_user, get_admin_user
from app.schemas import ModelCreate, ModelUpdate

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

    return templates.TemplateResponse(
        "models/list.html",
        {
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
        },
    )


@router.get("/export")
def export_models_csv(
    code: str | None = None,
    status: str | None = None,
    frequency: str | None = None,
    payment_method: str | None = None,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
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

    buffer = io.StringIO()
    writer = csv.writer(buffer)
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
            "Total Paid",
        ]
    )

    for model in models:
        start_date_value = model.start_date.strftime("%m/%d/%Y") if model.start_date else ""
        total_paid = totals_map.get(model.id, Decimal("0"))
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
                f"{total_paid:.2f}",
            ]
        )

    buffer.seek(0)
    filename_parts = ["models_export"]
    if code_filter:
        filename_parts.append(code_filter.replace(" ", "_"))
    filename = "_".join(filename_parts) + ".csv"

    headers = {
        "Content-Disposition": f"attachment; filename={filename}",
    }

    return StreamingResponse(iter([buffer.getvalue()]), media_type="text/csv", headers=headers)


@router.post("/import")
async def import_models_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    """Import models from a CSV file."""
    
    if not file.filename or not file.filename.lower().endswith('.csv'):
        raise HTTPException(status_code=400, detail="File must be a CSV file")
    
    try:
        # Read and parse CSV
        content = await file.read()
        text_stream = io.StringIO(content.decode('utf-8'))
        reader = csv.DictReader(text_stream)
        
        if not reader.fieldnames:
            raise HTTPException(status_code=400, detail="CSV file is empty")
        
        # Normalize field names
        required_fields = {'status', 'code', 'real_name', 'working_name', 'start_date', 
                          'payment_method', 'payment_frequency', 'amount_monthly'}
        field_names_lower = {f.lower(): f for f in reader.fieldnames}
        
        # Check for required fields
        missing = required_fields - set(field_names_lower.keys())
        if missing:
            raise HTTPException(
                status_code=400, 
                detail=f"Missing required columns: {', '.join(sorted(missing))}"
            )
        
        # Import models
        imported_count = 0
        errors = []
        
        for row_num, row in enumerate(reader, start=2):
            try:
                # Extract values (handle both lowercase and original case)
                code = row.get('code') or row.get('Code')
                status_val = row.get('status') or row.get('Status')
                real_name = row.get('real_name') or row.get('Real Name')
                working_name = row.get('working_name') or row.get('Working Name')
                start_date_str = row.get('start_date') or row.get('Start Date')
                payment_method = row.get('payment_method') or row.get('Payment Method')
                payment_frequency = row.get('payment_frequency') or row.get('Payment Frequency')
                amount_monthly_str = row.get('amount_monthly') or row.get('Amount Monthly')
                crypto_wallet = row.get('crypto_wallet') or row.get('Crypto Wallet')
                
                # Validate required fields
                if not all([code, status_val, real_name, working_name, start_date_str, 
                           payment_method, payment_frequency, amount_monthly_str]):
                    errors.append(f"Row {row_num}: Missing required field")
                    continue
                
                # Normalize and validate
                code = code.strip()
                status_val = status_val.strip().title()
                if status_val not in ['Active', 'Inactive']:
                    errors.append(f"Row {row_num}: Invalid status '{status_val}'. Must be 'Active' or 'Inactive'")
                    continue
                
                # Parse date
                try:
                    start_date_obj = date.fromisoformat(start_date_str.strip())
                except ValueError:
                    errors.append(f"Row {row_num}: Invalid date format '{start_date_str}'. Use YYYY-MM-DD")
                    continue
                
                # Parse amount
                try:
                    amount = Decimal(amount_monthly_str.strip())
                    if amount <= 0:
                        errors.append(f"Row {row_num}: Amount must be > 0")
                        continue
                except (ValueError, TypeError):
                    errors.append(f"Row {row_num}: Invalid amount '{amount_monthly_str}'")
                    continue
                
                # Validate frequency
                payment_frequency_val = payment_frequency.strip().lower()
                if payment_frequency_val not in ['weekly', 'biweekly', 'monthly']:
                    errors.append(f"Row {row_num}: Invalid frequency '{payment_frequency}'. Must be weekly, biweekly, or monthly")
                    continue
                
                # Check if code already exists
                existing = crud.get_model_by_code(db, code)
                if existing:
                    errors.append(f"Row {row_num}: Model code '{code}' already exists")
                    continue
                
                # Create model
                model_data = ModelCreate(
                    status=status_val,
                    code=code,
                    real_name=real_name.strip(),
                    working_name=working_name.strip(),
                    start_date=start_date_obj,
                    payment_method=payment_method.strip(),
                    payment_frequency=payment_frequency_val,
                    amount_monthly=amount,
                    crypto_wallet=crypto_wallet.strip() if crypto_wallet else None,
                )
                
                crud.create_model(db, model_data)
                imported_count += 1
            
            except Exception as e:
                errors.append(f"Row {row_num}: {str(e)}")
        
        # Prepare response message
        if imported_count == 0 and errors:
            raise HTTPException(status_code=400, detail=f"Import failed. Errors: {'; '.join(errors[:5])}")
        
        # Return redirect with success message
        message = f"Successfully imported {imported_count} model{'s' if imported_count != 1 else ''}"
        if errors:
            message += f". {len(errors)} row(s) had errors"
        
        return RedirectResponse(url="/models?message=" + message, status_code=303)
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing file: {str(e)}")


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
    
    # Get payment history
    payments = crud.list_payments_for_model(db, model_id)
    
    return templates.TemplateResponse(
        "models/view.html",
        {
            "request": request,
            "user": user,
            "model": model,
            "total_paid": total_paid,
            "payments": payments,
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


@router.post("/{model_id}/payments/import")
async def import_payment_history(
    model_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    """Import payment history from CSV file."""
    
    model = crud.get_model(db, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    
    if not file.filename or not file.filename.lower().endswith('.csv'):
        raise HTTPException(status_code=400, detail="File must be a CSV file")
    
    try:
        # Read and parse CSV
        content = await file.read()
        text_stream = io.StringIO(content.decode('utf-8'))
        reader = csv.DictReader(text_stream)
        
        if not reader.fieldnames:
            raise HTTPException(status_code=400, detail="CSV file is empty")
        
        # Required fields
        required_fields = {'payment_date', 'payment_to', 'amount'}
        field_names_lower = {f.lower(): f for f in reader.fieldnames}
        
        # Check for required fields
        missing = required_fields - set(field_names_lower.keys())
        if missing:
            raise HTTPException(
                status_code=400, 
                detail=f"Missing required columns: {', '.join(sorted(missing))}"
            )
        
        # Import payments
        imported_count = 0
        errors = []
        
        for row_num, row in enumerate(reader, start=2):
            try:
                # Extract values
                payment_date_str = row.get('payment_date') or row.get('Payment Date')
                payment_to = row.get('payment_to') or row.get('Payment to')
                amount_str = row.get('amount') or row.get('Amount')
                notes = row.get('notes') or row.get('Notes')
                
                # Validate required fields
                if not all([payment_date_str, payment_to, amount_str]):
                    errors.append(f"Row {row_num}: Missing required field")
                    continue
                
                # Parse date
                try:
                    payment_date_obj = date.fromisoformat(payment_date_str.strip())
                except ValueError:
                    # Try parsing common formats
                    try:
                        from dateutil import parser as date_parser
                        payment_date_obj = date_parser.parse(payment_date_str).date()
                    except:
                        errors.append(f"Row {row_num}: Invalid date format '{payment_date_str}'. Use YYYY-MM-DD or MM/DD/YYYY")
                        continue
                
                # Parse amount
                try:
                    amount_str_clean = amount_str.strip().replace('$', '').replace(',', '')
                    amount = Decimal(amount_str_clean)
                    if amount <= 0:
                        errors.append(f"Row {row_num}: Amount must be > 0")
                        continue
                except (ValueError, TypeError):
                    errors.append(f"Row {row_num}: Invalid amount '{amount_str}'")
                    continue
                
                # Create payment
                crud.create_payment(
                    db,
                    model_id=model_id,
                    payment_date=payment_date_obj,
                    payment_to=payment_to.strip(),
                    amount=amount,
                    notes=notes.strip() if notes else None,
                )
                imported_count += 1
            
            except Exception as e:
                errors.append(f"Row {row_num}: {str(e)}")
        
        # Prepare response message
        if imported_count == 0 and errors:
            raise HTTPException(status_code=400, detail=f"Import failed. Errors: {'; '.join(errors[:5])}")
        
        # Return redirect with success
        return RedirectResponse(url=f"/models/{model_id}", status_code=303)
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing file: {str(e)}")


@router.post("/{model_id}/payments/{payment_id}/delete")
def delete_payment(
    model_id: int,
    payment_id: int,
    db: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    """Delete a payment record."""
    
    model = crud.get_model(db, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    
    success = crud.delete_payment(db, payment_id)
    if not success:
        raise HTTPException(status_code=404, detail="Payment not found")
    
    return RedirectResponse(url=f"/models/{model_id}", status_code=303)


@router.post("/{model_id}/delete")
def delete_model(model_id: int, db: Session = Depends(get_session), user: User = Depends(get_admin_user)):
    model = crud.get_model(db, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    crud.delete_model(db, model)
    return RedirectResponse(url="/models", status_code=303)
