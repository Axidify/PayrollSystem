"""Database configuration for the payroll web application."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

DEFAULT_SQLITE_PATH = Path("data/payroll.db")
DEFAULT_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("PAYROLL_DATABASE_URL", f"sqlite:///{DEFAULT_SQLITE_PATH}")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

Base = declarative_base()


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session."""

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    """Ensure database tables exist."""

    from app import models  # noqa: F401  (import ensures model metadata is registered)

    # For SQLite, checkfirst=True sometimes fails with "table already exists"
    # Instead, inspect existing tables and only create missing ones
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    
    for table in Base.metadata.tables.values():
        if table.name not in existing_tables:
            table.create(bind=engine, checkfirst=True)
    
    ensure_schema_updates()


def ensure_schema_updates() -> None:
    inspector = inspect(engine)
    existing_columns = {column["name"] for column in inspector.get_columns("payouts")}
    if "status" not in existing_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE payouts ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'not_paid'"))
