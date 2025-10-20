"""Shared FastAPI dependencies."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.core.formatting import format_display_date, format_display_datetime
from app.database import get_session

TEMPLATES_PATH = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_PATH))


def _format_money(value) -> str:
    """Format numeric values with thousand separators and two decimals."""

    if value in (None, ""):
        decimal_value = Decimal("0")
    else:
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return str(value)

    decimal_value = decimal_value.quantize(Decimal("0.01"))
    return f"{decimal_value:,.2f}"


def _format_display_date(value) -> str:
    """Expose consistent mm/dd/yyyy formatting to templates."""

    return format_display_date(value)


def _format_display_datetime(value) -> str:
    """Expose consistent mm/dd/yyyy hh:mm AM/PM formatting to templates."""

    return format_display_datetime(value)


templates.env.filters["money"] = _format_money
templates.env.filters["display_date"] = _format_display_date
templates.env.filters["display_datetime"] = _format_display_datetime

get_db = get_session
