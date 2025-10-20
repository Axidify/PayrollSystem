from __future__ import annotations

from io import BytesIO
from typing import Iterable

import pandas as pd
from sqlalchemy.orm import Session

from app.core.payroll import ensure_non_empty_frames
from app.models import (
    AdhocPayment,
    Model,
    ModelCompensationAdjustment,
    Payout,
    ScheduleRun,
)


def _models_df(models: Iterable[Model], currency: str = "USD") -> pd.DataFrame:
    rows = []
    for item in models:
        rows.append(
            {
                "model_id": item.id,
                "code": item.code,
                "status": item.status,
                "real_name": item.real_name,
                "working_name": item.working_name,
                "start_date": item.start_date,
                "payment_method": item.payment_method,
                "payment_frequency": item.payment_frequency,
                f"amount_monthly ({currency})": float(item.amount_monthly)
                if item.amount_monthly is not None
                else None,
                "crypto_wallet": item.crypto_wallet,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
        )
    return pd.DataFrame(rows)


def _adjustments_df(adjustments: Iterable[ModelCompensationAdjustment]) -> pd.DataFrame:
    rows = []
    for item in adjustments:
        rows.append(
            {
                "adjustment_id": item.id,
                "model_id": item.model_id,
                "model_code": item.model.code if getattr(item, "model", None) else None,
                "effective_date": item.effective_date,
                "amount_monthly": float(item.amount_monthly)
                if item.amount_monthly is not None
                else None,
                "notes": item.notes,
                "created_at": item.created_at,
                "created_by": item.created_by,
            }
        )
    return pd.DataFrame(rows)


def _adhoc_df(adhocs: Iterable[AdhocPayment]) -> pd.DataFrame:
    rows = []
    for item in adhocs:
        rows.append(
            {
                "adhoc_id": item.id,
                "model_id": item.model_id,
                "model_code": item.model.code if getattr(item, "model", None) else None,
                "pay_date": item.pay_date,
                "amount": float(item.amount) if item.amount is not None else None,
                "description": item.description,
                "notes": item.notes,
                "status": item.status,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
        )
    return pd.DataFrame(rows)


def _runs_df(runs: Iterable[ScheduleRun]) -> pd.DataFrame:
    rows = []
    for item in runs:
        rows.append(
            {
                "schedule_run_id": item.id,
                "target_year": item.target_year,
                "target_month": item.target_month,
                "currency": item.currency,
                "include_inactive": item.include_inactive,
                "summary_models_paid": item.summary_models_paid,
                "summary_total_payout": float(item.summary_total_payout)
                if item.summary_total_payout is not None
                else None,
                "export_path": item.export_path,
                "created_at": item.created_at,
            }
        )
    return pd.DataFrame(rows)


def _payouts_df(payouts: Iterable[Payout]) -> pd.DataFrame:
    rows = []
    for item in payouts:
        rows.append(
            {
                "payout_id": item.id,
                "schedule_run_id": item.schedule_run_id,
                "schedule_run_label": (
                    f"{item.schedule_run.target_year}-{item.schedule_run.target_month:02d}"
                    if getattr(item, "schedule_run", None)
                    else None
                ),
                "model_id": item.model_id,
                "model_code": item.code,
                "pay_date": item.pay_date,
                "amount": float(item.amount) if item.amount is not None else None,
                "notes": item.notes,
                "status": item.status,
                "payment_method": item.payment_method,
                "payment_frequency": item.payment_frequency,
            }
        )
    return pd.DataFrame(rows)


def export_full_workbook(db: Session, currency: str = "USD") -> bytes:
    """Return an XLSX workbook (bytes) with all key payroll tables."""

    models = db.query(Model).order_by(Model.code).all()
    adjustments = (
        db.query(ModelCompensationAdjustment)
        .order_by(
            ModelCompensationAdjustment.model_id,
            ModelCompensationAdjustment.effective_date,
        )
        .all()
    )
    adhocs = db.query(AdhocPayment).order_by(AdhocPayment.model_id, AdhocPayment.pay_date).all()
    runs = (
        db.query(ScheduleRun)
        .order_by(ScheduleRun.target_year.desc(), ScheduleRun.target_month.desc())
        .all()
    )
    payouts = db.query(Payout).order_by(Payout.pay_date).all()

    df_models = _models_df(models, currency)
    df_adjustments = _adjustments_df(adjustments)
    df_adhoc = _adhoc_df(adhocs)
    df_runs = _runs_df(runs)
    df_payouts = _payouts_df(payouts)

    # ensure_non_empty_frames returns placeholders—retain call for parity with legacy exports
    ensure_non_empty_frames(pd.DataFrame(), df_models, pd.DataFrame(), currency)

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df_models.to_excel(writer, sheet_name="Models", index=False)
        df_adjustments.to_excel(writer, sheet_name="CompensationAdjustments", index=False)
        df_adhoc.to_excel(writer, sheet_name="AdhocPayments", index=False)
        df_runs.to_excel(writer, sheet_name="ScheduleRuns", index=False)
        df_payouts.to_excel(writer, sheet_name="Payouts", index=False)

    buffer.seek(0)
    return buffer.getvalue()
