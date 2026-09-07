"""The telemetry tables — `vehicle_location_history` and `delivery_events`.

Both are **append-only**: rows are inserted, never updated. That is what
separates them from the operational tables — a route's status changes over its
life, whereas a breadcrumb is a fact about one instant and is never revised.

Neither uses TimestampMixin. Each declares its own time column instead, named
for what it records (`recorded_at` for a position sample, `created_at` for an
event), rather than inheriting a generic one.

These are the tables that grow without bound, and the first candidates for time
partitioning or a move to a time-series store.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class VehicleLocationHistory(Base):
    """Where a vehicle was, sampled roughly every 300 m of travel.

    Sampled by *distance*, not time — see simulation/engine.py. Writing a row
    per vehicle per second would be ~20 rows/second for a 20-van fleet, over a
    million a day, for a track nobody needs at that resolution. Distance
    sampling keeps the shape of the journey at a fraction of the writes, and has
    the right behaviour under traffic: a stationary vehicle writes nothing,
    because it is not going anywhere.
    """

    __tablename__ = "vehicle_location_history"
    # Both indexes serve the same query — "replay one vehicle's track over a
    # period" — which filters on vehicle_id and orders by recorded_at.
    __table_args__ = (
        Index("ix_vlh_vehicle_id", "vehicle_id"),
        Index("ix_vlh_recorded_at", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # CASCADE: breadcrumbs of a vehicle we no longer have are noise.
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id", ondelete="CASCADE"))
    # SET NULL, and nullable: the position was still real even if the route it
    # belonged to is gone.
    route_id: Mapped[int | None] = mapped_column(ForeignKey("routes.id", ondelete="SET NULL"))

    # Plain floats, no PostGIS — this table is written constantly and read as a
    # time series, never radius-searched, so a geography column and its index
    # would be pure write cost.
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    # Effective speed at the sample: average speed scaled by the traffic factor,
    # so a severe-traffic stretch is visible in the history.
    speed: Mapped[float | None] = mapped_column(Float)

    # Stamped by Postgres, so all samples share one clock regardless of which
    # process wrote them.
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DeliveryEvent(Base):
    """Something happened to an order or a route.

    A generic event log rather than a table per event type: `event_type` is a
    string and the details go in `event_metadata`, so recording a new kind of
    event needs no migration. The trade-off is that the payload is unvalidated —
    nothing enforces which keys a given event_type carries.

    Feeds the dashboard's recent-activity list.
    """

    __tablename__ = "delivery_events"
    # order_id + created_at: the per-order timeline and the recent-activity
    # feed (newest first) respectively.
    __table_args__ = (
        Index("ix_delivery_events_order_id", "order_id"),
        Index("ix_delivery_events_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Both nullable so an event can concern an order, a route, or both.
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    route_id: Mapped[int | None] = mapped_column(ForeignKey("routes.id", ondelete="CASCADE"))

    # e.g. "DELIVERY_COMPLETED". Free text by design (see the class docstring).
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)

    # The Python attribute is `event_metadata` but the COLUMN is `metadata`.
    #
    # Not a typo, and not a preference: `metadata` is already an attribute on
    # SQLAlchemy's declarative Base (it holds the table registry), so a mapped
    # attribute of that name would collide with it. The first argument to
    # mapped_column renames the attribute while keeping the column name we
    # actually want in SQL.
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
