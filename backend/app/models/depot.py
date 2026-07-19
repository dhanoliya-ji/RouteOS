from __future__ import annotations

from datetime import time
from typing import TYPE_CHECKING

from geoalchemy2 import Geography
from sqlalchemy import Float, String, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.order import Order
    from app.models.route import Route
    from app.models.vehicle import Vehicle


class Depot(Base, TimestampMixin):
    __tablename__ = "depots"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    address: Mapped[str | None] = mapped_column(String(255))
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    # PostGIS geography point (WGS84). Kept in sync with lat/lng.
    location: Mapped[object] = mapped_column(
        Geography(geometry_type="POINT", srid=4326), nullable=True
    )
    operating_start: Mapped[time] = mapped_column(Time, default=time(8, 0))
    operating_end: Mapped[time] = mapped_column(Time, default=time(20, 0))

    vehicles: Mapped[list["Vehicle"]] = relationship(back_populates="home_depot")
    orders: Mapped[list["Order"]] = relationship(back_populates="depot")
    routes: Mapped[list["Route"]] = relationship(back_populates="depot")
