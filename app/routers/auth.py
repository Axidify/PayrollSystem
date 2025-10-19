"""Authentication routes and session management."""
from __future__ import annotations

import os
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_session
from app.auth import User
from app.dependencies import templates

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
    """Handle login form submission."""
    # Find user
    user = db.query(User).filter(User.username == username).first()
    
    if not user or not user.verify_password(password):
        # Return login page with error
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "error": "Invalid username or password"},
            status_code=401,
        )
    
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
