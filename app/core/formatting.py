"""Helpers for consistent user-facing date formatting."""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Iterable

DISPLAY_DATE_FORMAT = "%m/%d/%Y"
DISPLAY_DATETIME_FORMAT = "%m/%d/%Y %I:%M %p"
_STRING_PARSE_PATTERNS: Iterable[str] = (
    "%Y-%m-%d",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
)


def _coerce_to_datetime(value: Any) -> datetime | None:
    """Attempt to normalise incoming date-like values to a datetime."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            if text.endswith("Z"):
                try:
                    return datetime.fromisoformat(text.replace("Z", "+00:00"))
                except ValueError:
                    pass
            for pattern in _STRING_PARSE_PATTERNS:
                try:
                    return datetime.strptime(text, pattern)
                except ValueError:
                    continue
        return None
    return None


def format_display_date(value: Any) -> str:
    """Format a value as mm/dd/yyyy or return an empty string."""
    coerced = _coerce_to_datetime(value)
    if coerced is None:
        return "" if value in (None, "") else str(value)
    return coerced.strftime(DISPLAY_DATE_FORMAT)


def format_display_datetime(value: Any) -> str:
    """Format a value as mm/dd/yyyy hh:mm AM/PM or return an empty string."""
    coerced = _coerce_to_datetime(value)
    if coerced is None:
        return "" if value in (None, "") else str(value)
    return coerced.strftime(DISPLAY_DATETIME_FORMAT)


__all__ = [
    "format_display_date",
    "format_display_datetime",
]
