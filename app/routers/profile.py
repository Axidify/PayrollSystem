"""User profile routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import User
from app.database import get_session
from app.dependencies import templates
from app.routers.auth import get_current_user

router = APIRouter(prefix="/profile", tags=["Profile"])


@router.get("/")
def view_profile(
    request: Request,
    user: User = Depends(get_current_user),
):
    """View current user profile."""
    return templates.TemplateResponse(
        "profile/profile.html",
        {
            "request": request,
            "user": user,
        },
    )


@router.post("/change-password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Change current user's password."""
    # Verify current password
    if not user.verify_password(current_password):
        return templates.TemplateResponse(
            "profile/profile.html",
            {
                "request": request,
                "user": user,
                "error": "Current password is incorrect",
            },
            status_code=401,
        )
    
    # Verify new password matches confirmation
    if new_password != confirm_password:
        return templates.TemplateResponse(
            "profile/profile.html",
            {
                "request": request,
                "user": user,
                "error": "New passwords do not match",
            },
            status_code=400,
        )
    
    # Validate password length
    if len(new_password) < 6:
        return templates.TemplateResponse(
            "profile/profile.html",
            {
                "request": request,
                "user": user,
                "error": "Password must be at least 6 characters",
            },
            status_code=400,
        )
    
    # Update password
    user.password_hash = User.hash_password(new_password)
    db.commit()
    
    return templates.TemplateResponse(
        "profile/profile.html",
        {
            "request": request,
            "user": user,
            "success": "Password changed successfully",
        },
    )
