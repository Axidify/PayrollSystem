"""Pydantic schemas for API responses and forms."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from app.models import ADHOC_PAYMENT_STATUS_ENUM, FREQUENCY_ENUM, STATUS_ENUM


class ModelBase(BaseModel):
    status: str = Field(..., pattern="|".join(STATUS_ENUM))
    code: str = Field(..., min_length=1, max_length=50)
    real_name: str = Field(..., min_length=1, max_length=200)
    working_name: str = Field(..., min_length=1, max_length=200)
    start_date: date
    payment_method: str = Field(..., min_length=1, max_length=100)
    payment_frequency: str
    amount_monthly: Decimal = Field(..., gt=0)
    crypto_wallet: Optional[str] = Field(None, max_length=200)

    @field_validator("status")
    def validate_status(cls, value: str) -> str:
        value_title = value.title()
        if value_title not in STATUS_ENUM:
            raise ValueError("Status must be Active or Inactive.")
        return value_title

    @field_validator("code", "real_name", "working_name", "payment_method", mode="before")
    def strip_required_strings(cls, value: Any, info: ValidationInfo) -> str:
        if value is None:
            raise ValueError(f"{info.field_name.replace('_', ' ').title()} is required.")
        value_str = str(value).strip()
        if not value_str:
            raise ValueError(f"{info.field_name.replace('_', ' ').title()} cannot be empty.")
        return value_str

    @field_validator("start_date", mode="before")
    def ensure_start_date_present(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("Start date is required.")
        if isinstance(value, str) and not value.strip():
            raise ValueError("Start date is required.")
        return value

    @field_validator("payment_frequency")
    def validate_frequency(cls, value: str) -> str:
        value_lower = value.lower()
        if value_lower not in FREQUENCY_ENUM:
            raise ValueError("Payment frequency must be weekly, biweekly, or monthly.")
        return value_lower

    @field_validator("amount_monthly")
    def quantize_amount(cls, value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    model_config = ConfigDict(from_attributes=True)


class ModelCreate(ModelBase):
    pass


class ModelUpdate(ModelBase):
    pass


class ModelRead(ModelBase):
    id: int
    created_at: datetime
    updated_at: datetime


class ScheduleRunBase(BaseModel):
    target_year: int
    target_month: int
    currency: str = "USD"
    include_inactive: bool = False


class ScheduleRunRead(ScheduleRunBase):
    id: int
    summary_models_paid: int
    summary_total_payout: Decimal
    summary_frequency_counts: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PayoutRead(BaseModel):
    id: int
    pay_date: date
    code: str
    real_name: str
    working_name: str
    payment_method: str
    payment_frequency: str
    amount: Decimal
    notes: Optional[str]

    model_config = ConfigDict(from_attributes=True)


class ValidationIssueRead(BaseModel):
    id: int
    severity: str
    issue: str
    model_id: Optional[int]

    model_config = ConfigDict(from_attributes=True)


class AdhocPaymentBase(BaseModel):
    pay_date: date
    amount: Decimal = Field(..., gt=0)
    description: Optional[str] = Field(None, max_length=255)
    notes: Optional[str]
    status: str = Field(default="pending")

    @field_validator("status")
    def validate_status(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ADHOC_PAYMENT_STATUS_ENUM:
            raise ValueError("Status must be pending, paid, or cancelled.")
        return normalized

    @field_validator("amount")
    def quantize_amount(cls, value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    model_config = ConfigDict(from_attributes=True)


class AdhocPaymentCreate(AdhocPaymentBase):
    status: str = "pending"


class AdhocPaymentUpdate(BaseModel):
    pay_date: Optional[date] = None
    amount: Optional[Decimal] = None
    description: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = None
    status: Optional[str] = None

    @field_validator("status")
    def validate_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in ADHOC_PAYMENT_STATUS_ENUM:
            raise ValueError("Status must be pending, paid, or cancelled.")
        return normalized

    @field_validator("amount")
    def quantize_amount(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class AdhocPaymentRead(AdhocPaymentBase):
    id: int
    created_at: datetime
    updated_at: datetime
