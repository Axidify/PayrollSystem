from decimal import Decimal
from datetime import date

from app.core.payroll import allocate_amounts, get_pay_dates, payout_plan


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
