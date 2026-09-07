"""The `users` table — login and permissions.

Deliberately isolated: no other table has a foreign key to it. Orders and
routes are not "owned" by a user in this model, so deleting an account cannot
orphan operational data.
"""
from __future__ import annotations

from sqlalchemy import Enum as SAEnum
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import UserRole


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # unique: the email IS the login identifier, so the database enforces one
    # account per address rather than trusting application code to check.
    # index: every login does a lookup by email, and UNIQUE creates the index
    # that serves it.
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    # The bcrypt hash — never the password. The column name says "hash" so the
    # distinction is visible at every use site.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Safest default: a new account can look but not touch. Escalation is
    # deliberate rather than accidental.
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role"), default=UserRole.VIEWER, nullable=False
    )
    # Soft disable. Checked on every request by get_current_user, which is the
    # only way to revoke access before a 24-hour token expires — deleting the
    # row would not invalidate an already-issued token either.
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
