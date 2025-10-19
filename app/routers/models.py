"""Routes for managing models."""
from __future__ import annotations

import csv
import io
import json
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

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


@router.post("/import")
async def import_models_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    """Import models from a CSV file with optional payment data and duplicate detection."""
    
    if not file.filename or not file.filename.lower().endswith('.csv'):
        raise HTTPException(status_code=400, detail="File must be a CSV file")
    
    try:
        # Read and parse CSV
        content = await file.read()
        text_stream = io.StringIO(content.decode('utf-8'))
        reader = csv.DictReader(text_stream)
        
        if not reader.fieldnames:
            raise HTTPException(status_code=400, detail="CSV file is empty")
        
        # Normalize field names - map to exact column names from export
        required_fields = {'status', 'code', 'real_name', 'working_name', 'start_date', 
                          'payment_method', 'payment_frequency', 'monthly_amount', 'crypto_wallet'}
        optional_payment_fields = {'pay_date', 'amount', 'status (payment)'}
        field_names_lower = {f.lower().replace(' ', '_').replace('(', '').replace(')', ''): f for f in reader.fieldnames}
        
        # Check for required fields
        missing = required_fields - set(field_names_lower.keys())
        if missing:
            raise HTTPException(
                status_code=400, 
                detail=f"Missing required columns: {', '.join(sorted(missing))}"
            )
        
        # Check if CSV has payment data (all payment fields must be present together)
        has_payment_data = all(pf in field_names_lower for pf in optional_payment_fields)
        
        # Parse all rows
        valid_rows = []
        errors = []
        payout_duplicates = {}  # code -> [(row_num, existing_payout_id), ...]
        
        for row_num, row in enumerate(reader, start=2):
            try:
                # Extract model values - handle case and space variations
                code = (row.get('code') or row.get('Code') or '').strip()
                status_val = (row.get('status') or row.get('Status') or '').strip()
                real_name = (row.get('real_name') or row.get('Real Name') or '').strip()
                working_name = (row.get('working_name') or row.get('Working Name') or '').strip()
                start_date_str = (row.get('start_date') or row.get('Start Date') or '').strip()
                payment_method = (row.get('payment_method') or row.get('Payment Method') or '').strip()
                payment_frequency = (row.get('payment_frequency') or row.get('Payment Frequency') or '').strip()
                amount_monthly_str = (row.get('monthly_amount') or row.get('Monthly Amount') or '').strip()
                crypto_wallet = (row.get('crypto_wallet') or row.get('Crypto Wallet') or row.get('Crypto Wa') or '').strip()
                
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
                
                # Extract and validate payment data if present
                payout_data = None
                if has_payment_data:
                    try:
                        pay_date_str = (row.get('pay_date') or row.get('Pay Date') or '').strip()
                        payment_amount_str = (row.get('amount') or row.get('Amount') or '').strip()
                        payment_status_str = (row.get('status_payment') or row.get('Status (Payment)') or row.get('Status Payment') or '').strip()
                        payment_notes_str = (row.get('notes') or row.get('Notes') or '').strip()
                        
                        if pay_date_str and payment_amount_str and payment_status_str:
                            # Parse payment date
                            pay_date_obj = date.fromisoformat(pay_date_str.strip())
                            
                            # Parse payment amount
                            payment_amount = Decimal(payment_amount_str.strip())
                            if payment_amount <= 0:
                                errors.append(f"Row {row_num}: Payment amount must be > 0")
                                continue
                            
                            # Validate payment status
                            payment_status = payment_status_str.strip().lower()
                            if payment_status not in ['paid', 'on_hold', 'not_paid']:
                                errors.append(f"Row {row_num}: Invalid payment status '{payment_status_str}'. Must be 'paid', 'on_hold', or 'not_paid'")
                                continue
                            
                            payout_data = {
                                'pay_date': pay_date_obj,
                                'amount': payment_amount,
                                'status': payment_status,
                                'notes': payment_notes_str.strip() if payment_notes_str else None,
                            }
                    except ValueError as e:
                        errors.append(f"Row {row_num}: Invalid payment data - {str(e)}")
                        continue
                
                # Store valid parsed row
                parsed_row = {
                    'row_num': row_num,
                    'code': code,
                    'status': status_val,
                    'real_name': real_name.strip(),
                    'working_name': working_name.strip(),
                    'start_date': start_date_obj,
                    'payment_method': payment_method.strip(),
                    'payment_frequency': payment_frequency_val,
                    'amount_monthly': amount,
                    'crypto_wallet': crypto_wallet.strip() if crypto_wallet else None,
                    'payout_data': payout_data,
                }
                valid_rows.append(parsed_row)
                
                # Check for duplicate payouts with existing data
                if payout_data:
                    # Get model to check its ID
                    existing_model = crud.get_model_by_code(db, code)
                    if existing_model:
                        duplicates = crud.find_duplicate_payouts(
                            db,
                            existing_model.id,
                            payout_data['pay_date'],
                            payout_data['amount'],
                            payout_data['status'],
                        )
                        if duplicates:
                            key = f"{code}_{payout_data['pay_date']}_{payout_data['amount']}_{payout_data['status']}"
                            if key not in payout_duplicates:
                                payout_duplicates[key] = []
                            for dup in duplicates:
                                payout_duplicates[key].append({
                                    'row_num': row_num,
                                    'existing_payout_id': dup.id,
                                    'csv_data': payout_data,
                                    'existing_data': {
                                        'id': dup.id,
                                        'pay_date': dup.pay_date.isoformat(),
                                        'amount': str(dup.amount),
                                        'status': dup.status,
                                        'notes': dup.notes,
                                    }
                                })
            
            except Exception as e:
                errors.append(f"Row {row_num}: {str(e)}")
        
        # If there are payout duplicates or errors, show resolution UI
        if payout_duplicates or errors:
            temp_data = {
                'valid_rows': valid_rows,
                'payout_duplicates': payout_duplicates,
                'errors': errors,
                'has_payment_data': has_payment_data,
            }
            # Store as JSON file in temp directory
            temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, dir='data')
            json.dump(temp_data, temp_file, default=str)
            temp_file.close()
            
            batch_id = Path(temp_file.name).stem
            return RedirectResponse(url=f"/models/import/resolve?batch={batch_id}", status_code=303)
        
        # No duplicates - proceed with import
        imported_count = 0
        for row in valid_rows:
            # Check if code already exists (for new imports)
            if not crud.get_model_by_code(db, row['code']):
                model_data = ModelCreate(
                    status=row['status'],
                    code=row['code'],
                    real_name=row['real_name'],
                    working_name=row['working_name'],
                    start_date=row['start_date'],
                    payment_method=row['payment_method'],
                    payment_frequency=row['payment_frequency'],
                    amount_monthly=row['amount_monthly'],
                    crypto_wallet=row['crypto_wallet'],
                )
                crud.create_model(db, model_data)
                imported_count += 1
        
        # Return redirect with success message
        message = f"Successfully imported {imported_count} model{'s' if imported_count != 1 else ''}"
        return RedirectResponse(url="/models?message=" + message, status_code=303)
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing file: {str(e)}")


@router.get("/import/resolve")
def resolve_import_duplicates(
    batch: str,
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    """Show duplicate resolution UI."""
    try:
        batch_file = Path("data") / f"{batch}.json"
        if not batch_file.exists():
            raise HTTPException(status_code=404, detail="Batch file not found")
        
        with open(batch_file) as f:
            batch_data = json.load(f)
        
        return templates.TemplateResponse(
            "models/resolve_duplicates.html",
            {
                "request": request,
                "user": user,
                "batch_id": batch,
                "payout_duplicates": batch_data.get('payout_duplicates', {}),
                "errors": batch_data.get('errors', []),
                "has_payment_data": batch_data.get('has_payment_data', False),
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading batch: {str(e)}")


@router.post("/import/resolve")
async def process_duplicate_resolution(
    batch: str = Form(...),
    action: str = Form(...),  # "keep_all", "keep_one", "delete_all"
    selected_ids: str = Form(default=""),  # JSON array of duplicate IDs to keep
    db: Session = Depends(get_session),
    user: User = Depends(get_admin_user),
):
    """Process duplicate resolution and complete import."""
    try:
        batch_file = Path("data") / f"{batch}.json"
        if not batch_file.exists():
            raise HTTPException(status_code=404, detail="Batch file not found")
        
        with open(batch_file) as f:
            batch_data = json.load(f)
        
        valid_rows = batch_data['valid_rows']
        payout_duplicates = batch_data['payout_duplicates']
        
        # Parse selected IDs to keep/delete
        selected = json.loads(selected_ids) if selected_ids else {}
        
        # Process duplicate decisions
        for dup_key, duplicates_list in payout_duplicates.items():
            if action == "delete_all":
                # Delete all matching payouts
                for dup in duplicates_list:
                    payout_id = dup['existing_payout_id']
                    payout = db.get(crud.Payout, payout_id)
                    if payout:
                        db.delete(payout)
            elif action == "keep_one" and dup_key in selected:
                # Keep only selected ID, delete others
                keep_id = selected[dup_key]
                for dup in duplicates_list:
                    if dup['existing_payout_id'] != keep_id:
                        payout = db.get(crud.Payout, dup['existing_payout_id'])
                        if payout:
                            db.delete(payout)
            # "keep_all" means don't delete anything
        
        db.commit()
        
        # Now import valid rows
        imported_count = 0
        for row in valid_rows:
            # Check if model code already exists
            existing_model = crud.get_model_by_code(db, row['code'])
            if not existing_model:
                model_data = ModelCreate(
                    status=row['status'],
                    code=row['code'],
                    real_name=row['real_name'],
                    working_name=row['working_name'],
                    start_date=row['start_date'],
                    payment_method=row['payment_method'],
                    payment_frequency=row['payment_frequency'],
                    amount_monthly=row['amount_monthly'],
                    crypto_wallet=row['crypto_wallet'],
                )
                crud.create_model(db, model_data)
                imported_count += 1
        
        # Clean up batch file
        batch_file.unlink(missing_ok=True)
        
        # Return success
        message = f"Successfully imported {imported_count} model{'s' if imported_count != 1 else ''}"
        return RedirectResponse(url=f"/models?message={message}", status_code=303)
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing resolution: {str(e)}")


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

