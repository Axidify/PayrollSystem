from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO

import pandas as pd
import pytest
import sys
import types
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Provide a lightweight bcrypt stub for modules that expect it in test context.
if "bcrypt" not in sys.modules:
    mock_bcrypt = types.ModuleType("bcrypt")

    def _gensalt() -> bytes:
        return b"test-salt"

    def _hashpw(password: bytes, salt: bytes) -> bytes:
        return b"test-hash"

    def _checkpw(password: bytes, hashed: bytes) -> bool:
        return True

    mock_bcrypt.gensalt = _gensalt
    mock_bcrypt.hashpw = _hashpw
    mock_bcrypt.checkpw = _checkpw
    sys.modules["bcrypt"] = mock_bcrypt

from app import crud
from app.database import Base
from app.importers.excel_importer import ImportOptions, RunOptions, import_from_excel
from app.models import Model, ModelCompensationAdjustment
from app.schemas import ModelCreate
from app.routers import models as model_routes


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()


def test_create_model_seeds_adjustment():
    session = _make_session()
    try:
        payload = ModelCreate(
            status="Active",
            code="MOD-1",
            real_name="Real Name",
            working_name="Working Name",
            start_date=date(2024, 1, 1),
            payment_method="Wire",
            payment_frequency="monthly",
            amount_monthly=Decimal("5000"),
            crypto_wallet=None,
        )
        model = crud.create_model(session, payload)
        adjustments = (
            session.query(ModelCompensationAdjustment)
            .filter(ModelCompensationAdjustment.model_id == model.id)
            .all()
        )
        assert len(adjustments) == 1
        assert adjustments[0].effective_date == payload.start_date
        assert adjustments[0].amount_monthly == payload.amount_monthly
    finally:
        session.close()


def test_effective_amount_with_future_adjustment():
    session = _make_session()
    try:
        payload = ModelCreate(
            status="Active",
            code="MOD-2",
            real_name="Name",
            working_name="Alias",
            start_date=date(2024, 1, 1),
            payment_method="Wire",
            payment_frequency="monthly",
            amount_monthly=Decimal("4000"),
            crypto_wallet=None,
        )
        model = crud.create_model(session, payload)
        crud.create_compensation_adjustment(
            session,
            model,
            effective_date=date(2024, 6, 1),
            amount_monthly=Decimal("4500"),
            notes="Mid-year raise",
        )
        session.commit()

        before = crud.get_effective_compensation_amount(session, model, date(2024, 5, 1))
        after = crud.get_effective_compensation_amount(session, model, date(2024, 7, 1))

        assert before == Decimal("4000")
        assert after == Decimal("4500")
    finally:
        session.close()


def test_import_with_compensation_adjustments_sheet():
    session = _make_session()
    try:
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
        adjustments_df = pd.DataFrame(
            [
                {
                    "Code": "ALPHA1",
                    "Effective Date": date.today().isoformat(),
                    "Monthly Amount": 6000,
                    "Notes": "Annual increase",
                }
            ]
        )

        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            models_df.to_excel(writer, sheet_name="Models", index=False)
            payouts_df.to_excel(writer, sheet_name="Payouts", index=False)
            adjustments_df.to_excel(writer, sheet_name="CompensationAdjustments", index=False)

        import_options = ImportOptions()
        run_options = RunOptions(
            create_schedule_run=True,
            target_year=2024,
            target_month=2,
            currency="USD",
            export_dir="exports",
            auto_generate_runs=False,
        )

        summary = import_from_excel(session, buffer.getvalue(), import_options, run_options)
        session.commit()

        assert summary.adjustments_created == 1
        assert summary.adjustments_updated == 0
        assert summary.adjustment_errors == []

        model = session.query(Model).filter_by(code="ALPHA1").one()
        assert model.amount_monthly == Decimal("6000")

        new_adjustment = (
            session.query(ModelCompensationAdjustment)
            .filter(ModelCompensationAdjustment.amount_monthly == Decimal("6000"))
            .one()
        )
        assert new_adjustment.effective_date == date.today()
    finally:
        session.close()


def test_parse_adjustments_accepts_dates_after_start():
    baseline = date(2024, 1, 1)
    parsed = model_routes._parse_adjustment_rows(
        ["2024-03-01"],
        ["5500"],
        ["Raise"],
        baseline,
    )
    assert parsed == [(date(2024, 3, 1), Decimal("5500.00"), "Raise")]


def test_parse_adjustments_rejects_dates_before_start():
    baseline = date(2024, 1, 1)
    with pytest.raises(model_routes.HTTPException) as excinfo:
        model_routes._parse_adjustment_rows(
            ["2023-12-01"],
            ["4500"],
            ["Backdated"],
            baseline,
        )
    assert "on or after" in excinfo.value.detail
