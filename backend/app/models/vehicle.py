"""The `vehicles` table — the fleet.

Note what this table does NOT have: a PostGIS `location` column. Depots and
orders get one, vehicles deliberately don't — a vehicle's position is rewritten
every second by the simulation, and maintaining a geography column plus its
GiST index on every tick would cost more than the index returns. So
`vehicles_nearby` casts the two floats into a point inside the query and accepts
a full scan; see app/geospatial/README.md.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import VehicleStatus

if TYPE_CHECKING:  # avoid a runtime circular import; see depot.py
    from app.models.depot import Depot
    from app.models.route import Route


class Vehicle(Base, TimestampMixin):
    __tablename__ = "vehicles"
    # status: "AVAILABLE vehicles at depot X" — the optimizer's second query.
    # home_depot_id: every depot-scoped fleet view.
    __table_args__ = (
        Index("ix_vehicles_status", "status"),
        Index("ix_vehicles_home_depot_id", "home_depot_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # The number plate. UNIQUE because it identifies a physical vehicle, so a
    # duplicate means two rows for one van.
    registration_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    # Denormalised onto the vehicle rather than modelled as a driver table.
    # Fine while a van has one driver; a rota would need its own table.
    driver_name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Free text (BIKE / MINI_VAN / VAN / TRUCK), not an enum — it is descriptive
    # only, and nothing branches on it, so new types need no migration.
    vehicle_type: Mapped[str] = mapped_column(String(40), default="VAN")

    # The hard constraint the solver enforces per route.
    capacity_kg: Mapped[float] = mapped_column(Float, nullable=False)
    # Recorded but unused by the solver, which models kg only.
    capacity_volume: Mapped[float | None] = mapped_column(Float)
    # What is on board now. Set when a plan is accepted, cleared when the
    # vehicle gets back to the depot. A cached figure, derivable from the
    # route's stops, kept here so the fleet list needs no join.
    current_load_kg: Mapped[float] = mapped_column(Float, default=0.0)

    # AVAILABLE is the only status the optimizer will plan for, which is what
    # stops a van already out being given a second route.
    status: Mapped[VehicleStatus] = mapped_column(
        SAEnum(VehicleStatus, name="vehicle_status"), default=VehicleStatus.AVAILABLE
    )

    # Live position, rewritten every tick by the simulation engine. Nullable
    # because a vehicle that has never moved has no known position.
    current_latitude: Mapped[float | None] = mapped_column(Float)
    current_longitude: Mapped[float | None] = mapped_column(Float)

    # CASCADE: the fleet belongs to its depot.
    home_depot_id: Mapped[int] = mapped_column(ForeignKey("depots.id", ondelete="CASCADE"))

    # Per-vehicle range limit, enforced by the solver's Distance dimension on
    # the END node — so it bounds the whole round trip including the leg home.
    # Nullable in the schema; callers substitute 200.0 when it is unset.
    max_route_distance_km: Mapped[float | None] = mapped_column(Float, default=200.0)

    home_depot: Mapped["Depot"] = relationship(back_populates="vehicles")
    # Routes SURVIVE this vehicle (the FK is ON DELETE SET NULL), so selling a
    # van does not erase its delivery history.
    routes: Mapped[list["Route"]] = relationship(back_populates="vehicle")
