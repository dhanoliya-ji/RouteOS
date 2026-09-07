"""The `routes` and `route_stops` tables — a plan and its ordered stops.

These two are the heart of the data model. A `Route` is "this vehicle's work
today"; a `RouteStop` is "and its Nth call is this order".

Both are created *only* by accepting an optimization run — nothing else writes
them, which is why neither has a Create schema and there is no POST /routes.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import RouteStatus, RouteStopStatus

if TYPE_CHECKING:  # avoid a runtime circular import; see depot.py
    from app.models.depot import Depot
    from app.models.order import Order
    from app.models.vehicle import Vehicle


class Route(Base, TimestampMixin):
    __tablename__ = "routes"
    # status: the active-routes board, and the engine's "PLANNED or ACTIVE" load.
    # vehicle_id: "what is this van doing?"
    __table_args__ = (
        Index("ix_routes_status", "status"),
        Index("ix_routes_vehicle_id", "vehicle_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Human-facing reference ("RT-0007"), assigned in accept_plan.
    route_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)

    # SET NULL, and therefore nullable: the route outlives the vehicle. Selling
    # a van must not delete the deliveries it made.
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id", ondelete="SET NULL"))
    # CASCADE: a route is scoped to its depot.
    depot_id: Mapped[int] = mapped_column(ForeignKey("depots.id", ondelete="CASCADE"))

    status: Mapped[RouteStatus] = mapped_column(
        SAEnum(RouteStatus, name="route_status"), default=RouteStatus.PLANNED
    )

    # --- Planned figures, copied from the solver's answer at accept time -----
    # Stored rather than recomputed so the plan a dispatcher approved stays on
    # record even if distances or speeds are later recalculated differently.
    total_distance_km: Mapped[float] = mapped_column(Float, default=0.0)
    estimated_duration_minutes: Mapped[float] = mapped_column(Float, default=0.0)

    # --- Actuals, filled in by the simulation ------------------------------
    # Estimated vs actual side by side is what makes after-the-fact schedule
    # analysis possible: the estimate came from the solver, the actual from the
    # engine when the vehicle got home.
    actual_duration_minutes: Mapped[float | None] = mapped_column(Float)

    # The improvement percentage of the run that produced this route, denormalised
    # so the routes list can show it without joining optimization_runs.
    optimization_score: Mapped[float | None] = mapped_column(Float)
    total_load_kg: Mapped[float] = mapped_column(Float, default=0.0)

    # SET NULL: a live route must not stop working because its audit record was
    # cleaned up. The provenance link is useful, not essential.
    optimization_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("optimization_runs.id", ondelete="SET NULL")
    )

    # Simulation progress cursor (index of last completed stop; -1 = at depot start)
    #
    # Denormalised: it is derivable by scanning the stops, but the simulation
    # reads it every tick, so it is cached on the parent row.
    progress_stop_index: Mapped[int] = mapped_column(Integer, default=-1)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    vehicle: Mapped["Vehicle"] = relationship(back_populates="routes")
    depot: Mapped["Depot"] = relationship(back_populates="routes")
    stops: Mapped[list["RouteStop"]] = relationship(
        back_populates="route",
        # A stop cannot exist without its route, so deleting a Route deletes its
        # stops — and removing one from this list deletes that row too, rather
        # than leaving it orphaned with a dangling route_id.
        cascade="all, delete-orphan",
        # Always ordered by visiting sequence. Without this the list would come
        # back in whatever order Postgres chose, and "the route" would be a set
        # rather than a sequence — so every reader would have to remember to
        # sort it.
        order_by="RouteStop.stop_sequence",
    )


class RouteStop(Base, TimestampMixin):
    """One call on a route: "visit this order, Nth in line".

    Orders and routes are many-to-many, but the link carries its own data
    (sequence, both arrival times, leg distance), so it cannot be a plain join
    table. In ORM terms this is an *association object*.
    """

    __tablename__ = "route_stops"
    # Loading a route's stops is the dominant access pattern.
    __table_args__ = (Index("ix_route_stops_route_id", "route_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("routes.id", ondelete="CASCADE"))
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))

    # What makes a route a *route* rather than a set of stops: the visiting
    # order the solver decided on. Re-optimization rewrites these for PENDING
    # stops while leaving completed ones alone.
    stop_sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    # The order's coordinates, copied here at accept time. Denormalised on
    # purpose: it freezes where the vehicle was actually sent, so later editing
    # the order's address does not silently rewrite history.
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)

    # The solver's promise, and what happened. The pair is the raw material for
    # on-time analysis.
    estimated_arrival: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_arrival: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Length of the leg that ended here. Per-leg rather than cumulative, so a
    # route's distance is the sum of its stops and re-sequencing only has to
    # rewrite the legs it changed.
    distance_from_previous_km: Mapped[float] = mapped_column(Float, default=0.0)

    # PENDING vs COMPLETED is what makes mid-route re-optimization safe.
    status: Mapped[RouteStopStatus] = mapped_column(
        SAEnum(RouteStopStatus, name="route_stop_status"), default=RouteStopStatus.PENDING
    )

    route: Mapped["Route"] = relationship(back_populates="stops")
    order: Mapped["Order"] = relationship(back_populates="route_stops")
