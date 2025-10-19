"""Security utilities for rate limiting and account lockout."""
from __future__ import annotations

from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.auth import User
from app.models import LoginAttempt

# Configuration
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION_MINUTES = 15
RATE_LIMIT_WINDOW_MINUTES = 15


def record_login_attempt(
    db: Session,
    username: str,
    success: bool,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Record a login attempt in the database."""
    attempt = LoginAttempt(
        username=username,
        success=success,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(attempt)
    db.commit()


def get_failed_attempts_count(
    db: Session,
    username: str,
    minutes: int = RATE_LIMIT_WINDOW_MINUTES,
) -> int:
    """Get count of failed login attempts in the last N minutes."""
    cutoff_time = datetime.now() - timedelta(minutes=minutes)
    
    stmt = select(LoginAttempt).where(
        LoginAttempt.username == username,
        LoginAttempt.success == False,
        LoginAttempt.attempted_at >= cutoff_time,
    )
    
    attempts = db.execute(stmt).scalars().all()
    return len(attempts)


def is_account_locked(db: Session, username: str) -> tuple[bool, str | None]:
    """
    Check if account is locked.
    Returns (is_locked, reason_message)
    """
    user = db.query(User).filter(User.username == username).first()
    
    if not user:
        return False, None
    
    # Check if account is permanently locked by admin
    if user.is_locked:
        if user.locked_until and user.locked_until > datetime.now():
            return True, f"Account is locked until {user.locked_until.strftime('%Y-%m-%d %H:%M:%S')}"
        else:
            # Auto-unlock if lockout period has passed
            user.is_locked = False
            user.locked_until = None
            user.failed_login_count = 0
            db.add(user)
            db.commit()
            return False, None
    
    return False, None


def lock_account(
    db: Session,
    username: str,
    duration_minutes: int = LOCKOUT_DURATION_MINUTES,
) -> None:
    """Lock a user account after too many failed attempts."""
    user = db.query(User).filter(User.username == username).first()
    
    if user:
        user.is_locked = True
        user.locked_until = datetime.now() + timedelta(minutes=duration_minutes)
        user.failed_login_count = 0  # Reset counter
        db.add(user)
        db.commit()


def increment_failed_login(db: Session, username: str) -> None:
    """Increment failed login counter for a user."""
    user = db.query(User).filter(User.username == username).first()
    
    if user:
        user.failed_login_count += 1
        user.last_failed_login = datetime.now()
        db.add(user)
        db.commit()
        
        # Lock account if max attempts reached
        if user.failed_login_count >= MAX_FAILED_ATTEMPTS:
            lock_account(db, username)


def reset_failed_login(db: Session, username: str) -> None:
    """Reset failed login counter after successful login."""
    user = db.query(User).filter(User.username == username).first()
    
    if user:
        user.failed_login_count = 0
        user.last_failed_login = None
        db.add(user)
        db.commit()


def unlock_account(db: Session, username: str) -> None:
    """Manually unlock an account (admin only)."""
    user = db.query(User).filter(User.username == username).first()
    
    if user:
        user.is_locked = False
        user.locked_until = None
        user.failed_login_count = 0
        user.last_failed_login = None
        db.add(user)
        db.commit()


def get_recent_login_attempts(
    db: Session,
    username: str,
    limit: int = 10,
) -> list[LoginAttempt]:
    """Get recent login attempts for a user."""
    stmt = (
        select(LoginAttempt)
        .where(LoginAttempt.username == username)
        .order_by(LoginAttempt.attempted_at.desc())
        .limit(limit)
    )
    
    return db.execute(stmt).scalars().all()
