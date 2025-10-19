"""Pydantic schemas for API responses and forms."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from pydantic import BaseModel, Field, validator

from app.models import FREQUENCY_ENUM, STATUS_ENUM


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

    @validator("status")
    def validate_status(cls, value: str) -> str:
        value_title = value.title()
        if value_title not in STATUS_ENUM:
            raise ValueError("Status must be Active or Inactive.")
        return value_title

    @validator("payment_frequency")
    def validate_frequency(cls, value: str) -> str:
        value_lower = value.lower()
        if value_lower not in FREQUENCY_ENUM:
            raise ValueError("Payment frequency must be weekly, biweekly, or monthly.")
        return value_lower

    @validator("amount_monthly")
    def quantize_amount(cls, value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    class Config:
        orm_mode = True


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

    class Config:
        orm_mode = True


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

    class Config:
        orm_mode = True


class ValidationIssueRead(BaseModel):
    id: int
    severity: str
    issue: str
    model_id: Optional[int]

    class Config:
        orm_mode = True
