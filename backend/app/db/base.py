"""Declarative base and common column mixins.

The foundation every model in app/models/ is built on. No tables are defined
here — only the machinery they share.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """The declarative base every model subclasses.

    Subclassing this is what *registers* a table on `Base.metadata` — the
    single registry Alembic reads to work out what the schema should be. That
    registration is a side effect of the class being defined, which is why
    model modules must be imported for Alembic to see them (see
    `import_models` below).
    """


class TimestampMixin:
    """Adds a `created_at` column, stamped by the database.

    Mixed in by multiple inheritance, so a model is both a table and
    timestamped:

        class Order(Base, TimestampMixin): ...

    Note `server_default=func.now()`, not a Python `default=datetime.now`. The
    difference matters:

      * server_default compiles into the table definition, so *Postgres*
        supplies the value. One clock, whoever writes the row.
      * a Python default would use the app server's clock — which can drift, and
        differs between replicas — and would supply nothing at all for rows
        inserted by a migration, a script, or by hand in psql.

    timezone=True stores it as `timestamptz`, so the value is an unambiguous
    instant rather than a wall-clock reading with no zone.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# Import all models here so Alembic autogenerate & metadata are aware of them.
def import_models() -> None:  # pragma: no cover - side-effect importer
    """Force every model module to load, populating `Base.metadata`.

    This looks pointless — it imports something and does nothing with it — but
    it is load-bearing. `Base.metadata` is filled by the side effect of
    defining model classes, and Alembic's autogenerate diffs *metadata* against
    the live database. If the model modules were never imported, metadata would
    be empty and autogenerate would conclude that every existing table should
    be dropped.

    The import sits inside the function rather than at module top level to
    avoid a circular import: models import `Base` from this module.
    """
    from app import models  # noqa: F401
