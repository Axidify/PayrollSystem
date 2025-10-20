from __future__ import annotations

from datetime import date
from decimal import Decimal
import sys
import types

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Provide a lightweight bcrypt stub so unit tests do not require the optional dependency.
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
from app.models import AdhocPayment
from app.schemas import AdhocPaymentCreate, AdhocPaymentUpdate, ModelCreate


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()


def _seed_model(session):
    payload = ModelCreate(
        status="Active",
        code="ADHOC-1",
        real_name="Ad Hoc Model",
        working_name="Ad Hoc",
        start_date=date(2025, 1, 1),
        payment_method="Wire",
        payment_frequency="monthly",
        amount_monthly=Decimal("5000"),
        crypto_wallet=None,
    )
    return crud.create_model(session, payload)


def test_create_and_list_adhoc_payments():
    session = _make_session()
    try:
        model = _seed_model(session)
        payload = AdhocPaymentCreate(
            pay_date=date(2025, 2, 15),
            amount=Decimal("150.50"),
            description="One-off bonus",
            notes="Handle by Friday",
        )
        crud.create_adhoc_payment(session, model, payload)

        results = crud.list_adhoc_payments(session, model.id)
        assert len(results) == 1
        payment = results[0]
        assert payment.description == "One-off bonus"
        assert payment.status == "pending"
        assert payment.amount == Decimal("150.50")
    finally:
        session.close()


def test_status_transitions_and_notes_update():
    session = _make_session()
    try:
        model = _seed_model(session)
        payment = crud.create_adhoc_payment(
            session,
            model,
            AdhocPaymentCreate(
                pay_date=date(2025, 3, 1),
                amount=Decimal("200"),
                description="Travel reimbursement",
                notes=None,
            ),
        )

        crud.set_adhoc_payment_status(session, payment, "paid")
        refreshed = session.get(AdhocPayment, payment.id)
        assert refreshed.status == "paid"

        crud.update_adhoc_payment(
            session,
            refreshed,
            AdhocPaymentUpdate(notes=" Reimbursed by accounts "),
        )
        refreshed = session.get(AdhocPayment, payment.id)
        assert refreshed.notes == "Reimbursed by accounts"

        crud.set_adhoc_payment_status(session, refreshed, "cancelled")
        refreshed = session.get(AdhocPayment, payment.id)
        assert refreshed.status == "cancelled"
    finally:
        session.close()


def test_delete_adhoc_payment():
    session = _make_session()
    try:
        model = _seed_model(session)
        payment = crud.create_adhoc_payment(
            session,
            model,
            AdhocPaymentCreate(
                pay_date=date(2025, 4, 10),
                amount=Decimal("75"),
                description="Equipment purchase",
                notes=None,
            ),
        )

        crud.delete_adhoc_payment(session, payment)
        remaining = crud.list_adhoc_payments(session, model.id)
        assert remaining == []
    finally:
        session.close()


def test_list_adhoc_payments_for_month():
    session = _make_session()
    try:
        model = _seed_model(session)
        other_model = crud.create_model(
            session,
            ModelCreate(
                status="Active",
                code="ADHOC-2",
                real_name="Second Model",
                working_name="Second",
                start_date=date(2025, 1, 1),
                payment_method="Wire",
                payment_frequency="monthly",
                amount_monthly=Decimal("4000"),
                crypto_wallet=None,
            ),
        )

        first = crud.create_adhoc_payment(
            session,
            model,
            AdhocPaymentCreate(
                pay_date=date(2025, 5, 1),
                amount=Decimal("125.00"),
                description="First May payment",
                notes=None,
            ),
        )
        second = crud.create_adhoc_payment(
            session,
            other_model,
            AdhocPaymentCreate(
                pay_date=date(2025, 5, 20),
                amount=Decimal("250.00"),
                description="Second May payment",
                status="paid",
                notes=None,
            ),
        )
        crud.create_adhoc_payment(
            session,
            model,
            AdhocPaymentCreate(
                pay_date=date(2025, 6, 5),
                amount=Decimal("300.00"),
                description="June payment",
                notes=None,
            ),
        )

        results = crud.list_adhoc_payments_for_month(session, 2025, 5)
        assert [payment.id for payment in results] == [first.id, second.id]
        assert results[0].pay_date == date(2025, 5, 1)
        assert results[1].model.code == "ADHOC-2"

        paid_only = crud.list_adhoc_payments_for_month(session, 2025, 5, status="paid")
        assert [payment.id for payment in paid_only] == [second.id]
        # Ensure model relationship is eager loaded for schedule views
        assert paid_only[0].model.working_name == "Second"
    finally:
        session.close()
