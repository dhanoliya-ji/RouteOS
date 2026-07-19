"""Declarative base and common column mixins."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# Import all models here so Alembic autogenerate & metadata are aware of them.
def import_models() -> None:  # pragma: no cover - side-effect importer
    from app import models  # noqa: F401
