from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import VehicleStatus

if TYPE_CHECKING:
    from app.models.depot import Depot
    from app.models.route import Route


class Vehicle(Base, TimestampMixin):
    __tablename__ = "vehicles"
    __table_args__ = (
        Index("ix_vehicles_status", "status"),
        Index("ix_vehicles_home_depot_id", "home_depot_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    registration_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    driver_name: Mapped[str] = mapped_column(String(120), nullable=False)
    vehicle_type: Mapped[str] = mapped_column(String(40), default="VAN")
    capacity_kg: Mapped[float] = mapped_column(Float, nullable=False)
    capacity_volume: Mapped[float | None] = mapped_column(Float)
    current_load_kg: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[VehicleStatus] = mapped_column(
        SAEnum(VehicleStatus, name="vehicle_status"), default=VehicleStatus.AVAILABLE
    )
    current_latitude: Mapped[float | None] = mapped_column(Float)
    current_longitude: Mapped[float | None] = mapped_column(Float)
    home_depot_id: Mapped[int] = mapped_column(ForeignKey("depots.id", ondelete="CASCADE"))
    max_route_distance_km: Mapped[float | None] = mapped_column(Float, default=200.0)

    home_depot: Mapped["Depot"] = relationship(back_populates="vehicles")
    routes: Mapped[list["Route"]] = relationship(back_populates="vehicle")
