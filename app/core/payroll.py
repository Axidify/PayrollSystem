"""Core payroll scheduling utilities shared between the CLI and web app."""
from __future__ import annotations

import calendar
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import pandas as pd
from dateutil import parser as date_parser

CANONICAL_COLUMNS = {
    "status": "status",
    "code": "code",
    "real name": "real_name",
    "working name": "working_name",
    "start date": "start_date",
    "payment method": "payment_method",
    "payment frequency": "payment_frequency",
    "amount monthly": "amount_monthly",
}

FREQUENCY_PLANS = {
    "weekly": [0, 1, 2, 3],
    "biweekly": [1, 3],
    "monthly": [3],
}

MONEY_QUANT = Decimal("0.01")


@dataclass
class ValidationMessage:
    """Represents a validation outcome captured while parsing a row."""

    level: str
    text: str


@dataclass
class ModelRecord:
    """Normalized representation of a model row."""

    row_number: int
    status: str
    code: str
    real_name: str
    working_name: str
    start_date: Optional[date]
    payment_method: str
    payment_frequency: str
    amount_monthly: Optional[Decimal]
    validation_messages: List[ValidationMessage] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        """Return True when at least one blocking issue exists."""

        return any(message.level == "error" for message in self.validation_messages)

    def add_message(self, level: str, text: str) -> None:
        """Add a validation message, normalizing the severity label."""

        self.validation_messages.append(ValidationMessage(level=level, text=text))


def load_models(input_path: Path) -> pd.DataFrame:
    """Load model data from CSV or Excel into a DataFrame."""

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    ext = input_path.suffix.lower()
    if ext == ".csv":
        df = pd.read_csv(input_path)
    elif ext in {".xls", ".xlsx"}:
        df = pd.read_excel(input_path)
    else:
        raise ValueError("Unsupported input file type. Provide .csv or .xlsx")

    return normalize_columns(df)


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize column names to canonical snake_case identifiers."""

    rename_map = {}
    for column in df.columns:
        key = column.strip().lower()
        if key in CANONICAL_COLUMNS:
            rename_map[column] = CANONICAL_COLUMNS[key]
    df = df.rename(columns=rename_map)

    missing = {alias for alias in CANONICAL_COLUMNS.values() if alias not in df.columns}
    if missing:
        raise ValueError(f"Input missing required columns: {', '.join(sorted(missing))}")

    return df


def parse_decimal(value) -> Optional[Decimal]:
    """Attempt to coerce a value into a currency decimal."""

    if pd.isna(value):
        return None
    try:
        decimal_value = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        return None
    try:
        return decimal_value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def parse_date(value) -> Optional[date]:
    """Parse a date value if possible."""

    if pd.isna(value):
        return None
    if isinstance(value, date):
        return value
    try:
        return date_parser.parse(str(value)).date()
    except (ValueError, TypeError, OverflowError):
        return None


def parse_models(df: pd.DataFrame) -> List[ModelRecord]:
    """Convert the source DataFrame into ModelRecord objects with validation."""

    records: List[ModelRecord] = []
    for idx, row in df.iterrows():
        row_number = idx + 2  # account for header row when referencing Excel-style numbers
        status_raw = str(row.get("status", "")).strip()
        status = status_raw.title() if status_raw else ""

        code_raw = row.get("code")
        code = "" if pd.isna(code_raw) else str(code_raw).strip()

        real_name_raw = row.get("real_name")
        real_name = "" if pd.isna(real_name_raw) else str(real_name_raw).strip()

        working_name_raw = row.get("working_name")
        working_name = "" if pd.isna(working_name_raw) else str(working_name_raw).strip()

        payment_method_raw = row.get("payment_method")
        payment_method = "" if pd.isna(payment_method_raw) else str(payment_method_raw).strip()

        frequency_raw = row.get("payment_frequency")
        payment_frequency = (
            str(frequency_raw).strip().lower() if not pd.isna(frequency_raw) else ""
        )

        start_date = parse_date(row.get("start_date"))
        amount_monthly = parse_decimal(row.get("amount_monthly"))

        record = ModelRecord(
            row_number=row_number,
            status=status,
            code=code,
            real_name=real_name,
            working_name=working_name,
            start_date=start_date,
            payment_method=payment_method,
            payment_frequency=payment_frequency,
            amount_monthly=amount_monthly,
        )
        for message in validate_row(record):
            record.add_message(message.level, message.text)
        records.append(record)
    return records


def validate_row(record: ModelRecord) -> List[ValidationMessage]:
    """Apply validation rules to a record and collect issues."""

    messages: List[ValidationMessage] = []

    if not record.status:
        messages.append(ValidationMessage("error", "Status is required."))
    elif record.status.lower() not in {"active", "inactive"}:
        messages.append(ValidationMessage("error", f"Unrecognized status '{record.status}'."))
    elif record.status.lower() != "active":
        messages.append(ValidationMessage("warning", "Status is not Active; payouts suppressed."))

    if not record.code:
        messages.append(ValidationMessage("error", "Code is required."))

    if not record.real_name:
        messages.append(ValidationMessage("warning", "Real Name is blank."))

    if not record.working_name:
        messages.append(ValidationMessage("warning", "Working Name is blank."))

    if not record.payment_method:
        messages.append(ValidationMessage("warning", "Payment Method is blank."))

    if not record.payment_frequency:
        messages.append(ValidationMessage("error", "Payment Frequency is required."))
    elif record.payment_frequency not in FREQUENCY_PLANS:
        messages.append(
            ValidationMessage(
                "error",
                f"Payment Frequency '{record.payment_frequency}' is invalid. Expected weekly, biweekly, or monthly.",
            )
        )

    if record.amount_monthly is None:
        messages.append(ValidationMessage("error", "Amount Monthly is missing or invalid."))
    elif record.amount_monthly <= Decimal("0"):
        messages.append(ValidationMessage("error", "Amount Monthly must be positive."))

    if record.start_date is None:
        messages.append(ValidationMessage("error", "Start Date is missing or invalid."))

    return messages


def get_pay_dates(year: int, month: int) -> List[date]:
    """Return the four fixed pay dates for a given month."""

    eom = calendar.monthrange(year, month)[1]
    return [
        date(year, month, 7),
        date(year, month, 14),
        date(year, month, 21),
        date(year, month, eom),
    ]


def payout_plan(frequency: str) -> List[int]:
    """Return the plan indices for the supplied payment frequency."""

    return list(FREQUENCY_PLANS.get(frequency, []))


def allocate_amounts(monthly_amount: Decimal, frequency: str) -> Tuple[List[Decimal], bool]:
    """Allocate a monthly amount across the frequency plan with rounding adjustment."""

    plan = payout_plan(frequency)
    if not plan:
        raise ValueError(f"No payout plan configured for frequency '{frequency}'.")

    count = len(plan)
    base_share = (monthly_amount / count).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    amounts: List[Decimal] = [base_share for _ in range(count - 1)]
    if amounts:
        remaining = monthly_amount - sum(amounts)
    else:
        remaining = monthly_amount
    final_share = remaining.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    amounts.append(final_share)
    adjusted = final_share != base_share
    return amounts, adjusted


def is_eligible_for_date(record: ModelRecord, pay_date: date) -> bool:
    """Determine if a record qualifies to be paid on the given date."""

    if record.status.lower() != "active":
        return False
    if record.start_date is None:
        return False
    return record.start_date <= pay_date


def build_pay_schedule(
    records: Iterable[ModelRecord],
    year: int,
    month: int,
    currency: str,
) -> Tuple[pd.DataFrame, dict]:
    """Generate the pay schedule DataFrame and summary metrics."""

    pay_dates = get_pay_dates(year, month)
    rows: List[dict] = []
    total_payout = Decimal("0")
    frequency_counter: Counter[str] = Counter()
    scheduled_codes = set()

    for record in records:
        if record.has_errors or record.amount_monthly is None:
            continue
        plan = payout_plan(record.payment_frequency)
        if not plan:
            continue

        allocations, rounding_adjusted = allocate_amounts(record.amount_monthly, record.payment_frequency)
        carryover = Decimal("0")
        skipped_due_to_start = False

        for position, plan_index in enumerate(plan):
            pay_date = pay_dates[plan_index]
            base_amount = allocations[position]
            if not is_eligible_for_date(record, pay_date):
                carryover += base_amount
                skipped_due_to_start = True
                continue

            payout_amount = base_amount + carryover
            carryover = Decimal("0")
            notes: List[str] = []
            if skipped_due_to_start:
                notes.append("Start date blocks earlier payouts")
                skipped_due_to_start = False
            if payout_amount != base_amount:
                notes.append("Includes deferred payouts")
            if rounding_adjusted and position == len(plan) - 1:
                notes.append("Adjusted for rounding")

            rows.append(
                {
                    "Pay Date": pay_date,
                    "Code": record.code,
                    "Real Name": record.real_name,
                    "Working Name": record.working_name,
                    "Payment Method": record.payment_method,
                    "Payment Frequency": record.payment_frequency.title(),
                    f"Amount ({currency})": payout_amount,
                    "Notes": "; ".join(notes) if notes else "",
                }
            )
            total_payout += payout_amount
            frequency_counter[record.payment_frequency.title()] += 1
            scheduled_codes.add(record.code)

        if carryover > Decimal("0"):
            record.add_message(
                "warning",
                "Start date falls after all scheduled pay dates; nothing released this month.",
            )

    schedule_df = pd.DataFrame(rows)
    if not schedule_df.empty:
        schedule_df = schedule_df.sort_values(["Pay Date", "Code"]).reset_index(drop=True)
        schedule_df["Pay Date"] = pd.to_datetime(schedule_df["Pay Date"])
        amount_column = f"Amount ({currency})"
        schedule_df[amount_column] = schedule_df[amount_column].apply(
            lambda value: float(value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP))
        )

    summary = {
        "models_paid": len(scheduled_codes),
        "total_payout": float(total_payout.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)),
        "frequency_counts": dict(frequency_counter),
    }
    return schedule_df, summary


def build_models_table(records: Iterable[ModelRecord], currency: str) -> pd.DataFrame:
    """Create the normalized models DataFrame for export."""

    rows: List[dict] = []
    for record in records:
        validation_summary = "; ".join(
            f"[{message.level.upper()}] {message.text}" for message in record.validation_messages
        )
        amount_value = (
            float(record.amount_monthly.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP))
            if record.amount_monthly is not None
            else None
        )
        rows.append(
            {
                "Row": record.row_number,
                "Status": record.status,
                "Code": record.code,
                "Real Name": record.real_name,
                "Working Name": record.working_name,
                "Start Date": record.start_date,
                "Payment Method": record.payment_method,
                "Payment Frequency": record.payment_frequency.title(),
                f"Amount Monthly ({currency})": amount_value,
                "Validation Messages": validation_summary,
            }
        )

    models_df = pd.DataFrame(rows)
    if not models_df.empty:
        models_df["Start Date"] = pd.to_datetime(models_df["Start Date"])
    return models_df


def build_validation_report(
    records: Iterable[ModelRecord],
    include_inactive: bool,
) -> pd.DataFrame:
    """Aggregate validation messages into a flat report."""

    rows: List[dict] = []
    for record in records:
        is_active = record.status.lower() == "active"
        if not is_active and not include_inactive:
            continue
        for message in record.validation_messages:
            rows.append(
                {
                    "Row": record.row_number,
                    "Code": record.code,
                    "Severity": message.level,
                    "Issue": message.text,
                }
            )
    report_df = pd.DataFrame(rows)
    if not report_df.empty:
        report_df = report_df.sort_values(["Row", "Severity"]).reset_index(drop=True)
    return report_df


def export_outputs(
    base_filename: str,
    schedule_df: pd.DataFrame,
    models_df: pd.DataFrame,
    validation_df: pd.DataFrame,
    output_dir: Path,
) -> None:
    """Write Excel workbook and companion CSV extracts."""

    output_dir.mkdir(parents=True, exist_ok=True)

    excel_path = output_dir / f"{base_filename}.xlsx"
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        schedule_df.to_excel(writer, sheet_name="Pay_Schedule", index=False)
        models_df.to_excel(writer, sheet_name="Models", index=False)
        validation_df.to_excel(writer, sheet_name="Validation", index=False)

    schedule_df.to_csv(output_dir / f"{base_filename}.csv", index=False)
    models_df.to_csv(output_dir / f"{base_filename}_models.csv", index=False)
    validation_df.to_csv(output_dir / f"{base_filename}_validation.csv", index=False)


def print_preview(schedule_df: pd.DataFrame) -> None:
    """Print the schedule table to stdout in a human-friendly layout."""

    if schedule_df.empty:
        print("No payouts scheduled for the requested month.")
        return
    preview_df = schedule_df.copy()
    preview_df["Pay Date"] = preview_df["Pay Date"].dt.date
    print(preview_df.to_string(index=False))


def ensure_non_empty_frames(
    schedule_df: pd.DataFrame,
    models_df: pd.DataFrame,
    validation_df: pd.DataFrame,
    currency: str,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Ensure the exported DataFrames contain headers even when empty."""

    amount_column = f"Amount ({currency})"
    if schedule_df.empty:
        schedule_df = pd.DataFrame(
            columns=[
                "Pay Date",
                "Code",
                "Real Name",
                "Working Name",
                "Payment Method",
                "Payment Frequency",
                amount_column,
                "Notes",
            ]
        )
    if models_df.empty:
        models_df = pd.DataFrame(
            columns=[
                "Row",
                "Status",
                "Code",
                "Real Name",
                "Working Name",
                "Start Date",
                "Payment Method",
                "Payment Frequency",
                f"Amount Monthly ({currency})",
                "Validation Messages",
            ]
        )
    if validation_df.empty:
        validation_df = pd.DataFrame(columns=["Row", "Code", "Severity", "Issue"])
    return schedule_df, models_df, validation_df


__all__ = [
    "ModelRecord",
    "ValidationMessage",
    "load_models",
    "parse_models",
    "build_pay_schedule",
    "build_models_table",
    "build_validation_report",
    "get_pay_dates",
    "payout_plan",
    "allocate_amounts",
    "is_eligible_for_date",
    "export_outputs",
    "print_preview",
    "ensure_non_empty_frames",
    "validate_row",
    "parse_date",
    "parse_decimal",
    "normalize_columns",
    "MONEY_QUANT",
]
