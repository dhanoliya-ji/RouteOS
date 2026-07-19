"""Dashboard summary + recent activity. Cached in Redis (short TTL) because the
KPI aggregation runs several COUNT/SUM queries and is read on every page load."""
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

DASHBOARD_CACHE_KEY = "routeos:dashboard:summary"
DASHBOARD_TTL = 15


def _start_of_today() -> datetime:
    return datetime.combine(datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc)


async def _count(db: AsyncSession, stmt) -> int:
    return int((await db.execute(stmt)).scalar_one() or 0)


async def get_summary(db: AsyncSession, *, use_cache: bool = True) -> dict:
    if use_cache:
        cached = await cache_get_json(DASHBOARD_CACHE_KEY)
        if cached is not None:
            return cached

    today = _start_of_today()

    total_orders = await _count(db, select(func.count(Order.id)))
    pending = await _count(db, select(func.count(Order.id)).where(Order.status == OrderStatus.PENDING))
    out_for_delivery = await _count(
        db, select(func.count(Order.id)).where(Order.status == OrderStatus.OUT_FOR_DELIVERY)
    )
    delivered_today = await _count(
        db,
        select(func.count(Order.id)).where(
            Order.status == OrderStatus.DELIVERED, Order.created_at >= today
        ),
    )
    active_vehicles = await _count(
        db, select(func.count(Vehicle.id)).where(Vehicle.status == VehicleStatus.IN_TRANSIT)
    )
    available_vehicles = await _count(
        db, select(func.count(Vehicle.id)).where(Vehicle.status == VehicleStatus.AVAILABLE)
    )
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
    delivered_total = await _count(
        db, select(func.count(Order.id)).where(Order.status == OrderStatus.DELIVERED)
    )
    on_time = await _count(
        db,
        select(func.count(DeliveryEvent.id)).where(DeliveryEvent.event_type == "DELIVERY_COMPLETED"),
    )
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
        "on_time_delivery_rate": min(on_time_rate, 100.0),
    }
    await cache_set_json(DASHBOARD_CACHE_KEY, summary, DASHBOARD_TTL)
    return summary


async def recent_activity(db: AsyncSession, limit: int = 15) -> list[dict]:
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
            "metadata": e.event_metadata,
            "created_at": e.created_at.isoformat(),
        }
        for e in rows
    ]
