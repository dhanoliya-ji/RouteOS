"""The `orders` table — one delivery to make.

The busiest table in the system: it is filtered, paginated, searched and
radius-queried, which is why it carries the most indexes.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from geoalchemy2 import Geography
from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import OrderPriority, OrderStatus

if TYPE_CHECKING:  # avoid a runtime circular import; see depot.py
    from app.models.depot import Depot
    from app.models.route import RouteStop


class Order(Base, TimestampMixin):
    __tablename__ = "orders"
    # Each index serves a real query rather than being added speculatively:
    #   status      — the orders list filter, and "PENDING orders at depot X",
    #                 which is the optimizer's very first query
    #   priority    — the priority filter
    #   depot_id    — every depot-scoped query
    #   created_at  — the default sort (newest first) and the date filter
    __table_args__ = (
        Index("ix_orders_status", "status"),
        Index("ix_orders_priority", "priority"),
        Index("ix_orders_depot_id", "depot_id"),
        Index("ix_orders_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Human-facing reference ("ORD-00042"), separate from the surrogate id.
    # Server-assigned, which is why it is absent from OrderCreate — a client
    # cannot choose it. UNIQUE so a duplicate is a database error, not a
    # silently ambiguous order.
    order_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)

    customer_name: Mapped[str] = mapped_column(String(120), nullable=False)
    customer_phone: Mapped[str | None] = mapped_column(String(32))
    delivery_address: Mapped[str] = mapped_column(String(255), nullable=False)

    # Same dual storage as depots: floats for the API and the solver, a PostGIS
    # point for radius search. order_service keeps them in step.
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    location: Mapped[object] = mapped_column(
        Geography(geometry_type="POINT", srid=4326), nullable=True
    )

    # The capacity constraint the solver enforces. Defaults to 1.0 rather than
    # 0.0 so an order with no stated weight still consumes a little capacity
    # instead of being free to stack infinitely.
    weight_kg: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    # Recorded but NOT constrained by the solver, which models capacity in kg
    # only. A bulky-but-light load is therefore not represented today.
    volume: Mapped[float | None] = mapped_column(Float)

    # Scales the solver's drop penalty via OrderPriority.weight.
    priority: Mapped[OrderPriority] = mapped_column(
        SAEnum(OrderPriority, name="order_priority"), default=OrderPriority.NORMAL
    )
    # PENDING is the only status the optimizer will plan for.
    status: Mapped[OrderStatus] = mapped_column(
        SAEnum(OrderStatus, name="order_status"), default=OrderStatus.PENDING
    )

    # The delivery window, as absolute timestamps. Both nullable: an order with
    # no window can be served whenever, and the solver treats it as unbounded.
    # Converted to "minutes from the planning horizon" at the solver boundary,
    # so the solver itself does integer arithmetic only.
    delivery_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # How long the stop itself takes. Charged on departure in the solver's Time
    # dimension, so it delays every later stop but not this one's arrival.
    service_time_minutes: Mapped[int] = mapped_column(Integer, default=10)

    # CASCADE: an order has no meaning without its depot.
    depot_id: Mapped[int] = mapped_column(ForeignKey("depots.id", ondelete="CASCADE"))

    depot: Mapped["Depot"] = relationship(back_populates="orders")
    # A list, not a single stop: the same order can appear on more than one
    # RouteStop over its life (a failed attempt, then a re-delivery).
    route_stops: Mapped[list["RouteStop"]] = relationship(back_populates="order")
