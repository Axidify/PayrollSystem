"""Shared FastAPI dependencies."""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.database import get_session

TEMPLATES_PATH = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_PATH))

get_db = get_session
