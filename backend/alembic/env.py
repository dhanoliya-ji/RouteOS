"""Alembic environment — runs before any migration.

Its job is to answer two questions for Alembic:

    which database?   -> from Settings, not from alembic.ini
    what SHOULD the schema look like?  -> Base.metadata

The second is what autogenerate diffs against the live database to work out
which migration to write.
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.db.base import Base

# Register all models on Base.metadata
#
# This import looks unused and is the most important line in the file. A table
# lands on Base.metadata only as a side effect of its class being defined, which
# happens when its module is imported. Without this, target_metadata below would
# be EMPTY, and autogenerate would compare "no tables" against the real database
# and confidently emit a migration dropping every one of them.
import app.models  # noqa: F401,E402

config = context.config

# The database URL comes from Settings at runtime, overriding the deliberately
# blank sqlalchemy.url in alembic.ini. Two reasons:
#   * one source of truth — change DATABASE_URL and both the app and the
#     migrations follow
#   * no password is committed to a config file
#
# Note the SYNC url: Alembic is synchronous and needs the psycopg driver, while
# the app uses asyncpg. core/config.py derives both from whichever single URL it
# was given.
config.set_main_option("sqlalchemy.url", settings.database_url_sync)

# Apply the logging config from alembic.ini, if the file is present. Guarded
# because Alembic can be driven programmatically with no ini file at all.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The target state. Everything autogenerate knows about the schema comes from
# here — hence the import above.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it (`alembic upgrade --sql`).

    Used to hand a DBA a script for review, or to apply migrations in an
    environment where the tooling cannot reach the database directly. No
    connection is opened at all.
    """
    context.configure(
        url=settings.database_url_sync,
        target_metadata=target_metadata,
        # Inline the parameter values into the emitted SQL — a generated script
        # has no runtime to bind them at.
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect and run the migrations. The normal path.

    NullPool because a migration run is a short-lived process that needs one
    connection and then exits — pooling would only leave connections to clean up.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        # One transaction around the migrations, so a failure part-way rolls
        # back rather than leaving the schema half-changed. (Postgres supports
        # transactional DDL, which is what makes this work.)
        with context.begin_transaction():
            context.run_migrations()


# Alembic imports this module for its side effects and expects it to do the
# work at import time, which is why this runs at the bottom rather than under a
# __main__ guard.
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
