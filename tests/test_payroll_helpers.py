from decimal import Decimal
from datetime import date

from app.core.payroll import (
    ModelRecord,
    allocate_amounts,
    build_pay_schedule,
    get_pay_dates,
    payout_plan,
)


def test_get_pay_dates_returns_expected_dates():
    dates = get_pay_dates(2025, 2)
    assert dates == [
        date(2025, 2, 7),
        date(2025, 2, 14),
        date(2025, 2, 21),
        date(2025, 2, 28),
    ]


def test_allocate_amounts_weekly_even_split():
    amounts, adjusted = allocate_amounts(Decimal("1000"), "weekly")
    assert amounts == [Decimal("250.00")] * 4
    assert adjusted is False


def test_allocate_amounts_handles_rounding_adjustment():
    amounts, adjusted = allocate_amounts(Decimal("1000.10"), "weekly")
    assert sum(amounts) == Decimal("1000.10")
    assert amounts[-1] != amounts[0]
    assert adjusted is True


def test_payout_plan_mapping():
    assert payout_plan("biweekly") == [1, 3]
    assert payout_plan("monthly") == [3]
    assert payout_plan("unknown") == []


def test_build_pay_schedule_respects_compensation_adjustments():
    record = ModelRecord(
        row_number=1,
        status="Active",
        code="RAISE1",
        real_name="Real Name",
        working_name="Working",
        start_date=date(2025, 10, 1),
        payment_method="Wire",
        payment_frequency="weekly",
        amount_monthly=Decimal("2000"),
        compensation_adjustments=[
            (date(2025, 10, 1), Decimal("1000")),
            (date(2025, 10, 14), Decimal("2000")),
        ],
    )

    schedule_df, summary = build_pay_schedule([record], 2025, 10, "USD")
    amounts = schedule_df[f"Amount (USD)"]

    assert list(amounts) == [250.0, 500.0, 500.0, 500.0]
    assert summary["total_payout"] == 1750.0
