"""initial schema + PostGIS

Enables the PostGIS extension (required before any GEOGRAPHY column is created)
and then builds the full schema from the SQLAlchemy metadata. GeoAlchemy2
registers DDL hooks that additionally create GiST spatial indexes on the
geography columns during table creation.

Revision ID: 0001
Revises:
Create Date: 2026-07-20
"""
from alembic import op

from app.db.base import Base
import app.models  # noqa: F401  (registers all tables on Base.metadata)

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
