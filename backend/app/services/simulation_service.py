"""Simulation control + traffic disruption + remaining-route re-optimization.

Three kinds of function here, in increasing order of interest:

  1. **Pass-throughs** (start/stop/speed/status) — thin wrappers over the engine
     singleton. They exist so the API layer depends on a service rather than
     reaching into simulation/engine.py directly.
  2. **apply_traffic** — sets a speed factor AND works out the consequences:
     how late the route will now be, and which deliveries will miss their
     windows.
  3. **reoptimize_route** — re-plans the undelivered tail of a route that is
     already in motion. The most involved function in the module.
"""
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
#
# Same values as the engine's SPEED_FACTORS, minus "clear" — a clear road adds
# no delay, so it has nothing to contribute here.
_DELAY_FACTORS = {"moderate": 0.6, "severe": 0.35, "breakdown": 0.0}


# --- Pass-throughs to the engine singleton ---------------------------------
# No database, no rules. The engine owns its own state and opens its own
# sessions when it needs to write.


async def start_simulation(speed_multiplier: float) -> dict:
    """Start the clock. Returns {"started": false, ...} if there is no work."""
    return await engine.start(speed_multiplier)


async def stop_simulation() -> dict:
    """Stop the clock. Delivered orders stay delivered — those were committed
    to the database as they happened, not held in memory."""
    await engine.stop()
    return {"running": False}


def set_speed(multiplier: float) -> dict:
    """Change the time multiplier. Not async: it assigns one float in memory."""
    engine.set_speed(multiplier)
    return {"speed_multiplier": engine.speed_multiplier}


def simulation_status() -> dict:
    """Current engine state. Also the payload of the WebSocket SNAPSHOT event."""
    return engine.status()


async def _remaining_stops(db: AsyncSession, route_id: int) -> list[RouteStop]:
    """The stops of a route that have not been dealt with yet, in visiting order.

    PENDING only — so ARRIVED, COMPLETED and SKIPPED stops are all excluded.
    Sorted explicitly rather than relying on the relationship's order_by,
    because the filter below rebuilds the list anyway.
    """
    route = (
        await db.execute(
            # selectinload: `route.stops` is read below, and a lazy load under
            # asyncio raises MissingGreenlet rather than quietly re-querying.
            select(Route).options(selectinload(Route.stops)).where(Route.id == route_id)
        )
    ).scalar_one_or_none()
    if route is None:
        raise not_found("route", route_id)
    return [s for s in sorted(route.stops, key=lambda s: s.stop_sequence)
            if s.status == RouteStopStatus.PENDING]


async def apply_traffic(db: AsyncSession, route_id: int, severity: str) -> dict:
    """Slow a route down (or stop it), and report the fallout.

    Two distinct effects:
      * the engine starts moving the vehicle more slowly (or not at all)
      * we estimate the resulting delay and flag deliveries now at risk

    The second is what makes this useful — a dispatcher needs to know *which*
    customers to warn, not just that traffic exists.
    """
    # Validate against the engine's own table, so the two cannot disagree about
    # what a valid severity is.
    if severity not in SPEED_FACTORS:
        raise APIError("INVALID_SEVERITY", f"Unknown severity '{severity}'", 422)

    route = (await db.execute(select(Route).where(Route.id == route_id))).scalar_one_or_none()
    if route is None:
        raise not_found("route", route_id)

    # Both checks matter and neither implies the other: a route can be ACTIVE in
    # the database while absent from the engine (e.g. the process restarted), and
    # applying traffic to a route nothing is simulating would silently do nothing.
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
    #
    # At factor 0.6 the vehicle takes 1/0.6 = 1.67x as long, so the ADDED time
    # is 0.67x the original — hence the `- 1.0`.
    #
    # 99.0 for a breakdown is a sentinel, not a calculation: a stopped vehicle
    # never arrives, so the true delay is infinite. It produces a large, clearly
    # abnormal number rather than a division by zero.
    slowdown = (1.0 / factor - 1.0) if factor > 0 else 99.0

    # CAVEAT, since the names disguise it: baseline_remaining_min actually holds
    # KILOMETRES (see the sum above), and the literal 2.0 is the km-to-minutes
    # conversion — 2 minutes per km, i.e. exactly 30 km/h. So the arithmetic is
    # dimensionally correct but the 2.0 silently hardcodes AVERAGE_SPEED_KMH:
    # change that setting and this estimate becomes wrong while everything else
    # adjusts. Deriving it as (60 / settings.average_speed_kmh) would keep the
    # two in step.
    added_delay_min = round(baseline_remaining_min * 2.0 * slowdown, 1) if remaining else 0.0

    now = datetime.now(timezone.utc)
    late_orders = []
    for s in remaining:
        # The same flat delay is applied to every remaining stop, rather than
        # accumulating along the route. A simplification: in reality a later
        # stop compounds the delay of everything before it.
        new_eta = (s.estimated_arrival or now) + timedelta(minutes=added_delay_min)
        order = (await db.execute(select(Order).where(Order.id == s.order_id))).scalar_one()
        # Only orders that actually have a deadline can be late. One query per
        # stop — an N+1, acceptable because a route's remaining stops are few
        # and this runs on an explicit operator action, not on a hot path.
        if order.delivery_window_end and new_eta > order.delivery_window_end:
            late_orders.append({"order_id": order.id, "order_number": order.order_number})

    payload = {
        "route_id": route_id,
        "severity": severity,
        # `result` is None if the engine lost the route between the check above
        # and here; 0.0 is the safe reading (treat it as stopped).
        "speed_factor": result["speed_factor"] if result else 0.0,
        "added_delay_minutes": added_delay_min,
        "late_orders": late_orders,
        "remaining_stops": len(remaining),
    }
    # Broadcast so every connected dispatcher sees the disruption, not just the
    # one who triggered it.
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
    # Fewer than two stops has no ordering to improve. Returns a normal payload
    # rather than raising: "nothing to do" is a valid outcome, not an error.
    if len(pending) < 2:
        return {"route_id": route_id, "reoptimized": False, "reason": "Fewer than 2 remaining stops"}

    # Fetch all the orders in ONE query and index them by id, instead of a
    # lookup per stop. The dict is what makes the comprehension below O(n).
    orders = {
        o.id: o
        for o in (
            await db.execute(select(Order).where(Order.id.in_([s.order_id for s in pending])))
        ).scalars().all()
    }

    # Start the sub-problem from the vehicle's current position.
    #
    # This is the crux of mid-route re-planning: the van is where it is, and a
    # plan that started from the depot would be fiction.
    veh = route.vehicle
    if veh is None:
        # Possible because routes.vehicle_id is ON DELETE SET NULL — the vehicle
        # may have been removed while the route lives on.
        raise APIError("ROUTE_HAS_NO_VEHICLE", "This route has no assigned vehicle to re-optimize", 409)
    # Fall back to the depot if the vehicle has never reported a position.
    # Note this uses truthiness, so a legitimate coordinate of exactly 0.0
    # (the equator / prime meridian) would also fall back — harmless for this
    # deployment's geography, but `is not None` would be more correct.
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
            # NOTE: time windows are deliberately NOT passed. The original
            # horizon is gone and re-deriving it mid-route is ambiguous, so this
            # re-plan optimises purely for distance/time and may reorder a stop
            # out of its delivery window. apply_traffic's late_orders is what
            # surfaces window risk instead.
        )
        for s in pending
        # Walrus in the condition: bind the order and skip the stop if its order
        # somehow vanished, rather than raising a KeyError.
        if (o := orders.get(s.order_id))
    ]
    # A single-vehicle problem — we are re-planning one van's remaining work,
    # not redistributing it across the fleet.
    vehicle_input = [
        VehicleInput(
            vehicle_id=veh.id,
            registration_number=veh.registration_number,
            capacity_kg=veh.capacity_kg,
            max_route_distance_km=(veh.max_route_distance_km or 200.0),
        )
    ]

    # Uses the default time budget rather than a long one: an operator is
    # waiting, and the sub-problem is small.
    result = solve_vrp((start_lat, start_lon), order_nodes, vehicle_input)
    if not result.routes:
        return {"route_id": route_id, "reoptimized": False, "reason": "No feasible re-route found"}

    new_seq = result.routes[0].stops
    # Rewrite pending stops in the new order, continuing the sequence numbering.
    #
    # Continue from the highest COMPLETED sequence so the numbers stay monotonic
    # across the whole route — the delivered stops keep theirs untouched.
    base_seq = max((s.stop_sequence for s in route.stops if s.status != RouteStopStatus.PENDING), default=0)
    stop_by_order = {s.order_id: s for s in pending}
    # Start the new total from the distance already driven (the completed legs),
    # so the route's total_distance_km stays a whole-route figure.
    new_total_dist = sum(s.distance_from_previous_km for s in route.stops if s.status != RouteStopStatus.PENDING)

    for i, ns in enumerate(new_seq, start=1):
        stop = stop_by_order[ns.order_id]
        stop.stop_sequence = base_seq + i
        stop.distance_from_previous_km = ns.distance_from_previous_km
        # The solver's ETA is minutes from ITS start, which is now — so the new
        # horizon is the current time rather than the original planning horizon.
        stop.estimated_arrival = datetime.now(timezone.utc) + timedelta(minutes=ns.eta_minutes_from_start)
        new_total_dist += ns.distance_from_previous_km

    route.total_distance_km = round(new_total_dist, 3)
    # Commit BEFORE telling the engine to reload: the engine reads these rows in
    # its own session, so uncommitted changes would be invisible to it.
    await db.commit()

    await engine.reload_route(route_id)

    payload = {
        "route_id": route_id,
        "reoptimized": True,
        "new_sequence": [
            {"order_id": s.order_id, "order_number": s.order_number, "stop_sequence": base_seq + i}
            for i, s in enumerate(new_seq, start=1)
        ],
        # Only the remaining distance, not the whole route — this is what the
        # operator is deciding about.
        "remaining_distance_km": round(sum(s.distance_from_previous_km for s in new_seq), 2),
    }
    await manager.broadcast("ROUTE_REOPTIMIZED", payload)
    return payload
