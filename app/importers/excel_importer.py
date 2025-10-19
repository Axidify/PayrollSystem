"""Utilities for importing models and payouts from Excel workbooks."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any, Iterable

import pandas as pd
from dateutil import parser as date_parser
from sqlalchemy.orm import Session

from app import crud
from app.models import (
    FREQUENCY_ENUM,
    PAYOUT_STATUS_ENUM,
    STATUS_ENUM,
    Model,
    ModelCompensationAdjustment,
    Payout,
    ScheduleRun,
    ValidationIssue,
)

MODEL_COLUMNS: dict[str, dict[str, Any]] = {
    "code": {"aliases": ["code", "model code", "model"], "required": True},
    "status": {"aliases": ["status", "model status"], "required": False},
    "real_name": {"aliases": ["real name", "legal name", "real_name"], "required": True},
    "working_name": {"aliases": ["working name", "stage name", "working_name"], "required": True},
    "start_date": {"aliases": ["start date", "model start date", "start_date"], "required": True},
    "payment_method": {"aliases": ["payment method", "method", "payment_method"], "required": True},
    "payment_frequency": {"aliases": ["payment frequency", "frequency", "payment_frequency"], "required": True},
    "amount_monthly": {
        "aliases": [
            "monthly amount",
            "amount monthly",
            "monthly pay",
            "amount",
            "amount_monthly",
        ],
        "required": True,
    },
    "crypto_wallet": {"aliases": ["crypto wallet", "wallet", "crypto_wallet"], "required": False},
}

PAYOUT_COLUMNS: dict[str, dict[str, Any]] = {
    "code": {"aliases": ["code", "model code"], "required": True},
    "pay_date": {"aliases": ["pay date", "payment date", "pay_date"], "required": True},
    "amount": {"aliases": ["amount", "payment amount"], "required": True},
    "status": {"aliases": ["status", "payment status"], "required": True},
    "payment_method": {"aliases": ["payment method", "method", "payment_method"], "required": False},
    "payment_frequency": {"aliases": ["payment frequency", "frequency", "payment_frequency"], "required": False},
    "notes": {"aliases": ["notes", "note", "notes & actions", "actions"], "required": False},
}

ADJUSTMENT_COLUMNS: dict[str, dict[str, Any]] = {
    "code": {"aliases": ["code", "model code"], "required": True},
    "effective_date": {"aliases": ["effective date", "effective_date"], "required": True},
    "amount_monthly": {
        "aliases": [
            "monthly amount",
            "amount monthly",
            "monthly pay",
            "amount",
            "amount_monthly",
        ],
        "required": True,
    },
    "notes": {"aliases": ["notes", "note", "comments"], "required": False},
}

DATE_FORMATS: tuple[str, ...] = ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y")


@dataclass
class RunOptions:
    schedule_run_id: int | None = None
    create_schedule_run: bool = False
    target_year: int | None = None
    target_month: int | None = None
    currency: str = "USD"
    export_dir: str = "exports"
    auto_generate_runs: bool = False


@dataclass
class ImportOptions:
    model_sheet: str = "Models"
    payout_sheet: str = "Payouts"
    update_existing: bool = False
    adjustments_sheet: str | None = "CompensationAdjustments"


@dataclass
class ImportSummary:
    models_created: int = 0
    models_updated: int = 0
    payouts_created: int = 0
    adjustments_created: int = 0
    adjustments_updated: int = 0
    schedule_run_id: int | None = None
    schedule_run_ids: list[int] = field(default_factory=list)
    model_errors: list[str] = field(default_factory=list)
    payout_errors: list[str] = field(default_factory=list)
    adjustment_errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "models_created": self.models_created,
            "models_updated": self.models_updated,
            "payouts_created": self.payouts_created,
            "adjustments_created": self.adjustments_created,
            "adjustments_updated": self.adjustments_updated,
            "schedule_run_id": self.schedule_run_id,
            "schedule_run_ids": self.schedule_run_ids,
            "model_errors": self.model_errors,
            "payout_errors": self.payout_errors,
            "adjustment_errors": self.adjustment_errors,
        }


def resolve_column(df: pd.DataFrame, aliases: Iterable[str]) -> str | None:
    lookup = {str(col).strip().lower(): str(col) for col in df.columns}
    for alias in aliases:
        key = alias.strip().lower()
        if key in lookup:
            return lookup[key]
    return None


def normalize_columns(df: pd.DataFrame, spec: dict[str, dict[str, Any]], label: str) -> pd.DataFrame:
    mapping: dict[str, str] = {}
    for canonical, column_spec in spec.items():
        source = resolve_column(df, column_spec["aliases"])
        if source:
            mapping[source] = canonical
        elif column_spec.get("required", False):
            raise ValueError(f"Missing required column '{canonical}' in {label} sheet")
    renamed = df.rename(columns=mapping)
    columns = list(mapping.values())
    return renamed[columns]


def parse_date_value(raw: Any, field_name: str) -> date:
    if pd.isna(raw):
        raise ValueError(f"{field_name} is missing")
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    text = str(raw).strip()
    if not text:
        raise ValueError(f"{field_name} is empty")
    for pattern in DATE_FORMATS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    try:
        return date_parser.parse(text).date()
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Could not parse {field_name} value '{raw}'") from exc


def parse_decimal_value(raw: Any, field_name: str) -> Decimal:
    if pd.isna(raw):
        raise ValueError(f"{field_name} is missing")
    if isinstance(raw, Decimal):
        value = raw
    else:
        text = str(raw).strip()
        if not text:
            raise ValueError(f"{field_name} is empty")
        normalized = text.replace("$", "").replace(",", "")
        try:
            value = Decimal(normalized)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"Invalid {field_name} value '{raw}'") from exc
    if value <= 0:
        raise ValueError(f"{field_name} must be greater than zero (got {value})")
    return value


def normalize_frequency(raw: Any) -> str:
    if pd.isna(raw):
        raise ValueError("payment frequency is missing")
    text = str(raw).strip().lower().replace(" ", "")
    if text in ("weekly", "week"):
        value = "weekly"
    elif text in ("biweekly", "bi-weekly", "fortnightly"):
        value = "biweekly"
    elif text in ("monthly", "month"):
        value = "monthly"
    else:
        value = text
    if value not in FREQUENCY_ENUM:
        raise ValueError(f"Unsupported payment frequency '{raw}'")
    return value


def normalize_status(raw: Any) -> str:
    if pd.isna(raw):
        return "Active"
    text = str(raw).strip()
    if not text:
        return "Active"
    lowered = text.lower()
    allowed = {value.lower(): value for value in STATUS_ENUM}
    if lowered in allowed:
        return allowed[lowered]
    raise ValueError(f"Unsupported model status '{raw}'")


def normalize_payout_status(raw: Any) -> str:
    if pd.isna(raw):
        return "not_paid"
    text = str(raw).strip().lower().replace(" ", "_")
    if text == "paid":
        return "paid"
    if text in ("not_paid", "unpaid"):
        return "not_paid"
    if text in ("on_hold", "hold", "holding"):
        return "on_hold"
    if text not in PAYOUT_STATUS_ENUM:
        raise ValueError(f"Unsupported payout status '{raw}'")
    return text


def clean_string(raw: Any) -> str | None:
    if pd.isna(raw):
        return None
    text = str(raw).strip()
    return text or None


def load_sheet(workbook_bytes: bytes, sheet_name: str) -> pd.DataFrame:
    try:
        return pd.read_excel(BytesIO(workbook_bytes), sheet_name=sheet_name)
    except ValueError as exc:
        raise ValueError(f"Could not read sheet '{sheet_name}'") from exc


def group_payout_rows_by_month(df: pd.DataFrame) -> tuple[dict[tuple[int, int], pd.DataFrame], list[str]]:
    column = resolve_column(df, PAYOUT_COLUMNS["pay_date"]["aliases"])
    if not column:
        raise ValueError("Missing required pay_date column in payout sheet")

    errors: list[str] = []
    buckets: dict[tuple[int, int], list[int]] = {}

    for index, raw in df[column].items():
        try:
            pay_date = parse_date_value(raw, "pay date")
        except ValueError as exc:
            errors.append(f"Row {index + 2}: {exc}")
            continue
        key = (pay_date.year, pay_date.month)
        buckets.setdefault(key, []).append(index)

    grouped_frames: dict[tuple[int, int], pd.DataFrame] = {}
    for key, indices in buckets.items():
        grouped_frames[key] = df.loc[indices].copy()

    return grouped_frames, errors


def import_models(df: pd.DataFrame, session: Session, update_existing: bool) -> tuple[int, int, list[str]]:
    created = 0
    updated = 0
    errors: list[str] = []
    normalized = normalize_columns(df, MODEL_COLUMNS, "model")
    records = normalized.dropna(how="all")
    existing = {m.code.lower(): m for m in session.query(Model).all()}

    for idx, row in records.iterrows():
        code_raw = row.get("code")
        if pd.isna(code_raw):
            errors.append(f"Row {idx + 2}: model code is missing")
            continue
        code = str(code_raw).strip()
        if not code:
            errors.append(f"Row {idx + 2}: model code is empty")
            continue
        try:
            start_date = parse_date_value(row.get("start_date"), "start date")
            amount = parse_decimal_value(row.get("amount_monthly"), "monthly amount")
            frequency = normalize_frequency(row.get("payment_frequency"))
            status_value = normalize_status(row.get("status"))
            real_name = clean_string(row.get("real_name"))
            working_name = clean_string(row.get("working_name"))
            method = clean_string(row.get("payment_method"))
            wallet = clean_string(row.get("crypto_wallet"))
        except ValueError as exc:
            errors.append(f"Row {idx + 2}: {exc}")
            continue
        if not real_name or not working_name or not method:
            errors.append(f"Row {idx + 2}: required text fields are missing")
            continue

        model = existing.get(code.lower())
        if model:
            if update_existing:
                model.status = status_value
                model.real_name = real_name
                model.working_name = working_name
                model.start_date = start_date
                model.payment_method = method
                model.payment_frequency = frequency
                model.amount_monthly = amount
                model.crypto_wallet = wallet
                updated += 1
            else:
                errors.append(f"Row {idx + 2}: model '{code}' already exists (enable update to modify)")
            continue

        model = Model(
            code=code,
            status=status_value,
            real_name=real_name,
            working_name=working_name,
            start_date=start_date,
            payment_method=method,
            payment_frequency=frequency,
            amount_monthly=amount,
            crypto_wallet=wallet,
        )
        session.add(model)
        existing[code.lower()] = model
        created += 1
    session.flush()
    return created, updated, errors


def import_compensation_adjustments(
    df: pd.DataFrame, session: Session
) -> tuple[int, int, list[str]]:
    created = 0
    updated = 0
    errors: list[str] = []
    normalized = normalize_columns(df, ADJUSTMENT_COLUMNS, "compensation adjustment")
    records = normalized.dropna(how="all")
    models_by_code = {m.code.lower(): m for m in session.query(Model).all()}

    for idx, row in records.iterrows():
        code_raw = row.get("code")
        if pd.isna(code_raw):
            errors.append(f"Row {idx + 2}: model code is missing")
            continue
        code = str(code_raw).strip()
        if not code:
            errors.append(f"Row {idx + 2}: model code is empty")
            continue
        model = models_by_code.get(code.lower())
        if not model:
            errors.append(f"Row {idx + 2}: model '{code}' not found; import models first")
            continue
        try:
            effective_date = parse_date_value(row.get("effective_date"), "effective date")
            amount = parse_decimal_value(row.get("amount_monthly"), "monthly amount")
            notes = clean_string(row.get("notes"))
        except ValueError as exc:
            errors.append(f"Row {idx + 2}: {exc}")
            continue

        existing = (
            session.query(ModelCompensationAdjustment)
            .filter(
                ModelCompensationAdjustment.model_id == model.id,
                ModelCompensationAdjustment.effective_date == effective_date,
            )
            .first()
        )
        if existing:
            if existing.amount_monthly != amount or existing.notes != notes:
                existing.amount_monthly = amount
                existing.notes = notes
                session.add(existing)
                updated += 1
        else:
            crud.create_compensation_adjustment(
                session,
                model,
                effective_date=effective_date,
                amount_monthly=amount,
                notes=notes,
            )
            created += 1

    session.flush()
    return created, updated, errors


def ensure_schedule_run(session: Session, options: RunOptions) -> ScheduleRun:
    if options.schedule_run_id:
        run = session.get(ScheduleRun, options.schedule_run_id)
        if not run:
            raise ValueError(f"Schedule run {options.schedule_run_id} not found")
        return run
    if not options.create_schedule_run:
        raise ValueError("Provide schedule_run_id or enable create_schedule_run")
    if options.target_year is None or options.target_month is None:
        raise ValueError("target_year and target_month are required for new schedule runs")
    if not 1 <= int(options.target_month) <= 12:
        raise ValueError("target_month must be between 1 and 12")
    existing_run = (
        session.query(ScheduleRun)
        .filter(
            ScheduleRun.target_year == int(options.target_year),
            ScheduleRun.target_month == int(options.target_month),
        )
        .order_by(ScheduleRun.created_at.desc())
        .first()
    )
    if existing_run:
        existing_run.currency = str(options.currency).upper()
        existing_run.export_path = str(options.export_dir)
        existing_run.include_inactive = False
        session.flush()
        return existing_run
    run = ScheduleRun(
        target_year=int(options.target_year),
        target_month=int(options.target_month),
        currency=str(options.currency).upper(),
        include_inactive=False,
        summary_models_paid=0,
        summary_total_payout=Decimal("0"),
        summary_frequency_counts="{}",
        export_path=str(options.export_dir),
    )
    session.add(run)
    session.flush()
    return run


def import_payouts(
    df: pd.DataFrame,
    session: Session,
    run: ScheduleRun,
) -> tuple[int, list[str]]:
    created = 0
    errors: list[str] = []
    # Always reset existing payouts and validation issues before re-importing
    session.query(Payout).filter(Payout.schedule_run_id == run.id).delete()
    session.query(ValidationIssue).filter(ValidationIssue.schedule_run_id == run.id).delete()
    session.flush()
    normalized = normalize_columns(df, PAYOUT_COLUMNS, "payout")
    records = normalized.dropna(how="all")
    models_by_code = {m.code.lower(): m for m in session.query(Model).all()}

    payouts_to_add: list[Payout] = []
    for idx, row in records.iterrows():
        code_raw = row.get("code")
        if pd.isna(code_raw):
            errors.append(f"Row {idx + 2}: payout code is missing")
            continue
        code = str(code_raw).strip()
        if not code:
            errors.append(f"Row {idx + 2}: payout code is empty")
            continue
        model = models_by_code.get(code.lower())
        if not model:
            errors.append(f"Row {idx + 2}: model '{code}' not found; import models first")
            continue
        try:
            pay_date = parse_date_value(row.get("pay_date"), "pay date")
            amount = parse_decimal_value(row.get("amount"), "amount")
            status_value = normalize_payout_status(row.get("status"))
            frequency = row.get("payment_frequency")
            frequency_value = normalize_frequency(frequency) if not pd.isna(frequency) else model.payment_frequency
            method_value = clean_string(row.get("payment_method")) or model.payment_method
            notes_value = clean_string(row.get("notes"))
        except ValueError as exc:
            errors.append(f"Row {idx + 2}: {exc}")
            continue

        payout = Payout(
            schedule_run_id=run.id,
            model_id=model.id,
            pay_date=pay_date,
            code=code,
            real_name=model.real_name,
            working_name=model.working_name,
            payment_method=method_value,
            payment_frequency=frequency_value,
            amount=amount,
            status=status_value,
            notes=notes_value,
        )
        payouts_to_add.append(payout)
        created += 1

    session.add_all(payouts_to_add)
    session.flush()
    refresh_schedule_summary(session, run.id)
    return created, errors


def refresh_schedule_summary(session: Session, run_id: int) -> None:
    payouts = session.query(Payout).filter(Payout.schedule_run_id == run_id).all()
    total = sum((p.amount for p in payouts), Decimal("0"))
    paid_count = sum(1 for p in payouts if p.status == "paid")
    freq_counts: dict[str, int] = {}
    for payout in payouts:
        freq = payout.payment_frequency or ""
        freq_counts[freq] = freq_counts.get(freq, 0) + 1
    run = session.get(ScheduleRun, run_id)
    if run:
        run.summary_total_payout = total
        run.summary_models_paid = paid_count
        run.summary_frequency_counts = json.dumps(freq_counts)


def import_from_excel(
    session: Session,
    workbook_bytes: bytes,
    import_options: ImportOptions,
    run_options: RunOptions,
) -> ImportSummary:
    model_df = load_sheet(workbook_bytes, import_options.model_sheet)
    payout_df = load_sheet(workbook_bytes, import_options.payout_sheet)

    summary = ImportSummary()

    created_models, updated_models, model_errors = import_models(
        model_df,
        session,
        import_options.update_existing,
    )
    summary.models_created = created_models
    summary.models_updated = updated_models
    summary.model_errors = model_errors

    adjustment_df: pd.DataFrame | None = None
    if import_options.adjustments_sheet:
        try:
            adjustment_df = load_sheet(workbook_bytes, import_options.adjustments_sheet)
        except ValueError:
            adjustment_df = None
    if adjustment_df is not None:
        created_adjustments, updated_adjustments, adjustment_errors = import_compensation_adjustments(
            adjustment_df,
            session,
        )
        summary.adjustments_created = created_adjustments
        summary.adjustments_updated = updated_adjustments
        summary.adjustment_errors = adjustment_errors

    if run_options.auto_generate_runs:
        grouped_frames, grouping_errors = group_payout_rows_by_month(payout_df)
        summary.payout_errors.extend(grouping_errors)

        if not grouped_frames:
            if not grouping_errors:
                raise ValueError("No payout rows available to import.")
            raise ValueError("No valid pay dates found; unable to auto-create schedule runs.")

        for (year, month), subset in sorted(grouped_frames.items()):
            per_run_options = RunOptions(
                schedule_run_id=None,
                create_schedule_run=True,
                target_year=year,
                target_month=month,
                currency=run_options.currency,
                export_dir=run_options.export_dir,
            )
            run = ensure_schedule_run(session, per_run_options)
            summary.schedule_run_ids.append(run.id)
            if summary.schedule_run_id is None:
                summary.schedule_run_id = run.id

            created_payouts, payout_errors = import_payouts(subset, session, run)
            summary.payouts_created += created_payouts
            summary.payout_errors.extend(
                [f"{year:04d}-{month:02d}: {message}" for message in payout_errors]
            )
    else:
        run = ensure_schedule_run(session, run_options)
        summary.schedule_run_id = run.id
        summary.schedule_run_ids.append(run.id)

        created_payouts, payout_errors = import_payouts(payout_df, session, run)
        summary.payouts_created = created_payouts
        summary.payout_errors = payout_errors

    return summary
