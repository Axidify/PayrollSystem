"""Database configuration for the payroll web application."""
from __future__ import annotations

import os
from datetime import date, datetime
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
    from app.auth import User

    # Try to create all tables; if they already exist, skip
    try:
        Base.metadata.create_all(bind=engine, checkfirst=True)
    except Exception as e:
        # If table creation fails due to existing tables, just log and continue
        if "already exists" in str(e).lower():
            print(f"[init_db] Tables already exist, skipping creation: {e}")
        else:
            print(f"[init_db] Warning during table creation: {e}")
    
    # Create default admin user if it doesn't exist
    session = SessionLocal()
    try:
        # Check if admin user exists
        admin_count = session.query(User).filter(User.username == "admin").count()
        if admin_count == 0:
            # Admin doesn't exist, create it
            admin_user = User.create_user("admin", "admin", role="admin")
            session.add(admin_user)
            session.commit()
            print("[init_db] Created default admin user (username: admin, password: admin, role: admin)")
        else:
            print("[init_db] Admin user already exists, skipping creation")
    except Exception as e:
        print(f"[init_db] Error with admin user: {type(e).__name__}: {e}")
        try:
            session.rollback()
        except:
            pass
    finally:
        try:
            session.close()
        except:
            pass
    
        ensure_schema_updates()


def ensure_schema_updates() -> None:
    """Ensure all required columns exist in the database tables."""
    from app.models import Model, ModelCompensationAdjustment

    inspector = inspect(engine)
    
    # Remove is_active column from users table (migration from soft-delete to hard-delete)
    try:
        users_columns = {column["name"] for column in inspector.get_columns("users")}
        if "is_active" in users_columns:
            print("[ensure_schema_updates] Removing deprecated is_active column from users table")
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE users DROP COLUMN is_active"))
                print("[ensure_schema_updates] Successfully removed is_active column")
    except Exception as e:
        print(f"[ensure_schema_updates] Error removing is_active column: {e}")
    
    # Ensure users table has role column
    try:
        users_columns = {column["name"] for column in inspector.get_columns("users")}
        if "role" not in users_columns:
            print("[ensure_schema_updates] Adding role column to users table")
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR(50) NOT NULL DEFAULT 'user'"))
                print("[ensure_schema_updates] Successfully added role column to users table")
        else:
            # Column exists, but make sure admin user has admin role
            print("[ensure_schema_updates] Checking and fixing admin user role")
            with engine.begin() as connection:
                # Update any admin user to have admin role
                connection.execute(text("UPDATE users SET role = 'admin' WHERE username = 'admin' AND role != 'admin'"))
                # Ensure no NULL roles exist
                connection.execute(text("UPDATE users SET role = 'user' WHERE role IS NULL"))
    except Exception as e:
        print(f"[ensure_schema_updates] Error updating users table: {e}")
    
    # Ensure payouts table has status column
    try:
        payouts_columns = {column["name"] for column in inspector.get_columns("payouts")}
        if "status" not in payouts_columns:
            print("[ensure_schema_updates] Adding status column to payouts table")
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE payouts ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'not_paid'"))
                print("[ensure_schema_updates] Successfully added status column to payouts table")
    except Exception as e:
        print(f"[ensure_schema_updates] Error updating payouts table: {e}")
    
    # Ensure models table has crypto_wallet column
    try:
        models_columns = {column["name"] for column in inspector.get_columns("models")}
        if "crypto_wallet" not in models_columns:
            print("[ensure_schema_updates] Adding crypto_wallet column to models table")
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE models ADD COLUMN crypto_wallet VARCHAR(200)"))
                print("[ensure_schema_updates] Successfully added crypto_wallet column to models table")
    except Exception as e:
        print(f"[ensure_schema_updates] Error updating models table: {e}")
    
    # Ensure users table has security fields
    try:
        users_columns = {column["name"] for column in inspector.get_columns("users")}
        if "is_locked" not in users_columns:
            print("[ensure_schema_updates] Adding security fields to users table")
            with engine.begin() as connection:
                is_postgres = DATABASE_URL.startswith("postgresql")
                
                # Add is_locked column
                connection.execute(text("ALTER TABLE users ADD COLUMN is_locked BOOLEAN NOT NULL DEFAULT false"))
                
                # Add locked_until column (use TIMESTAMP for PostgreSQL, DATETIME for SQLite)
                datetime_type = "TIMESTAMP" if is_postgres else "DATETIME"
                connection.execute(text(f"ALTER TABLE users ADD COLUMN locked_until {datetime_type}"))
                
                # Add failed_login_count column
                connection.execute(text("ALTER TABLE users ADD COLUMN failed_login_count INTEGER NOT NULL DEFAULT 0"))
                
                # Add last_failed_login column
                connection.execute(text(f"ALTER TABLE users ADD COLUMN last_failed_login {datetime_type}"))
                
                print("[ensure_schema_updates] Successfully added security fields to users table")
    except Exception as e:
        print(f"[ensure_schema_updates] Error updating users table: {e}")

        # Ensure compensation adjustments table exists and is populated from existing models
        try:
            tables = inspector.get_table_names()
        except Exception as e:
            print(f"[ensure_schema_updates] Error listing tables: {e}")
            tables = []

        try:
            if "model_compensation_adjustments" not in tables:
                print("[ensure_schema_updates] Creating model_compensation_adjustments table")
                ModelCompensationAdjustment.__table__.create(bind=engine, checkfirst=True)
        except Exception as e:
            print(f"[ensure_schema_updates] Error creating model_compensation_adjustments table: {e}")

        session = SessionLocal()
        try:
            for model in session.query(Model).all():
                existing = (
                    session.query(ModelCompensationAdjustment)
                    .filter(ModelCompensationAdjustment.model_id == model.id)
                    .first()
                )
                if existing:
                    continue
                effective_date = model.start_date or date.today()
                adjustment = ModelCompensationAdjustment(
                    model_id=model.id,
                    effective_date=effective_date,
                    amount_monthly=model.amount_monthly,
                    notes="Seeded from existing model record",
                )
                session.add(adjustment)
            session.commit()
        except Exception as e:
            print(f"[ensure_schema_updates] Error seeding compensation adjustments: {e}")
            try:
                session.rollback()
            except Exception:
                pass
        finally:
            session.close()

