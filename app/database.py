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
    """Ensure database tables exist and create default admin user if needed."""

    from app import models  # noqa: F401  (import ensures model metadata is registered)
    from app.auth import User  # noqa: F401  (import ensures User model is registered)

    # Try to create all tables; if they already exist, skip
    try:
        Base.metadata.create_all(bind=engine, checkfirst=True)
    except Exception as e:
        # If table creation fails due to existing tables, just log and continue
        if "already exists" in str(e).lower():
            print(f"[init_db] Tables already exist, skipping creation: {e}")
        else:
            raise
    
    # Create default admin user if it doesn't exist
    session = SessionLocal()
    try:
        from app.auth import User
        existing_admin = session.query(User).filter(User.username == "admin").first()
        if not existing_admin:
            admin_user = User.create_user("admin", "admin")
            session.add(admin_user)
            session.commit()
            print("[init_db] Created default admin user (username: admin, password: admin)")
    finally:
        session.close()
    
    ensure_schema_updates()


def ensure_schema_updates() -> None:
    inspector = inspect(engine)
    existing_columns = {column["name"] for column in inspector.get_columns("payouts")}
    if "status" not in existing_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE payouts ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'not_paid'"))
