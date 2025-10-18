"""Application service layer."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Tuple

import pandas as pd
from sqlalchemy.orm import Session

from app.core.payroll import (
    ModelRecord,
    build_models_table,
    build_pay_schedule,
    build_validation_report,
    ensure_non_empty_frames,
    export_outputs,
    validate_row,
)
from app import crud
from app.models import Model


class PayrollService:
    """Coordinates payroll operations using database state."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def list_models(self) -> Iterable[Model]:
        return crud.list_models(self.db)

    def create_model(self, payload) -> Model:
        return crud.create_model(self.db, payload)

    def update_model(self, model: Model, payload) -> Model:
        return crud.update_model(self.db, model, payload)

    def delete_model(self, model: Model) -> None:
        crud.delete_model(self.db, model)

    def run_payroll(
        self,
        target_year: int,
        target_month: int,
        currency: str,
        include_inactive: bool,
        output_dir: Path,
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict, int]:
        models = crud.list_models(self.db)
        records = [self._to_record(index, model) for index, model in enumerate(models, start=1)]

        schedule_df, summary = build_pay_schedule(records, target_year, target_month, currency)
        models_df = build_models_table(records, currency)
        validation_df = build_validation_report(records, include_inactive)

        schedule_df, models_df, validation_df = ensure_non_empty_frames(
            schedule_df, models_df, validation_df, currency
        )

        run = crud.create_schedule_run(
            self.db,
            target_year=target_year,
            target_month=target_month,
            currency=currency,
            include_inactive=include_inactive,
            summary=summary,
            export_path=str(output_dir),
        )

        amount_column = f"Amount ({currency})"
        payout_records = schedule_df.to_dict(orient="records")
        for payout in payout_records:
            pay_date_value = payout.get("Pay Date")
            if hasattr(pay_date_value, "date"):
                payout["Pay Date"] = pay_date_value.date()
            amount_value = payout.get(amount_column)
            if amount_value is not None:
                payout[amount_column] = Decimal(str(amount_value))
            notes_value = payout.get("Notes")
            if notes_value is None or (isinstance(notes_value, float) and pd.isna(notes_value)):
                payout["Notes"] = None

        crud.store_payouts(
            self.db,
            run,
            payout_records,
            amount_column=amount_column,
        )
        crud.store_validation_messages(self.db, run, records, include_inactive)

        export_outputs(
            base_filename=f"pay_schedule_{target_year:04d}_{target_month:02d}_run{run.id}",
            schedule_df=schedule_df,
            models_df=models_df,
            validation_df=validation_df,
            output_dir=output_dir,
        )

        return schedule_df, models_df, validation_df, summary, run.id

    def _to_record(self, position: int, model: Model) -> ModelRecord:
        record = ModelRecord(
            row_number=position + 1,
            status=model.status,
            code=model.code,
            real_name=model.real_name,
            working_name=model.working_name,
            start_date=model.start_date,
            payment_method=model.payment_method,
            payment_frequency=model.payment_frequency.lower(),
            amount_monthly=Decimal(model.amount_monthly),
        )
        for message in validate_row(record):
            record.add_message(message.level, message.text)
        return record
