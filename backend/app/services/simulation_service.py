"""Simulation control + traffic disruption + remaining-route re-optimization."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import APIError, not_found
from app.models.enums import RouteStatus, RouteStopStatus
from app.models.order import Order
from app.models.route import Route, RouteStop
from app.optimization.solver import solve_vrp
from app.optimization.types import OrderNode, VehicleInput
from app.simulation.engine import SPEED_FACTORS, engine
from app.websocket.manager import manager

# Extra travel-time multiplier used to compute a human-readable delay estimate.
_DELAY_FACTORS = {"moderate": 0.6, "severe": 0.35, "breakdown": 0.0}


async def start_simulation(speed_multiplier: float) -> dict:
    return await engine.start(speed_multiplier)


async def stop_simulation() -> dict:
    await engine.stop()
    return {"running": False}


def set_speed(multiplier: float) -> dict:
    engine.set_speed(multiplier)
    return {"speed_multiplier": engine.speed_multiplier}


def simulation_status() -> dict:
    return engine.status()


async def _remaining_stops(db: AsyncSession, route_id: int) -> list[RouteStop]:
    route = (
        await db.execute(
            select(Route).options(selectinload(Route.stops)).where(Route.id == route_id)
        )
    ).scalar_one_or_none()
    if route is None:
        raise not_found("route", route_id)
    return [s for s in sorted(route.stops, key=lambda s: s.stop_sequence)
            if s.status == RouteStopStatus.PENDING]


async def apply_traffic(db: AsyncSession, route_id: int, severity: str) -> dict:
    if severity not in SPEED_FACTORS:
        raise APIError("INVALID_SEVERITY", f"Unknown severity '{severity}'", 422)
    route = (await db.execute(select(Route).where(Route.id == route_id))).scalar_one_or_none()
    if route is None:
        raise not_found("route", route_id)
    if route.status != RouteStatus.ACTIVE or not engine.has_route(route_id):
        raise APIError("ROUTE_NOT_ACTIVE", "Traffic can only be applied to an active, simulating route", 409)

    result = engine.apply_traffic(route_id, severity)

    # Estimate the additional delay on the remaining portion of the route.
    remaining = await _remaining_stops(db, route_id)
    factor = _DELAY_FACTORS.get(severity, 1.0)
    baseline_remaining_min = sum(
        # rough: distance_from_previous at avg speed already baked into ETA gaps
        s.distance_from_previous_km for s in remaining
    )
    # Delay = extra time because speed drops by `factor`.
    slowdown = (1.0 / factor - 1.0) if factor > 0 else 99.0
    added_delay_min = round(baseline_remaining_min * 2.0 * slowdown, 1) if remaining else 0.0

    now = datetime.now(timezone.utc)
    late_orders = []
    for s in remaining:
        new_eta = (s.estimated_arrival or now) + timedelta(minutes=added_delay_min)
        order = (await db.execute(select(Order).where(Order.id == s.order_id))).scalar_one()
        if order.delivery_window_end and new_eta > order.delivery_window_end:
            late_orders.append({"order_id": order.id, "order_number": order.order_number})

    payload = {
        "route_id": route_id,
        "severity": severity,
        "speed_factor": result["speed_factor"] if result else 0.0,
        "added_delay_minutes": added_delay_min,
        "late_orders": late_orders,
        "remaining_stops": len(remaining),
    }
    await manager.broadcast("ROUTE_DELAYED", payload)
    return payload


async def reoptimize_route(db: AsyncSession, route_id: int) -> dict:
    """Re-sequence only the PENDING (undelivered) stops of an active route.

    Completed stops are preserved. A single-vehicle VRP is solved over the
    remaining orders, the route's pending stops are rewritten, and the running
    simulation reloads the route on its next tick.
    """
    route = (
        await db.execute(
            select(Route)
            .options(
                selectinload(Route.stops),
                selectinload(Route.depot),
                # Eager-load the vehicle too: it is read below to seed the
                # re-route from the vehicle's live position, and an async
                # lazy-load here would raise MissingGreenlet.
                selectinload(Route.vehicle),
            )
            .where(Route.id == route_id)
        )
    ).scalar_one_or_none()
    if route is None:
        raise not_found("route", route_id)
    if route.status != RouteStatus.ACTIVE:
        raise APIError("ROUTE_NOT_ACTIVE", "Only active routes can be re-optimized", 409)

    pending = [s for s in sorted(route.stops, key=lambda s: s.stop_sequence)
               if s.status == RouteStopStatus.PENDING]
    if len(pending) < 2:
        return {"route_id": route_id, "reoptimized": False, "reason": "Fewer than 2 remaining stops"}

    orders = {
        o.id: o
        for o in (
            await db.execute(select(Order).where(Order.id.in_([s.order_id for s in pending])))
        ).scalars().all()
    }

    # Start the sub-problem from the vehicle's current position.
    veh = route.vehicle
    if veh is None:
        raise APIError("ROUTE_HAS_NO_VEHICLE", "This route has no assigned vehicle to re-optimize", 409)
    start_lat = veh.current_latitude if veh.current_latitude else route.depot.latitude
    start_lon = veh.current_longitude if veh.current_longitude else route.depot.longitude

    order_nodes = [
        OrderNode(
            order_id=o.id,
            order_number=o.order_number,
            coord=(o.latitude, o.longitude),
            demand_kg=o.weight_kg,
            service_time_min=o.service_time_minutes,
            priority_weight=o.priority.weight,
        )
        for s in pending
        if (o := orders.get(s.order_id))
    ]
    vehicle_input = [
        VehicleInput(
            vehicle_id=veh.id,
            registration_number=veh.registration_number,
            capacity_kg=veh.capacity_kg,
            max_route_distance_km=(veh.max_route_distance_km or 200.0),
        )
    ]

    result = solve_vrp((start_lat, start_lon), order_nodes, vehicle_input)
    if not result.routes:
        return {"route_id": route_id, "reoptimized": False, "reason": "No feasible re-route found"}

    new_seq = result.routes[0].stops
    # Rewrite pending stops in the new order, continuing the sequence numbering.
    base_seq = max((s.stop_sequence for s in route.stops if s.status != RouteStopStatus.PENDING), default=0)
    stop_by_order = {s.order_id: s for s in pending}
    new_total_dist = sum(s.distance_from_previous_km for s in route.stops if s.status != RouteStopStatus.PENDING)
    for i, ns in enumerate(new_seq, start=1):
        stop = stop_by_order[ns.order_id]
        stop.stop_sequence = base_seq + i
        stop.distance_from_previous_km = ns.distance_from_previous_km
        stop.estimated_arrival = datetime.now(timezone.utc) + timedelta(minutes=ns.eta_minutes_from_start)
        new_total_dist += ns.distance_from_previous_km

    route.total_distance_km = round(new_total_dist, 3)
    await db.commit()

    await engine.reload_route(route_id)
    payload = {
        "route_id": route_id,
        "reoptimized": True,
        "new_sequence": [
            {"order_id": s.order_id, "order_number": s.order_number, "stop_sequence": base_seq + i}
            for i, s in enumerate(new_seq, start=1)
        ],
        "remaining_distance_km": round(sum(s.distance_from_previous_km for s in new_seq), 2),
    }
    await manager.broadcast("ROUTE_REOPTIMIZED", payload)
    return payload
