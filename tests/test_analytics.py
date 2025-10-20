from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import User
from app.database import Base, get_session
from app.main import app
from app.models import (
    AdhocPayment,
    Model,
    ModelCompensationAdjustment,
    Payout,
    ScheduleRun,
)
from app.routers.auth import get_current_user


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session):
    user = User.create_user("analytics-tester", "secret", role="admin")
    db_session.add(user)
    db_session.commit()

    def override_session():
        try:
            yield db_session
        finally:
            db_session.rollback()

    def override_user():
        return user

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = override_user

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_current_user, None)


def _seed_data(session):
    today = date.today()
    model = Model(
        status="Active",
        code="MODEL100",
        real_name="Model R",
        working_name="Model W",
        start_date=today - timedelta(days=90),
        payment_method="ACH",
        payment_frequency="monthly",
        amount_monthly=Decimal("5000.00"),
        crypto_wallet="0xABC",
    )
    session.add(model)
    session.flush()

    run = ScheduleRun(
        target_year=today.year,
        target_month=today.month,
        currency="USD",
        include_inactive=False,
        summary_models_paid=1,
        summary_total_payout=Decimal("1500.00"),
        summary_frequency_counts="{}",
    )
    session.add(run)
    session.flush()

    payout = Payout(
        schedule_run_id=run.id,
        model_id=model.id,
        pay_date=today,
        code=model.code,
        real_name=model.real_name,
        working_name=model.working_name,
        payment_method="ACH",
        payment_frequency="monthly",
        amount=Decimal("1500.00"),
        status="paid",
    )
    session.add(payout)

    adhoc = AdhocPayment(
        model_id=model.id,
        pay_date=today,
        amount=Decimal("250.00"),
        description="Spot bonus",
        status="paid",
    )
    session.add(adhoc)

    adjustment = ModelCompensationAdjustment(
        model_id=model.id,
        effective_date=today,
        amount_monthly=Decimal("5200.00"),
        notes="Annual raise",
    )
    session.add(adjustment)

    session.commit()


def test_analytics_data_returns_expected_datasets(client, db_session):
    _seed_data(db_session)
    today = date.today()
    params = {
        "start": (today - timedelta(days=1)).isoformat(),
        "end": (today + timedelta(days=1)).isoformat(),
        "datasets": "payouts,adhoc,adjustments,runs",
    }

    response = client.get("/analytics/data", params=params)
    assert response.status_code == 200
    payload = response.json()

    assert set(payload["results"].keys()) == {"payouts", "adhoc", "adjustments", "runs"}
    assert payload["meta"]["counts"]["payouts"] == 1
    assert payload["meta"]["counts"]["adhoc"] == 1
    assert payload["meta"]["counts"]["adjustments"] == 1
    assert payload["meta"]["counts"]["runs"] >= 1
    assert payload["meta"]["start"] == params["start"]
    assert payload["meta"]["end"] == params["end"]
    assert payload["meta"]["totals"]["paid"] == pytest.approx(1750.0)
    assert payload["meta"]["totals"]["unpaid"] == pytest.approx(0.0)


def test_analytics_defaults_to_payouts_dataset(client, db_session):
    _seed_data(db_session)
    response = client.get("/analytics/data")
    assert response.status_code == 200
    payload = response.json()
    assert payload["results"].keys() == {"payouts"}
    assert payload["meta"]["counts"]["payouts"] == 1
    assert payload["meta"]["totals"]["paid"] == pytest.approx(1500.0)
    assert payload["meta"]["totals"]["unpaid"] == pytest.approx(0.0)
