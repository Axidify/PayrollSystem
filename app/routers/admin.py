"""Admin routes for user management."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.auth import User
from app.database import get_session
from app.dependencies import templates
from app.routers.auth import get_current_user, get_admin_user
from app.security import unlock_account

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/users")
def list_users(
    request: Request,
    db: Session = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """List all users (admin only)."""
    users = db.query(User).all()
    return templates.TemplateResponse(
        "admin/users.html",
        {
            "request": request,
            "users": users,
            "user": admin,
        },
    )


@router.get("/users/new")
def new_user_form(
    request: Request,
    admin: User = Depends(get_admin_user),
):
    """Show new user creation form (admin only)."""
    return templates.TemplateResponse(
        "admin/user_form.html",
        {
            "request": request,
            "action": "create",
            "user": admin,
        },
    )


@router.post("/users/new")
def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("user"),
    db: Session = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Create a new user (admin only)."""
    # Check if username already exists
    existing = db.query(User).filter(User.username == username).first()
    if existing:
        return templates.TemplateResponse(
            "admin/user_form.html",
            {
                "request": request,
                "action": "create",
                "error": "Username already exists",
                "user": admin,
            },
            status_code=400,
        )
    
    # Validate role
    if role not in ["admin", "user"]:
        return templates.TemplateResponse(
            "admin/user_form.html",
            {
                "request": request,
                "action": "create",
                "error": "Invalid role",
                "user": admin,
            },
            status_code=400,
        )
    
    new_user = User.create_user(username, password, role=role)
    db.add(new_user)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        # Handle unique constraint violation
        if "username" in str(e):
            return templates.TemplateResponse(
                "admin/user_form.html",
                {
                    "request": request,
                    "action": "create",
                    "error": "Username already exists",
                    "user": admin,
                },
                status_code=400,
            )
        raise
    return RedirectResponse(url="/admin/users", status_code=303)


@router.get("/users/{user_id}/edit")
def edit_user_form(
    user_id: int,
    request: Request,
    db: Session = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Show user edit form (admin only)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return templates.TemplateResponse(
        "admin/user_form.html",
        {
            "request": request,
            "action": "edit",
            "form_user": user,
            "user": admin,
        },
    )


@router.post("/users/{user_id}/edit")
def update_user(
    user_id: int,
    request: Request,
    role: str = Form(...),
    db: Session = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Update user role (admin only)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Prevent admin from modifying their own role
    if user.id == admin.id:
        return templates.TemplateResponse(
            "admin/user_form.html",
            {
                "request": request,
                "action": "edit",
                "form_user": user,
                "error": "Cannot modify your own role",
                "user": admin,
            },
            status_code=400,
        )
    
    if role not in ["admin", "user"]:
        return templates.TemplateResponse(
            "admin/user_form.html",
            {
                "request": request,
                "action": "edit",
                "form_user": user,
                "error": "Invalid role",
                "user": admin,
            },
            status_code=400,
        )
    
    user.role = role
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/reset-password")
def reset_user_password(
    user_id: int,
    request: Request,
    new_password: str = Form(...),
    db: Session = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Reset user password (admin only)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    user.password_hash = User.hash_password(new_password)
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/unlock")
def unlock_user_account(
    user_id: int,
    db: Session = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Unlock a locked user account (admin only)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Check if account is actually locked
    if not user.is_locked:
        raise HTTPException(status_code=400, detail="Account is not locked")
    
    # Unlock the account
    unlock_account(db, user.username)
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/delete")
def delete_user(
    user_id: int,
    db: Session = Depends(get_session),
    admin: User = Depends(get_admin_user),
):
    """Delete user (admin only)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Prevent admin from deleting their own account
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    
    db.delete(user)
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)
