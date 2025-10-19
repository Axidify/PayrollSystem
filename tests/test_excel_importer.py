from __future__ import annotations

from io import BytesIO

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.importers.excel_importer import ImportOptions, RunOptions, import_from_excel
from app.models import Model, Payout, ScheduleRun


def _build_workbook() -> bytes:
    models_df = pd.DataFrame(
        [
            {
                "Code": "ALPHA1",
                "Status": "Active",
                "Real Name": "Alex Smith",
                "Working Name": "Alpha",
                "Start Date": "2024-01-01",
                "Payment Method": "Wire",
                "Payment Frequency": "Monthly",
                "Monthly Amount": 5000,
                "Crypto Wallet": "",
            }
        ]
    )
    payouts_df = pd.DataFrame(
        [
            {
                "Code": "ALPHA1",
                "Pay Date": "2024-02-01",
                "Amount": 2500,
                "Status": "Paid",
            }
        ]
    )

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        models_df.to_excel(writer, sheet_name="Models", index=False)
        payouts_df.to_excel(writer, sheet_name="Payouts", index=False)
    return buffer.getvalue()


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()


def test_import_payouts_without_name_columns():
    session = _make_session()
    try:
        workbook_bytes = _build_workbook()
        import_options = ImportOptions()
        run_options = RunOptions(
            create_schedule_run=True,
            target_year=2024,
            target_month=2,
            currency="USD",
            export_dir="exports",
            auto_generate_runs=False,
        )

        summary = import_from_excel(session, workbook_bytes, import_options, run_options)

        assert summary.model_errors == []
        assert summary.payout_errors == []
        assert summary.payouts_created == 1
        assert summary.schedule_run_id is not None

        model = session.query(Model).filter_by(code="ALPHA1").one()
        payout = session.query(Payout).one()
        run = session.query(ScheduleRun).one()

        assert payout.model_id == model.id
        assert payout.schedule_run_id == run.id
        assert payout.real_name == model.real_name
        assert payout.working_name == model.working_name
        assert payout.payment_method == model.payment_method
        assert payout.payment_frequency == model.payment_frequency
    finally:
        session.close()
