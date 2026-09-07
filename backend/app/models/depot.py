"""The `depots` table — the hub every route starts and ends at.

A depot is the root of its own world: its vehicles, orders and routes all
cascade-delete with it (see the delete-rules table in models/README.md), which
is why deleting one is admin-only.
"""
from __future__ import annotations

from datetime import time
from typing import TYPE_CHECKING

from geoalchemy2 import Geography
from sqlalchemy import Float, String, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

# Imported for type checking only. At runtime these would be circular — Vehicle
# imports Depot and Depot imports Vehicle — so the annotations below are written
# as strings ("Vehicle") and resolved lazily by SQLAlchemy.
if TYPE_CHECKING:
    from app.models.order import Order
    from app.models.route import Route
    from app.models.vehicle import Vehicle


class Depot(Base, TimestampMixin):
    __tablename__ = "depots"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    address: Mapped[str | None] = mapped_column(String(255))

    # Position is stored TWICE, on purpose:
    #
    #   latitude/longitude  plain floats — what the API returns and the solver
    #                       reads. No PostGIS needed to use them.
    #   location            a PostGIS geography point — what radius searches
    #                       (ST_DWithin) use, backed by a GiST index.
    #
    # The duplication buys speed on both paths at the cost of having to keep
    # them in step, which depot_service does on every create and update.
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    # PostGIS geography point (WGS84). Kept in sync with lat/lng.
    #
    # Geography (not Geometry) so ST_Distance answers in metres rather than
    # degrees. nullable because the column is populated by the service layer
    # after the row is constructed, and older rows may predate it.
    location: Mapped[object] = mapped_column(
        Geography(geometry_type="POINT", srid=4326), nullable=True
    )

    # Opening hours. Held as plain times (no date, no zone) because they
    # describe a recurring daily window, not a specific instant.
    # Not currently enforced by the solver — its planning horizon is anchored to
    # the earliest order window instead.
    operating_start: Mapped[time] = mapped_column(Time, default=time(8, 0))
    operating_end: Mapped[time] = mapped_column(Time, default=time(20, 0))

    # The other side of the three CASCADE foreign keys pointing here. These are
    # convenience accessors for Python; they emit a query when first touched, so
    # in async code they must be eager-loaded (selectinload) before use.
    vehicles: Mapped[list["Vehicle"]] = relationship(back_populates="home_depot")
    orders: Mapped[list["Order"]] = relationship(back_populates="depot")
    routes: Mapped[list["Route"]] = relationship(back_populates="depot")
