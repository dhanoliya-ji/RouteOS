from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import RouteStatus, RouteStopStatus

if TYPE_CHECKING:
    from app.models.depot import Depot
    from app.models.order import Order
    from app.models.vehicle import Vehicle


class Route(Base, TimestampMixin):
    __tablename__ = "routes"
    __table_args__ = (
        Index("ix_routes_status", "status"),
        Index("ix_routes_vehicle_id", "vehicle_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    route_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id", ondelete="SET NULL"))
    depot_id: Mapped[int] = mapped_column(ForeignKey("depots.id", ondelete="CASCADE"))
    status: Mapped[RouteStatus] = mapped_column(
        SAEnum(RouteStatus, name="route_status"), default=RouteStatus.PLANNED
    )
    total_distance_km: Mapped[float] = mapped_column(Float, default=0.0)
    estimated_duration_minutes: Mapped[float] = mapped_column(Float, default=0.0)
    actual_duration_minutes: Mapped[float | None] = mapped_column(Float)
    optimization_score: Mapped[float | None] = mapped_column(Float)
    total_load_kg: Mapped[float] = mapped_column(Float, default=0.0)
    optimization_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("optimization_runs.id", ondelete="SET NULL")
    )
    # Simulation progress cursor (index of last completed stop; -1 = at depot start)
    progress_stop_index: Mapped[int] = mapped_column(Integer, default=-1)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    vehicle: Mapped["Vehicle"] = relationship(back_populates="routes")
    depot: Mapped["Depot"] = relationship(back_populates="routes")
    stops: Mapped[list["RouteStop"]] = relationship(
        back_populates="route",
        cascade="all, delete-orphan",
        order_by="RouteStop.stop_sequence",
    )


class RouteStop(Base, TimestampMixin):
    __tablename__ = "route_stops"
    __table_args__ = (Index("ix_route_stops_route_id", "route_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("routes.id", ondelete="CASCADE"))
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    stop_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    estimated_arrival: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_arrival: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    distance_from_previous_km: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[RouteStopStatus] = mapped_column(
        SAEnum(RouteStopStatus, name="route_stop_status"), default=RouteStopStatus.PENDING
    )

    route: Mapped["Route"] = relationship(back_populates="stops")
    order: Mapped["Order"] = relationship(back_populates="route_stops")
