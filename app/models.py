"""SQLAlchemy models for the payroll application."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

STATUS_ENUM = ("Active", "Inactive")
FREQUENCY_ENUM = ("weekly", "biweekly", "monthly")
PAYOUT_STATUS_ENUM = ("paid", "on_hold", "not_paid")
ADHOC_PAYMENT_STATUS_ENUM = ("pending", "paid", "cancelled")


class Model(Base):
    __tablename__ = "models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="Active")
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    real_name: Mapped[str] = mapped_column(String(200), nullable=False)
    working_name: Mapped[str] = mapped_column(String(200), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    payment_method: Mapped[str] = mapped_column(String(100), nullable=False)
    payment_frequency: Mapped[str] = mapped_column(String(20), nullable=False)
    amount_monthly: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    crypto_wallet: Mapped[str] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, nullable=False
    )

    payouts: Mapped[list["Payout"]] = relationship(back_populates="model", cascade="all, delete-orphan")
    validations: Mapped[list["ValidationIssue"]] = relationship(
        back_populates="model", cascade="all, delete-orphan"
    )
    compensation_adjustments: Mapped[list["ModelCompensationAdjustment"]] = relationship(
        back_populates="model",
        cascade="all, delete-orphan",
        order_by="ModelCompensationAdjustment.effective_date",
    )
    adhoc_payments: Mapped[list["AdhocPayment"]] = relationship(
        back_populates="model",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("amount_monthly > 0", name="ck_models_amount_positive"),
    )


class ScheduleRun(Base):
    __tablename__ = "schedule_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_year: Mapped[int] = mapped_column(Integer, nullable=False)
    target_month: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="USD")
    include_inactive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    summary_models_paid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary_total_payout: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    summary_frequency_counts: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    export_path: Mapped[str] = mapped_column(String(255), nullable=False, default="exports")

    payouts: Mapped[list["Payout"]] = relationship(back_populates="schedule_run", cascade="all, delete-orphan")
    validations: Mapped[list["ValidationIssue"]] = relationship(
        back_populates="schedule_run", cascade="all, delete-orphan"
    )


class Payout(Base):
    __tablename__ = "payouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    schedule_run_id: Mapped[int] = mapped_column(ForeignKey("schedule_runs.id", ondelete="CASCADE"), nullable=False)
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id", ondelete="SET NULL"), nullable=True)
    pay_date: Mapped[date] = mapped_column(Date, nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    real_name: Mapped[str] = mapped_column(String(200), nullable=False)
    working_name: Mapped[str] = mapped_column(String(200), nullable=False)
    payment_method: Mapped[str] = mapped_column(String(100), nullable=False)
    payment_frequency: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="not_paid")

    schedule_run: Mapped[ScheduleRun] = relationship(back_populates="payouts")
    model: Mapped[Model] = relationship(back_populates="payouts")


class ValidationIssue(Base):
    __tablename__ = "validation_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    schedule_run_id: Mapped[int] = mapped_column(ForeignKey("schedule_runs.id", ondelete="CASCADE"), nullable=False)
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id", ondelete="SET NULL"), nullable=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    issue: Mapped[str] = mapped_column(Text, nullable=False)

    schedule_run: Mapped[ScheduleRun] = relationship(back_populates="validations")
    model: Mapped[Model] = relationship(back_populates="validations")


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    success: Mapped[bool] = mapped_column(default=False, nullable=False)
    ip_address: Mapped[str] = mapped_column(String(50), nullable=True)
    user_agent: Mapped[str] = mapped_column(Text, nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False, index=True)

    __table_args__ = (
        Index("idx_failed_attempts", "username", "success", "attempted_at"),
    )


class ModelCompensationAdjustment(Base):
    __tablename__ = "model_compensation_adjustments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[int] = mapped_column(
        ForeignKey("models.id", ondelete="CASCADE"), nullable=False, index=True
    )
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_monthly: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)

    model: Mapped[Model] = relationship(back_populates="compensation_adjustments")

    __table_args__ = (
        UniqueConstraint("model_id", "effective_date", name="uq_adjustment_model_date"),
        CheckConstraint("amount_monthly > 0", name="ck_adjustment_amount_positive"),
    )


class AdhocPayment(Base):
    __tablename__ = "adhoc_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id", ondelete="CASCADE"), nullable=False, index=True)
    pay_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, nullable=False
    )

    model: Mapped[Model] = relationship(back_populates="adhoc_payments")

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_adhoc_payments_amount_positive"),
        CheckConstraint(
            "status IN ('pending', 'paid', 'cancelled')",
            name="ck_adhoc_payments_status_valid",
        ),
    )
