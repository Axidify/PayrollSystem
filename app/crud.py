"""Database access helpers."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Iterable, Sequence

import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.payroll import ModelRecord, ValidationMessage
from app.models import AdhocPayment, Model, ModelCompensationAdjustment, Payout, ScheduleRun, ValidationIssue
from app.schemas import AdhocPaymentCreate, AdhocPaymentUpdate, ModelCreate, ModelUpdate


def list_models(
    db: Session,
    code: str | None = None,
    status: str | None = None,
    frequency: str | None = None,
    payment_method: str | None = None,
) -> Sequence[Model]:
    stmt = select(Model)

    if code:
        like_value = f"%{code.strip()}%"
        stmt = stmt.where(Model.code.ilike(like_value))

    if status:
        stmt = stmt.where(Model.status == status)

    if frequency:
        stmt = stmt.where(Model.payment_frequency == frequency)

    if payment_method:
        stmt = stmt.where(Model.payment_method == payment_method)

    stmt = stmt.order_by(Model.code)
    return db.execute(stmt).scalars().all()


def get_model(db: Session, model_id: int) -> Model | None:
    return db.get(Model, model_id)


def get_model_by_code(db: Session, code: str) -> Model | None:
    stmt = select(Model).where(Model.code == code)
    return db.execute(stmt).scalars().first()


def create_model(db: Session, payload: ModelCreate) -> Model:
    model = Model(**payload.model_dump())
    db.add(model)
    db.flush()

    effective_date = model.start_date or date.today()
    existing_adjustment = (
        db.query(ModelCompensationAdjustment)
        .filter(
            ModelCompensationAdjustment.model_id == model.id,
            ModelCompensationAdjustment.effective_date == effective_date,
        )
        .first()
    )
    if not existing_adjustment:
        create_compensation_adjustment(
            db,
            model,
            effective_date=effective_date,
            amount_monthly=Decimal(model.amount_monthly),
            notes="Initial compensation",
        )
    db.commit()
    db.refresh(model)
    return model


def update_model(db: Session, model: Model, payload: ModelUpdate) -> Model:
    for key, value in payload.model_dump().items():
        setattr(model, key, value)
    model.updated_at = datetime.now()
    db.add(model)
    db.commit()
    db.refresh(model)
    return model


def delete_model(db: Session, model: Model) -> None:
    db.delete(model)
    db.commit()


def get_effective_compensation_amount(db: Session, model: Model, target_date: date) -> Decimal:
    adjustment = (
        db.query(ModelCompensationAdjustment)
        .filter(
            ModelCompensationAdjustment.model_id == model.id,
            ModelCompensationAdjustment.effective_date <= target_date,
        )
        .order_by(ModelCompensationAdjustment.effective_date.desc())
        .first()
    )
    if adjustment:
        return Decimal(adjustment.amount_monthly)
    return Decimal(model.amount_monthly)


def create_compensation_adjustment(
    db: Session,
    model: Model,
    effective_date: date,
    amount_monthly: Decimal,
    notes: str | None = None,
) -> ModelCompensationAdjustment:
    adjustment = (
        db.query(ModelCompensationAdjustment)
        .filter(
            ModelCompensationAdjustment.model_id == model.id,
            ModelCompensationAdjustment.effective_date == effective_date,
        )
        .first()
    )
    if adjustment:
        adjustment.amount_monthly = amount_monthly
        adjustment.notes = notes
    else:
        adjustment = ModelCompensationAdjustment(
            model_id=model.id,
            effective_date=effective_date,
            amount_monthly=amount_monthly,
            notes=notes,
        )
        db.add(adjustment)

    if effective_date <= date.today():
        model.amount_monthly = amount_monthly
        model.updated_at = datetime.now()
        db.add(model)

    db.flush()
    return adjustment


def clear_schedule_data(db: Session, schedule_run: ScheduleRun) -> None:
    db.query(Payout).filter(Payout.schedule_run_id == schedule_run.id).delete()
    db.query(ValidationIssue).filter(ValidationIssue.schedule_run_id == schedule_run.id).delete()
    db.commit()


def create_schedule_run(
    db: Session,
    target_year: int,
    target_month: int,
    currency: str,
    include_inactive: bool,
    summary: dict,
    export_path: str,
) -> ScheduleRun:
    existing = (
        db.query(ScheduleRun)
        .filter(
            ScheduleRun.target_year == target_year,
            ScheduleRun.target_month == target_month,
        )
        .order_by(ScheduleRun.created_at.desc())
        .first()
    )
    if existing:
        raise ValueError(
            f"A schedule run already exists for {target_year:04d}-{target_month:02d} (id {existing.id})."
        )
    run = ScheduleRun(
        target_year=target_year,
        target_month=target_month,
        currency=currency,
        include_inactive=include_inactive,
        summary_models_paid=summary.get("models_paid", 0),
        summary_total_payout=summary.get("total_payout", 0),
        summary_frequency_counts=json.dumps(summary.get("frequency_counts", {})),
        export_path=export_path,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def store_payouts(db: Session, run: ScheduleRun, payouts: Iterable[dict], amount_column: str, old_payout_data: dict | None = None) -> None:
    """Store payouts, preserving status and notes from previous payouts when available."""
    if old_payout_data is None:
        old_payout_data = {}
    
    objects = []
    for payout in payouts:
        pay_date = payout["Pay Date"]
        code = payout["Code"]
        key = (code, pay_date)
        
        # Check if this payout existed before - if so, preserve its status and notes
        status = old_payout_data.get(key, {}).get("status", "not_paid")
        notes = old_payout_data.get(key, {}).get("notes", payout.get("Notes"))
        
        payout_obj = Payout(
            schedule_run_id=run.id,
            model_id=_lookup_model_id(db, code),
            pay_date=pay_date,
            code=code,
            real_name=payout["Real Name"],
            working_name=payout["Working Name"],
            payment_method=payout["Payment Method"],
            payment_frequency=payout["Payment Frequency"],
            amount=payout.get(amount_column),
            notes=notes,
            status=status,
        )
        objects.append(payout_obj)
    
    db.add_all(objects)
    db.commit()


def store_validation_messages(
    db: Session,
    run: ScheduleRun,
    records: Iterable[ModelRecord],
    include_inactive: bool,
) -> None:
    issues: list[ValidationIssue] = []
    for record in records:
        is_active = record.status.lower() == "active"
        if not is_active and not include_inactive:
            continue
        for message in record.validation_messages:
            issues.append(
                ValidationIssue(
                    schedule_run_id=run.id,
                    model_id=_lookup_model_id(db, record.code),
                    severity=message.level,
                    issue=message.text,
                )
            )
    if issues:
        db.add_all(issues)
        db.commit()


def _lookup_model_id(db: Session, code: str) -> int | None:
    stmt = select(Model.id).where(Model.code == code)
    return db.execute(stmt).scalar_one_or_none()


def list_schedule_runs(
    db: Session, target_year: int | None = None, target_month: int | None = None
) -> Sequence[ScheduleRun]:
    stmt = select(ScheduleRun)

    if target_year is not None:
        stmt = stmt.where(ScheduleRun.target_year == target_year)

    if target_month is not None:
        stmt = stmt.where(ScheduleRun.target_month == target_month)

    stmt = stmt.order_by(ScheduleRun.created_at.desc())
    return db.execute(stmt).scalars().all()


def get_schedule_run(db: Session, run_id: int) -> ScheduleRun | None:
    return db.get(ScheduleRun, run_id)


def list_payouts_for_run(
    db: Session,
    run_id: int,
    code: str | None = None,
    frequency: str | None = None,
    payment_method: str | None = None,
    status: str | None = None,
    pay_date: date | None = None,
) -> Sequence[Payout]:
    stmt = select(Payout).where(Payout.schedule_run_id == run_id)

    if code:
        stmt = stmt.where(Payout.code.ilike(f"%{code.strip()}%"))

    if frequency:
        stmt = stmt.where(Payout.payment_frequency == frequency)

    if payment_method:
        stmt = stmt.where(Payout.payment_method == payment_method)

    if status:
        stmt = stmt.where(Payout.status == status)

    if pay_date:
        stmt = stmt.where(Payout.pay_date == pay_date)

    stmt = stmt.order_by(Payout.pay_date, Payout.code)
    return db.execute(stmt).scalars().all()


def list_validation_for_run(db: Session, run_id: int) -> Sequence[ValidationIssue]:
    stmt = select(ValidationIssue).where(ValidationIssue.schedule_run_id == run_id).order_by(
        ValidationIssue.severity, ValidationIssue.id
    )
    return db.execute(stmt).scalars().all()


def get_payout(db: Session, payout_id: int) -> Payout | None:
    return db.get(Payout, payout_id)


def update_payout(db: Session, payout: Payout, note: str | None, status: str) -> None:
    payout.notes = note or None
    payout.status = status
    db.add(payout)
    db.commit()


def delete_schedule_run(db: Session, run: ScheduleRun) -> None:
    db.delete(run)
    db.commit()


def total_paid_by_model(db: Session, model_ids: Sequence[int]) -> dict[int, Decimal]:
    if not model_ids:
        return {}

    stmt = (
        select(Payout.model_id, func.coalesce(func.sum(Payout.amount), 0))
        .where(Payout.model_id.in_(model_ids), Payout.status == "paid")
        .group_by(Payout.model_id)
    )
    results = db.execute(stmt).all()
    totals: dict[int, Decimal] = {}
    for model_id, total in results:
        if isinstance(total, Decimal):
            totals[model_id] = total
        else:
            totals[model_id] = Decimal(total)
    return totals


def list_payment_methods(db: Session) -> list[str]:
    stmt = select(Model.payment_method).distinct().order_by(Model.payment_method)
    return [row[0] for row in db.execute(stmt).all() if row[0]]


def payment_methods_for_run(db: Session, run_id: int) -> list[str]:
    stmt = (
        select(Payout.payment_method)
        .where(Payout.schedule_run_id == run_id)
        .distinct()
        .order_by(Payout.payment_method)
    )
    return [row[0] for row in db.execute(stmt).all() if row[0]]


def frequencies_for_run(db: Session, run_id: int) -> list[str]:
    stmt = (
        select(Payout.payment_frequency)
        .where(Payout.schedule_run_id == run_id)
        .distinct()
        .order_by(Payout.payment_frequency)
    )
    return [row[0] for row in db.execute(stmt).all() if row[0]]


def run_payment_summary(db: Session, run_id: int) -> dict[str, Decimal | int]:
    paid_sum_stmt = (
        select(func.coalesce(func.sum(Payout.amount), 0))
        .where(Payout.schedule_run_id == run_id, Payout.status == "paid")
    )
    unpaid_sum_stmt = (
        select(func.coalesce(func.sum(Payout.amount), 0))
        .where(Payout.schedule_run_id == run_id, Payout.status != "paid")
    )
    # Count unique models that have at least one payout with status "paid"
    paid_models_stmt = (
        select(func.count(func.distinct(Payout.code)))
        .where(Payout.schedule_run_id == run_id, Payout.status == "paid")
    )

    paid_total = Decimal(db.execute(paid_sum_stmt).scalar_one() or 0)
    unpaid_total = Decimal(db.execute(unpaid_sum_stmt).scalar_one() or 0)
    paid_models = db.execute(paid_models_stmt).scalar_one() or 0

    overall_paid_stmt = select(func.coalesce(func.sum(Payout.amount), 0)).where(Payout.status == "paid")
    overall_paid_total = Decimal(db.execute(overall_paid_stmt).scalar_one() or 0)

    return {
        "paid_total": paid_total,
        "unpaid_total": unpaid_total,
        "paid_models": int(paid_models),
        "overall_paid": overall_paid_total,
    }


def payout_status_counts(db: Session, run_id: int) -> dict[str, int]:
    stmt = (
        select(Payout.status, func.count())
        .where(Payout.schedule_run_id == run_id)
        .group_by(Payout.status)
    )
    return {status: count for status, count in db.execute(stmt).all()}


def payout_codes_for_run(db: Session, run_id: int) -> list[str]:
    stmt = (
        select(Payout.code)
        .where(Payout.schedule_run_id == run_id)
        .distinct()
        .order_by(Payout.code)
    )
    return [row[0] for row in db.execute(stmt).all() if row[0]]


def payout_dates_for_run(db: Session, run_id: int) -> list[date]:
    stmt = (
        select(Payout.pay_date)
        .where(Payout.schedule_run_id == run_id)
        .distinct()
        .order_by(Payout.pay_date)
    )
    return [row[0] for row in db.execute(stmt).all() if row[0]]


def dashboard_summary(db: Session) -> dict[str, Decimal | int | date | None]:
    total_models = db.execute(select(func.count(Model.id))).scalar_one() or 0
    active_models = db.execute(select(func.count(Model.id)).where(Model.status == "Active")).scalar_one() or 0
    inactive_models = db.execute(select(func.count(Model.id)).where(Model.status == "Inactive")).scalar_one() or 0

    total_runs = db.execute(select(func.count(ScheduleRun.id))).scalar_one() or 0
    latest_run = (
        db.execute(select(ScheduleRun).order_by(ScheduleRun.created_at.desc())).scalars().first()
    )

    today = date.today()
    current_month_run = (
        db.execute(
            select(ScheduleRun)
            .where(
                ScheduleRun.target_year == today.year,
                ScheduleRun.target_month == today.month,
            )
            .order_by(ScheduleRun.created_at.desc())
        )
        .scalars()
        .first()
    )

    run_for_metrics = current_month_run or latest_run

    lifetime_paid_stmt = select(func.coalesce(func.sum(Payout.amount), 0)).where(Payout.status == "paid")
    lifetime_paid = Decimal(db.execute(lifetime_paid_stmt).scalar_one() or 0)

    outstanding_stmt = select(func.coalesce(func.sum(Payout.amount), 0)).where(Payout.status != "paid")
    outstanding_total = Decimal(db.execute(outstanding_stmt).scalar_one() or 0)

    pending_count_stmt = select(func.count()).where(Payout.status == "not_paid")
    pending_count = db.execute(pending_count_stmt).scalar_one() or 0

    on_hold_count_stmt = select(func.count()).where(Payout.status == "on_hold")
    on_hold_count = db.execute(on_hold_count_stmt).scalar_one() or 0

    latest_run_paid = Decimal("0")
    latest_run_unpaid = Decimal("0")
    if run_for_metrics:
        latest_paid_stmt = (
            select(func.coalesce(func.sum(Payout.amount), 0))
            .where(Payout.schedule_run_id == run_for_metrics.id, Payout.status == "paid")
        )
        latest_run_paid = Decimal(db.execute(latest_paid_stmt).scalar_one() or 0)

        latest_unpaid_stmt = (
            select(func.coalesce(func.sum(Payout.amount), 0))
            .where(Payout.schedule_run_id == run_for_metrics.id, Payout.status != "paid")
        )
        latest_run_unpaid = Decimal(db.execute(latest_unpaid_stmt).scalar_one() or 0)

    return {
        "total_models": int(total_models),
        "active_models": int(active_models),
        "inactive_models": int(inactive_models),
        "total_runs": int(total_runs),
    "latest_run": run_for_metrics,
    "latest_run_is_current_month": bool(current_month_run),
        "lifetime_paid": lifetime_paid,
        "outstanding_total": outstanding_total,
        "pending_count": int(pending_count),
        "on_hold_count": int(on_hold_count),
        "latest_run_paid": latest_run_paid,
        "latest_run_unpaid": latest_run_unpaid,
    }


def recent_schedule_runs(db: Session, limit: int = 5) -> Sequence[ScheduleRun]:
    stmt = select(ScheduleRun).order_by(ScheduleRun.created_at.desc()).limit(limit)
    return db.execute(stmt).scalars().all()


def top_paid_models(db: Session, limit: int = 5) -> list[tuple[Model, Decimal]]:
    stmt = (
        select(Model, func.coalesce(func.sum(Payout.amount), 0).label("total_paid"))
        .join(Payout, Payout.model_id == Model.id)
        .where(Payout.status == "paid")
        .group_by(Model.id)
        .order_by(func.coalesce(func.sum(Payout.amount), 0).desc())
        .limit(limit)
    )
    results = db.execute(stmt).all()
    output: list[tuple[Model, Decimal]] = []
    for model, total in results:
        if isinstance(total, Decimal):
            output.append((model, total))
        else:
            output.append((model, Decimal(total)))
    return output


def recent_validation_issues(db: Session, limit: int = 5) -> Sequence[ValidationIssue]:
    stmt = select(ValidationIssue).order_by(ValidationIssue.id.desc()).limit(limit)
    return db.execute(stmt).scalars().all()


def pending_adhoc_payments(db: Session, limit: int = 6) -> Sequence[AdhocPayment]:
    stmt = (
        select(AdhocPayment)
        .options(selectinload(AdhocPayment.model))
        .where(AdhocPayment.status == "pending")
        .order_by(AdhocPayment.pay_date.asc(), AdhocPayment.id.asc())
    )
    if limit:
        stmt = stmt.limit(limit)
    return db.execute(stmt).scalars().all()


def get_paid_payouts_for_model(db: Session, model_id: int) -> Sequence[Payout]:
    """
    Get all paid payouts for a model, sorted by pay date descending.
    This is the unified source of truth for payment history.
    """
    stmt = (
        select(Payout)
        .where(Payout.model_id == model_id)
        .where(Payout.status == "paid")
        .order_by(Payout.pay_date.desc())
    )
    return db.execute(stmt).scalars().all()


def find_duplicate_payouts(
    db: Session, 
    model_id: int, 
    pay_date: date, 
    amount: Decimal, 
    status: str
) -> Sequence[Payout]:
    """
    Find existing payouts matching the given criteria (date, amount, status).
    Used for duplicate detection.
    """
    stmt = (
        select(Payout)
        .where(Payout.model_id == model_id)
        .where(Payout.pay_date == pay_date)
        .where(Payout.amount == amount)
        .where(Payout.status == status)
    )
    return db.execute(stmt).scalars().all()


def list_adhoc_payments(
    db: Session,
    model_id: int,
    status: str | None = None,
) -> Sequence[AdhocPayment]:
    stmt = select(AdhocPayment).where(AdhocPayment.model_id == model_id)
    if status:
        stmt = stmt.where(AdhocPayment.status == status)
    stmt = stmt.order_by(AdhocPayment.pay_date.desc(), AdhocPayment.id.desc())
    return db.execute(stmt).scalars().all()


def list_adhoc_payments_for_month(
    db: Session,
    year: int,
    month: int,
    status: str | None = None,
) -> Sequence[AdhocPayment]:
    if month < 1 or month > 12:
        raise ValueError("month must be in 1..12")

    month_start = date(year, month, 1)
    if month == 12:
        next_month_start = date(year + 1, 1, 1)
    else:
        next_month_start = date(year, month + 1, 1)

    stmt = (
        select(AdhocPayment)
        .options(selectinload(AdhocPayment.model))
        .where(AdhocPayment.pay_date >= month_start)
        .where(AdhocPayment.pay_date < next_month_start)
    )
    if status:
        stmt = stmt.where(AdhocPayment.status == status)
    stmt = stmt.order_by(AdhocPayment.pay_date.asc(), AdhocPayment.id.asc())
    return db.execute(stmt).scalars().all()


def get_adhoc_payment(db: Session, payment_id: int) -> AdhocPayment | None:
    return db.get(AdhocPayment, payment_id)


def create_adhoc_payment(db: Session, model: Model, payload: AdhocPaymentCreate) -> AdhocPayment:
    payment = AdhocPayment(
        model_id=model.id,
        pay_date=payload.pay_date,
        amount=payload.amount,
        description=(payload.description.strip() if payload.description else None),
        notes=(payload.notes.strip() if payload.notes else None),
        status=payload.status.lower(),
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


def update_adhoc_payment(db: Session, payment: AdhocPayment, payload: AdhocPaymentUpdate) -> AdhocPayment:
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        if field in {"description", "notes"}:
            normalized = value.strip() if isinstance(value, str) else None
            setattr(payment, field, normalized or None)
        elif field == "status" and value is not None:
            setattr(payment, field, value.lower())
        else:
            setattr(payment, field, value)
    payment.updated_at = datetime.now()
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


def delete_adhoc_payment(db: Session, payment: AdhocPayment) -> None:
    db.delete(payment)
    db.commit()


def set_adhoc_payment_status(db: Session, payment: AdhocPayment, status: str, notes: str | None = None) -> AdhocPayment:
    payment.status = status.lower()
    if notes is not None:
        payment.notes = notes.strip() or None
    payment.updated_at = datetime.now()
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment
