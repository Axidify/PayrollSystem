from datetime import date, datetime

import pytest
from fastapi import HTTPException

from app.routers import schedules


def test_resolve_quick_range_days():
    today = date(2025, 10, 20)
    start, end, identifier = schedules._resolve_quick_range("past_7_days", today)
    assert identifier == "past_7_days"
    assert start == date(2025, 10, 14)
    assert end == today


def test_resolve_quick_range_months_handles_rollover():
    today = date(2025, 3, 31)
    start, end, identifier = schedules._resolve_quick_range("past_3_months", today)
    assert identifier == "past_3_months"
    assert end == today
    # February gets clamped to the last valid day
    assert start == date(2024, 12, 31)


def test_parse_date_param_rejects_invalid_strings():
    with pytest.raises(HTTPException):
        schedules._parse_date_param("2025-13-01", "Start date")


def test_filter_runs_by_range_filters_by_cycle_date():
    class DummyRun:
        def __init__(self, created: date, year: int, month: int):
            self.created_at = datetime.combine(created, datetime.min.time())
            self.target_year = year
            self.target_month = month

    runs = [
        DummyRun(date(2025, 6, 1), 2025, 1),
        DummyRun(date(2025, 6, 1), 2025, 2),
        DummyRun(date(2025, 6, 1), 2025, 3),
    ]

    filtered = schedules._filter_runs_by_range(runs, date(2025, 2, 1), date(2025, 3, 1))
    assert len(filtered) == 2
    assert filtered[0].target_month == 2
    assert filtered[1].target_month == 3


def test_filter_runs_by_range_ignores_created_date_outside_range():
    class DummyRun:
        def __init__(self, created: date, year: int, month: int):
            self.created_at = datetime.combine(created, datetime.min.time())
            self.target_year = year
            self.target_month = month

    runs = [
        DummyRun(date(2025, 10, 20), 2025, 1),  # created recently but cycle in January
        DummyRun(date(2025, 10, 20), 2025, 4),
    ]

    filtered = schedules._filter_runs_by_range(runs, date(2025, 1, 1), date(2025, 3, 31))
    assert len(filtered) == 1
    assert filtered[0].target_month == 1


def test_format_range_label_variations():
    today = date(2025, 10, 20)
    assert schedules._format_range_label(today, today, "Fallback") == "Oct 20, 2025 – Oct 20, 2025"
    assert schedules._format_range_label(today, None, "Fallback").startswith("Since")
    assert schedules._format_range_label(None, today, "Fallback").startswith("Through")
    assert schedules._format_range_label(None, None, "Fallback") == "Fallback"
