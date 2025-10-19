"""Authentication and user management."""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
import bcrypt


class User(Base):
    """User account for application access."""
    
    __tablename__ = "users"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), default="user", nullable=False)  # "admin" or "user"
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, nullable=False)
    
    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password using bcrypt."""
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')
    
    def verify_password(self, password: str) -> bool:
        """Verify a password against the stored hash."""
        return bcrypt.checkpw(password.encode('utf-8'), self.password_hash.encode('utf-8'))
    
    @classmethod
    def create_user(cls, username: str, password: str, role: str = "user") -> User:
        """Create a new user with hashed password."""
        return cls(username=username, password_hash=cls.hash_password(password), role=role)
    
    def is_admin(self) -> bool:
        """Check if user is an admin."""
        return self.role == "admin"

