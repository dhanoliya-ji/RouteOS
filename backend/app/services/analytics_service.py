"""Analytics via SQL aggregation (not Python-side row crunching), cached in Redis.

That parenthesis is the governing principle. Every function here asks Postgres
for a *summary* — a COUNT, a SUM, an AVG, a GROUP BY — and never fetches rows to
add up in Python. The difference is a few bytes over the wire versus every
matching row in the table, and it is the single most important habit in this
layer.

Five endpoints' worth of read-only reporting. Only `summary_metrics` is cached,
because it is the only one that runs many queries; the other four are a single
indexed GROUP BY each, where caching would add invalidation complexity for
little gain.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import cache_get_json, cache_set_json
from app.models.enums import OrderStatus, RouteStatus, VehicleStatus
from app.models.optimization import OptimizationRun
from app.models.order import Order
from app.models.route import Route
from app.models.vehicle import Vehicle

# 30 seconds. Longer than the dashboard's 15s because analytics describe a
# period rather than the present moment, so staleness matters less.
ANALYTICS_TTL = 30


def _cache_key(prefix: str, start: date | None, end: date | None) -> str:
    """Cache key including the date range.

    The range is part of the key because each range is a different result —
    caching them under one key would serve January's numbers for a February
    query. It also means the number of cached variants is unbounded, which is
    why cache_invalidate_prefix exists for clearing them as a group.
    """
    return f"routeos:analytics:{prefix}:{start}:{end}"


async def summary_metrics(db: AsyncSession, start: date | None = None, end: date | None = None) -> dict:
    """The KPI panel: averages, utilisation rates and mean optimizer gain.

    The heaviest function in the module — roughly ten separate aggregate
    queries — which is why it is the only cached one.
    """
    # Cache first. A miss, an unreachable Redis and a corrupt entry all return
    # None here, and the response to all three is the same: recompute.
    key = _cache_key("summary", start, end)
    cached = await cache_get_json(key)
    if cached is not None:
        return cached

    # Both bounds are optional and independent, so each is appended only if
    # given — passing just `start` means "since then".
    #
    # time.min / time.max widen a calendar date to the whole day in UTC, so a
    # route created at 18:00 on the end date is included rather than cut off at
    # midnight.
    route_filter = []
    if start:
        route_filter.append(Route.created_at >= datetime.combine(start, time.min, tzinfo=timezone.utc))
    if end:
        route_filter.append(Route.created_at <= datetime.combine(end, time.max, tzinfo=timezone.utc))

    # Build the filtered set of completed routes ONCE as a subquery, then
    # aggregate over it three times below. Repeating the filter in three
    # separate queries would risk them drifting apart as the code changes.
    #
    # Only COMPLETED routes: an average that included in-progress routes would
    # be measuring partial journeys and would read as a suspiciously low figure.
    completed_routes = select(Route).where(Route.status == RouteStatus.COMPLETED)
    for f in route_filter:
        completed_routes = completed_routes.where(f)
    completed_sub = completed_routes.subquery()

    # coalesce(..., 0.0) on every aggregate: AVG and SUM over zero rows return
    # NULL, not 0, and a None here would break the arithmetic and the JSON.
    avg_route_distance = float(
        (await db.execute(select(func.coalesce(func.avg(completed_sub.c.total_distance_km), 0.0)))).scalar_one()
    )
    # actual_duration_minutes, not estimated: this reports what really happened.
    avg_route_duration = float(
        (await db.execute(select(func.coalesce(func.avg(completed_sub.c.actual_duration_minutes), 0.0)))).scalar_one()
    )
    total_completed = int(
        (await db.execute(select(func.count(completed_sub.c.id)))).scalar_one() or 0
    )

    delivered = int(
        (await db.execute(select(func.count(Order.id)).where(Order.status == OrderStatus.DELIVERED))).scalar_one() or 0
    )
    total_orders = int((await db.execute(select(func.count(Order.id)))).scalar_one() or 0)
    unassigned = int(
        (await db.execute(select(func.count(Order.id)).where(Order.status == OrderStatus.PENDING))).scalar_one() or 0
    )

    # Vehicle & capacity utilisation
    fleet_size = int((await db.execute(select(func.count(Vehicle.id)))).scalar_one() or 0)
    # "Busy" spans both ASSIGNED (has work, not moving yet) and IN_TRANSIT
    # (moving), because from a utilisation standpoint both are committed.
    busy = int(
        (
            await db.execute(
                select(func.count(Vehicle.id)).where(
                    Vehicle.status.in_([VehicleStatus.IN_TRANSIT, VehicleStatus.ASSIGNED])
                )
            )
        ).scalar_one()
        or 0
    )
    # Two SUMs in ONE query, unpacked with .one() — cheaper than two round
    # trips, and it guarantees both figures describe the same instant.
    cap_used, cap_total = (
        await db.execute(
            select(func.coalesce(func.sum(Vehicle.current_load_kg), 0.0), func.coalesce(func.sum(Vehicle.capacity_kg), 0.0))
        )
    ).one()

    # Optimization improvement (avg over completed runs)
    #
    # Averaged over every run that recorded a percentage. Note this includes
    # runs whose plan was discarded — it measures what the optimizer achieved,
    # not what was dispatched.
    avg_improvement = float(
        (
            await db.execute(
                select(func.coalesce(func.avg(OptimizationRun.improvement_percentage), 0.0))
            )
        ).scalar_one()
    )

    # Every ratio below is guarded against a zero denominator with a trailing
    # conditional — an empty fleet or an order-less database must yield 0.0
    # rather than raising ZeroDivisionError inside a reporting endpoint.
    result = {
        "avg_route_distance_km": round(avg_route_distance, 2),
        "avg_route_duration_minutes": round(avg_route_duration, 1),
        "completed_routes": total_completed,
        "deliveries_per_vehicle": round(delivered / fleet_size, 2) if fleet_size else 0.0,
        # Reads oddly but is correct. Rearranged, this is
        # (avg_route_distance * total_completed) / delivered — i.e. total
        # distance across completed routes, divided by deliveries made.
        "distance_per_delivery_km": round(avg_route_distance / max(1, delivered) * total_completed, 2)
        if delivered
        else 0.0,
        "vehicle_utilisation_pct": round(busy / fleet_size * 100.0, 1) if fleet_size else 0.0,
        "capacity_utilisation_pct": round(float(cap_used) / float(cap_total) * 100.0, 1) if cap_total else 0.0,
        "delivered_orders": delivered,
        "unassigned_order_rate_pct": round(unassigned / total_orders * 100.0, 1) if total_orders else 0.0,
        "avg_optimization_improvement_pct": round(avg_improvement, 1),
    }
    await cache_set_json(key, result, ANALYTICS_TTL)
    return result


async def orders_by_status(db: AsyncSession) -> list[dict]:
    """Order counts per status — the pie chart.

    One GROUP BY, so Postgres returns one row per status rather than every
    order. `s.value` unwraps the enum member to its string for the JSON.

    Note a status with no orders is simply absent from the result rather than
    present with zero — GROUP BY only reports groups that exist. A chart
    wanting all six statuses shown must supply the missing ones itself.
    """
    rows = (
        await db.execute(select(Order.status, func.count(Order.id)).group_by(Order.status))
    ).all()
    return [{"status": s.value, "count": int(c)} for s, c in rows]


async def distance_by_vehicle(db: AsyncSession, limit: int = 20) -> list[dict]:
    """Distance driven per vehicle, busiest first.

    `isouter=True` makes this a LEFT JOIN, which is deliberate: a vehicle that
    has never been routed still appears, with 0.0 km, instead of vanishing from
    the chart. An inner join would silently hide idle vehicles — exactly the
    ones a fleet manager wants to see.
    """
    rows = (
        await db.execute(
            select(
                Vehicle.registration_number,
                func.coalesce(func.sum(Route.total_distance_km), 0.0).label("dist"),
                func.count(Route.id),
            )
            .join(Route, Route.vehicle_id == Vehicle.id, isouter=True)
            .group_by(Vehicle.id)
            # Order by the same expression rather than the label, since not all
            # databases permit an alias in ORDER BY.
            .order_by(func.coalesce(func.sum(Route.total_distance_km), 0.0).desc())
            .limit(limit)
        )
    ).all()
    return [
        {"vehicle": reg, "distance_km": round(float(dist), 1), "routes": int(routes)}
        for reg, dist, routes in rows
    ]


async def deliveries_over_time(db: AsyncSession, days: int = 14) -> list[dict]:
    """Daily order totals and delivered counts — the trend line.

    Two aggregations in one pass:
      * date_trunc('day', ...) buckets rows by calendar day in SQL, so no
        grouping happens in Python.
      * SUM(CASE WHEN delivered THEN 1 ELSE 0 END) is a *conditional* count
        alongside the unconditional one. Doing it this way means one scan
        produces both series; two separate queries would need two.
    """
    day = func.date_trunc("day", Order.created_at)
    rows = (
        await db.execute(
            select(
                day.label("day"),
                func.count(Order.id).label("total"),
                func.sum(case((Order.status == OrderStatus.DELIVERED, 1), else_=0)).label("delivered"),
            )
            .group_by(day)
            # DESC + LIMIT to take the most recent N days — ascending would
            # return the oldest instead.
            .order_by(day.desc())
            .limit(days)
        )
    ).all()
    out = [
        {"day": d.date().isoformat(), "total": int(t), "delivered": int(dv or 0)}
        for d, t, dv in rows
    ]
    # Reverse into chronological order: the query had to sort descending to pick
    # the latest days, but a time series plots oldest-first.
    return list(reversed(out))


async def optimization_savings(db: AsyncSession, limit: int = 20) -> list[dict]:
    """Before/after distance for recent optimization runs.

    Reads the figures *stored on each run*, which were measured against the
    greedy baseline at solve time. Deliberately not recomputed: the fleet and
    orders have changed since, so a fresh calculation would not describe the
    problem the run actually solved.

    Filters out runs with a NULL improvement — those are PROCESSING or FAILED
    and have no comparison to show.
    """
    rows = (
        await db.execute(
            select(
                OptimizationRun.id,
                OptimizationRun.created_at,
                OptimizationRun.total_distance_before,
                OptimizationRun.total_distance_after,
                OptimizationRun.improvement_percentage,
            )
            .where(OptimizationRun.improvement_percentage.is_not(None))
            .order_by(OptimizationRun.created_at.desc())
            .limit(limit)
        )
    ).all()
    out = [
        {
            "run_id": rid,
            "created_at": created.isoformat(),
            # `or 0` on each: the filter only guarantees improvement_percentage
            # is non-null, so the two distances could still be missing.
            "before_km": round(float(b or 0), 1),
            "after_km": round(float(a or 0), 1),
            "improvement_pct": round(float(imp or 0), 1),
        }
        for rid, created, b, a, imp in rows
    ]
    # Same reversal as above: newest-first for the query, oldest-first to plot.
    return list(reversed(out))
