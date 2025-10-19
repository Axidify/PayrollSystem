"""Authentication routes and session management."""
from __future__ import annotations

import os
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_session
from app.auth import User
from app.dependencies import templates
from app.security import (
    record_login_attempt,
    is_account_locked,
    increment_failed_login,
    reset_failed_login,
)

router = APIRouter(tags=["Auth"])


@router.get("/login")
def login_page(request: Request):
    """Render login page."""
    return templates.TemplateResponse("auth/login.html", {"request": request})


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_session),
):
    """Handle login form submission with rate limiting and account lockout."""
    # Get client IP address
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "")
    
    # Check if account is locked
    locked, lock_reason = is_account_locked(db, username)
    if locked:
        record_login_attempt(db, username, False, client_ip, user_agent)
        return templates.TemplateResponse(
            "auth/login.html",
            {
                "request": request,
                "error": f"Account locked due to too many failed login attempts. {lock_reason}",
            },
            status_code=403,
        )
    
    # Find user
    user = db.query(User).filter(User.username == username).first()
    
    if not user or not user.verify_password(password):
        # Record failed attempt
        increment_failed_login(db, username)
        record_login_attempt(db, username, False, client_ip, user_agent)
        
        # Get updated failed attempt count
        user = db.query(User).filter(User.username == username).first()
        failed_count = user.failed_login_count if user else 0
        attempts_remaining = max(0, 5 - failed_count)
        
        # Create error message with attempt counter
        error_msg = "Invalid username or password"
        if attempts_remaining > 0:
            error_msg += f" ({attempts_remaining} attempt{'s' if attempts_remaining != 1 else ''} remaining)"
        
        # Return login page with error and attempt count
        return templates.TemplateResponse(
            "auth/login.html",
            {
                "request": request,
                "error": error_msg,
                "attempts_remaining": attempts_remaining,
                "failed_count": failed_count,
            },
            status_code=401,
        )
    
    # Successful login - reset failed counter and record attempt
    reset_failed_login(db, username)
    record_login_attempt(db, username, True, client_ip, user_agent)
    
    # Set session cookie and redirect to dashboard
    response = RedirectResponse(url="/dashboard", status_code=303)
    # In production (Render), secure=True for HTTPS. In dev, secure=False for HTTP.
    is_production = os.getenv("PAYROLL_DATABASE_URL", "").startswith("postgresql")
    response.set_cookie(
        key="user_id",
        value=str(user.id),
        httponly=True,
        path="/",
        secure=is_production,  # True in production (HTTPS), False in dev (HTTP)
        samesite="lax",
        max_age=86400,  # 24 hours
    )
    return response



@router.get("/logout")
def logout():
    """Handle logout — clear session cookie."""
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("user_id")
    return response


def get_current_user(request: Request, db: Session = Depends(get_session)) -> User:
    """Dependency to get current authenticated user."""
    user_id = request.cookies.get("user_id")
    
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        user_id = int(user_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid session")
    
    user = db.query(User).filter(User.id == user_id).first()
    
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    
    return user


def get_admin_user(user: User = Depends(get_current_user)) -> User:
    """Dependency to ensure user is admin."""
    if not user.is_admin():
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
