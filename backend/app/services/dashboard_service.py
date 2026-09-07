"""Dashboard summary + recent activity. Cached in Redis (short TTL) because the
KPI aggregation runs several COUNT/SUM queries and is read on every page load.

Distinct from analytics_service: this answers "what is happening now" rather
than "what happened over a period", which is why it takes no date range and
uses a shorter TTL.
"""
from __future__ import annotations

from datetime import datetime, time, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import cache_get_json, cache_set_json
from app.models.enums import OrderStatus, RouteStatus, VehicleStatus
from app.models.order import Order
from app.models.route import Route
from app.models.telemetry import DeliveryEvent
from app.models.vehicle import Vehicle

# A single fixed key — there are no parameters, so there is only one variant to
# cache. That is also what lets optimization.py invalidate it by name.
DASHBOARD_CACHE_KEY = "routeos:dashboard:summary"
# 15 seconds: an operations dashboard may be a few seconds behind, but not a
# minute. Shorter than the analytics TTL because this describes the present.
DASHBOARD_TTL = 15


def _start_of_today() -> datetime:
    """Midnight UTC today, as the boundary for "today's" figures.

    UTC rather than a local timezone, so the number does not depend on where the
    server happens to be. Worth knowing when reading "delivered today": the day
    rolls over at UTC midnight, not the viewer's.
    """
    return datetime.combine(datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc)


async def _count(db: AsyncSession, stmt) -> int:
    """Run a COUNT statement and return a plain int.

    A tiny helper because this module does it nine times, and each call would
    otherwise repeat the same execute/scalar_one/or-0/int dance. The `or 0`
    guards a NULL from an empty result.
    """
    return int((await db.execute(stmt)).scalar_one() or 0)


async def get_summary(db: AsyncSession, *, use_cache: bool = True) -> dict:
    """The KPI tiles.

    `use_cache=False` exists so a caller can force fresh numbers — useful right
    after a state change, and for testing that the cache is not masking a bug.
    """
    if use_cache:
        cached = await cache_get_json(DASHBOARD_CACHE_KEY)
        if cached is not None:
            return cached

    today = _start_of_today()

    # Each of these is a COUNT the database performs; none fetches rows.
    total_orders = await _count(db, select(func.count(Order.id)))
    # The dispatcher's work queue — and the same set the optimizer plans over.
    pending = await _count(db, select(func.count(Order.id)).where(Order.status == OrderStatus.PENDING))
    out_for_delivery = await _count(
        db, select(func.count(Order.id)).where(Order.status == OrderStatus.OUT_FOR_DELIVERY)
    )
    # NOTE: filters on created_at, not on when it was delivered. So this counts
    # orders *created* today that are now delivered — an order created yesterday
    # and delivered this morning is not included. Counting true same-day
    # deliveries would mean joining route_stops on actual_arrival.
    delivered_today = await _count(
        db,
        select(func.count(Order.id)).where(
            Order.status == OrderStatus.DELIVERED, Order.created_at >= today
        ),
    )
    # "Active" is IN_TRANSIT only — actually moving. ASSIGNED vehicles are
    # committed but stationary, and are counted as neither active nor available.
    active_vehicles = await _count(
        db, select(func.count(Vehicle.id)).where(Vehicle.status == VehicleStatus.IN_TRANSIT)
    )
    available_vehicles = await _count(
        db, select(func.count(Vehicle.id)).where(Vehicle.status == VehicleStatus.AVAILABLE)
    )
    # Planned distance, summed over routes created today — not distance actually
    # driven so far, which would need the location history.
    total_distance_today = float(
        (
            await db.execute(
                select(func.coalesce(func.sum(Route.total_distance_km), 0.0)).where(
                    Route.created_at >= today
                )
            )
        ).scalar_one()
    )
    active_routes = await _count(
        db, select(func.count(Route.id)).where(Route.status == RouteStatus.ACTIVE)
    )

    # On-time rate: delivered stops whose actual arrival <= order window end.
    #
    # WARNING: the query below does NOT implement that description.
    #
    # `on_time` counts every DELIVERY_COMPLETED event, and the engine writes one
    # for *every* delivery regardless of timing — so the ratio is ~100% by
    # construction and the min(..., 100.0) clamp hides the remainder. It is
    # currently a delivery-completion rate, not an on-time rate.
    #
    # Measuring the stated intent means comparing route_stops.actual_arrival
    # against orders.delivery_window_end. Both are stored, so the data is
    # already there — see the note in app/services/README.md.
    delivered_total = await _count(
        db, select(func.count(Order.id)).where(Order.status == OrderStatus.DELIVERED)
    )
    on_time = await _count(
        db,
        select(func.count(DeliveryEvent.id)).where(DeliveryEvent.event_type == "DELIVERY_COMPLETED"),
    )
    # Defaults to 100.0 with nothing delivered: an empty system is not failing.
    on_time_rate = round((on_time / delivered_total * 100.0), 1) if delivered_total else 100.0

    summary = {
        "total_orders": total_orders,
        "pending": pending,
        "out_for_delivery": out_for_delivery,
        "delivered_today": delivered_today,
        "active_vehicles": active_vehicles,
        "available_vehicles": available_vehicles,
        "total_distance_today_km": round(total_distance_today, 1),
        "active_routes": active_routes,
        # Clamped because events can outnumber delivered orders — an order
        # re-delivered after a failure has two events — which would otherwise
        # produce a rate above 100%.
        "on_time_delivery_rate": min(on_time_rate, 100.0),
    }
    # Written even when use_cache=False, so a forced refresh also repopulates
    # the cache for the next reader.
    await cache_set_json(DASHBOARD_CACHE_KEY, summary, DASHBOARD_TTL)
    return summary


async def recent_activity(db: AsyncSession, limit: int = 15) -> list[dict]:
    """The latest delivery events, newest first.

    Uncached, unlike the summary: it is one indexed query on
    delivery_events(created_at), and this is the panel a user most expects to be
    live.

    Shaped by hand rather than through a Pydantic model because the event
    payload is a free-form JSONB blob — there is no fixed schema to declare.
    """
    rows = (
        await db.execute(
            select(DeliveryEvent).order_by(DeliveryEvent.created_at.desc()).limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": e.id,
            "event_type": e.event_type,
            "order_id": e.order_id,
            "route_id": e.route_id,
            # Exposed as "metadata", the column's real name — the Python
            # attribute is event_metadata only because `metadata` collides with
            # SQLAlchemy's declarative Base. See models/telemetry.py.
            "metadata": e.event_metadata,
            # isoformat() rather than the datetime object, so the dict is
            # JSON-serialisable for the cache and the response.
            "created_at": e.created_at.isoformat(),
        }
        for e in rows
    ]
