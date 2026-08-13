"""Optimization orchestration: load data -> solve -> compare -> persist run,
plus the accept/discard workflow that turns a plan into active routes."""
from __future__ import annotations

import asyncio
import time as _time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import APIError, not_found
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.depot import Depot
from app.models.enums import (
    OptimizationObjective,
    OptimizationStatus,
    OrderStatus,
    RouteStatus,
    VehicleStatus,
)
from app.models.optimization import OptimizationRun
from app.models.order import Order
from app.models.route import Route, RouteStop
from app.models.vehicle import Vehicle
from app.optimization.baseline import nearest_neighbour
from app.optimization.solver import solve_vrp
from app.optimization.types import OrderNode, SolveResult, VehicleInput
from app.schemas.optimization import OptimizationRequest
from app.websocket.manager import manager

logger = get_logger(__name__)

# Strong references to in-flight background solves. asyncio only holds weak
# references to tasks, so without this a job can be garbage-collected mid-solve.
_JOBS: set[asyncio.Task] = set()

_HEARTBEAT_SECONDS = 5


def _metrics(result: SolveResult, total_orders: int) -> dict:
    total_distance = round(sum(r.total_distance_km for r in result.routes), 2)
    total_time = round(sum(r.estimated_duration_minutes for r in result.routes), 1)
    assigned = sum(len(r.stops) for r in result.routes)
    return {
        "total_distance_km": total_distance,
        "estimated_duration_minutes": total_time,
        "vehicles_used": len(result.routes),
        "assigned_orders": assigned,
        "unassigned_orders": total_orders - assigned,
    }


def _pct(before: float, after: float) -> float:
    if before <= 0:
        return 0.0
    return round((before - after) / before * 100.0, 1)


# Distance-equivalent cost of putting one more vehicle on the road. This mirrors
# VEHICLE_FIXED_COST in the solver model (300_000 metre-equivalents = 300 km), so
# the two plans are ranked by the same trade-off the solver itself optimises.
VEHICLE_COST_KM_EQUIVALENT = 300.0


def _plan_cost(result: SolveResult) -> float:
    distance = sum(r.total_distance_km for r in result.routes)
    return distance + VEHICLE_COST_KM_EQUIVALENT * len(result.routes)


def _better_plan(
    baseline: SolveResult, optimized: SolveResult, total_orders: int
) -> tuple[SolveResult, str]:
    """Pick the plan to dispatch, preferring the solver on ties.

    Serving more orders always wins: a cheaper plan that strands deliveries is
    not actually better. Otherwise the model's own distance/vehicle trade-off
    decides.
    """
    opt_assigned = sum(len(r.stops) for r in optimized.routes)
    base_assigned = sum(len(r.stops) for r in baseline.routes)
    if opt_assigned != base_assigned:
        return (
            (optimized, "solver") if opt_assigned > base_assigned else (baseline, "baseline")
        )
    if _plan_cost(baseline) < _plan_cost(optimized):
        return baseline, "baseline"
    return optimized, "solver"


def _comparison(baseline: SolveResult, optimized: SolveResult, total_orders: int) -> dict:
    b = _metrics(baseline, total_orders)
    o = _metrics(optimized, total_orders)
    return {
        "baseline": b,
        "optimized": o,
        "distance_reduction_pct": _pct(b["total_distance_km"], o["total_distance_km"]),
        "time_reduction_pct": _pct(b["estimated_duration_minutes"], o["estimated_duration_minutes"]),
        "vehicles_reduction_pct": _pct(b["vehicles_used"], o["vehicles_used"]),
    }


def _route_to_dict(r) -> dict:
    return {
        "vehicle_id": r.vehicle_id,
        "registration_number": r.registration_number,
        "total_distance_km": r.total_distance_km,
        "estimated_duration_minutes": r.estimated_duration_minutes,
        "total_load_kg": r.total_load_kg,
        "capacity_kg": r.capacity_kg,
        "stops": [
            {
                "order_id": s.order_id,
                "order_number": s.order_number,
                "stop_sequence": s.stop_sequence,
                "latitude": s.coord[0],
                "longitude": s.coord[1],
                "distance_from_previous_km": s.distance_from_previous_km,
                "load_kg": s.load_kg,
                "eta_minutes_from_start": s.eta_minutes_from_start,
            }
            for s in r.stops
        ],
    }


async def _load_inputs(db: AsyncSession, req: OptimizationRequest):
    """Resolve and validate the depot, orders and vehicles for a request."""
    depot = (await db.execute(select(Depot).where(Depot.id == req.depot_id))).scalar_one_or_none()
    if depot is None:
        raise not_found("depot", req.depot_id)

    order_stmt = select(Order).where(
        Order.depot_id == req.depot_id, Order.status == OrderStatus.PENDING
    )
    if req.order_ids:
        order_stmt = order_stmt.where(Order.id.in_(req.order_ids))
    orders = list((await db.execute(order_stmt)).scalars().all())

    vehicle_stmt = select(Vehicle).where(
        Vehicle.home_depot_id == req.depot_id, Vehicle.status == VehicleStatus.AVAILABLE
    )
    if req.vehicle_ids:
        vehicle_stmt = vehicle_stmt.where(Vehicle.id.in_(req.vehicle_ids))
    vehicles = list((await db.execute(vehicle_stmt)).scalars().all())

    if not orders:
        raise APIError("NO_ORDERS", "No pending orders match the request", 422)
    if not vehicles:
        raise APIError("NO_VEHICLES", "No available vehicles match the request", 422)

    return depot, orders, vehicles


async def run_optimization(
    db: AsyncSession, req: OptimizationRequest, time_limit_seconds: int | None = None
) -> OptimizationRun:
    depot, orders, vehicles = await _load_inputs(db, req)
    run = OptimizationRun(
        status=OptimizationStatus.PROCESSING,
        objective=req.objective,
        depot_id=req.depot_id,
        orders_count=len(orders),
        vehicles_count=len(vehicles),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return await _execute_run(db, run, req, depot, orders, vehicles, time_limit_seconds)


async def _execute_run(
    db: AsyncSession,
    run: OptimizationRun,
    req: OptimizationRequest,
    depot: Depot,
    orders: list[Order],
    vehicles: list[Vehicle],
    time_limit_seconds: int | None = None,
) -> OptimizationRun:
    try:
        # Anchor the planning horizon at the earliest delivery window (or now).
        window_starts = [o.delivery_window_start for o in orders if o.delivery_window_start]
        horizon = min(window_starts) if window_starts else datetime.now(timezone.utc)

        def to_min(dt: datetime | None) -> int | None:
            if dt is None:
                return None
            return max(0, int((dt - horizon).total_seconds() // 60))

        order_nodes = [
            OrderNode(
                order_id=o.id,
                order_number=o.order_number,
                coord=(o.latitude, o.longitude),
                demand_kg=o.weight_kg,
                service_time_min=o.service_time_minutes,
                tw_start_min=to_min(o.delivery_window_start),
                tw_end_min=to_min(o.delivery_window_end),
                priority_weight=o.priority.weight,
            )
            for o in orders
        ]
        vehicle_inputs = [
            VehicleInput(
                vehicle_id=v.id,
                registration_number=v.registration_number,
                capacity_kg=v.capacity_kg,
                max_route_distance_km=v.max_route_distance_km or 200.0,
            )
            for v in vehicles
        ]
        depot_coord = (depot.latitude, depot.longitude)

        # OR-Tools is CPU-bound; run it off the event loop so the API stays responsive.
        started = _time.perf_counter()
        optimized = await asyncio.to_thread(
            solve_vrp, depot_coord, order_nodes, vehicle_inputs, req.objective, time_limit_seconds
        )
        baseline = await asyncio.to_thread(
            nearest_neighbour, depot_coord, order_nodes, vehicle_inputs
        )
        elapsed_ms = int((_time.perf_counter() - started) * 1000)

        # Take whichever plan is actually better. The VRP solver is a heuristic
        # under a wall-clock budget: on a large instance with little CPU it can
        # stop while still worse than the greedy baseline, and shipping that
        # would mean dispatching a plan a human router would have beaten.
        # Running both and keeping the winner is standard portfolio practice.
        chosen, plan_source = _better_plan(baseline, optimized, len(orders))

        comparison = _comparison(baseline, chosen, len(orders))
        opt_metrics = comparison["optimized"]

        payload = {
            "objective": req.objective.value,
            "routes": [_route_to_dict(r) for r in chosen.routes],
            "unassigned": [
                {"order_id": u.order_id, "order_number": u.order_number, "reason": u.reason}
                for u in chosen.unassigned
            ],
            "comparison": comparison,
            "objective_value": chosen.objective_value,
            "execution_time_ms": elapsed_ms,
            "matrix_source": optimized.matrix_source,
            "horizon": horizon.isoformat(),
            # Which heuristic produced the dispatched plan. "baseline" means the
            # solver failed to beat greedy within its time budget — a signal to
            # raise SOLVER_TIME_LIMIT_SECONDS or give the service more CPU.
            "plan_source": plan_source,
            "solver_plan": {
                "total_distance_km": round(sum(r.total_distance_km for r in optimized.routes), 2),
                "vehicles_used": len(optimized.routes),
            },
        }

        run.status = OptimizationStatus.COMPLETED
        run.assigned_count = opt_metrics["assigned_orders"]
        run.unassigned_count = opt_metrics["unassigned_orders"]
        run.total_distance_before = comparison["baseline"]["total_distance_km"]
        run.total_distance_after = opt_metrics["total_distance_km"]
        run.improvement_percentage = comparison["distance_reduction_pct"]
        run.execution_time_ms = elapsed_ms
        run.objective_value = optimized.objective_value
        run.result_payload = payload
    except Exception as exc:  # noqa: BLE001
        run.status = OptimizationStatus.FAILED
        run.error_message = str(exc)[:490]
        await db.commit()
        raise

    await db.commit()
    await db.refresh(run)
    return run


def _budget_for(order_count: int) -> int:
    """Scale the solve budget to the problem size.

    Search difficulty grows with the number of stops, so spending the full
    budget on a small instance just makes the user wait after the search has
    already converged: 50 orders settles in a few CPU-seconds while 150 needs
    every second it can get. Bounded below so tiny runs still get a real search,
    and above by the configured ceiling.
    """
    scaled = int(order_count * settings.solver_seconds_per_order)
    return max(
        settings.solver_min_async_time_limit_seconds,
        min(settings.solver_async_time_limit_seconds, scaled),
    )


async def start_optimization_job(
    db: AsyncSession, req: OptimizationRequest
) -> OptimizationRun:
    """Validate the request, persist a PROCESSING run, and solve in the background.

    Returns immediately so the client never holds a connection open for the solve.
    That is what lets the budget be minutes rather than the seconds an HTTP
    request can tolerate — on a shared-CPU instance the extra search is the
    difference between beating the greedy baseline and falling back to it.
    """
    # Each solve pins a CPU for minutes. Without a cap, repeated clicks on a
    # public demo pile up CPU-bound work and starve both the solves and the API.
    if len(_JOBS) >= settings.max_concurrent_optimization_jobs:
        raise APIError(
            "OPTIMIZER_BUSY",
            f"{len(_JOBS)} optimization run(s) already in progress. Wait for one to finish.",
            429,
        )

    depot, orders, vehicles = await _load_inputs(db, req)
    run = OptimizationRun(
        status=OptimizationStatus.PROCESSING,
        objective=req.objective,
        depot_id=req.depot_id,
        orders_count=len(orders),
        vehicles_count=len(vehicles),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    budget = _budget_for(len(orders))
    # Keep a reference: a bare create_task can be garbage-collected mid-flight.
    task = asyncio.create_task(_run_job(run.id, req, budget))
    _JOBS.add(task)
    task.add_done_callback(_JOBS.discard)

    await manager.broadcast(
        "OPTIMIZATION_STARTED",
        {
            "run_id": run.id,
            "orders_count": run.orders_count,
            "vehicles_count": run.vehicles_count,
            "objective": req.objective.value,
            "budget_seconds": budget,
        },
    )
    return run


async def _run_job(run_id: int, req: OptimizationRequest, budget: int) -> None:
    """Background solve. Owns its own DB session — the request's session is gone."""
    heartbeat = asyncio.create_task(_heartbeat(run_id, budget))
    try:
        async with AsyncSessionLocal() as db:
            run = (
                await db.execute(select(OptimizationRun).where(OptimizationRun.id == run_id))
            ).scalar_one()
            try:
                depot, orders, vehicles = await _load_inputs(db, req)
                run = await _execute_run(db, run, req, depot, orders, vehicles, budget)
            except Exception as exc:  # noqa: BLE001
                # _execute_run already marks FAILED and re-raises; catch so the
                # task never dies silently, and always tell the clients.
                logger.exception("optimization run %s failed", run_id)
                await manager.broadcast(
                    "OPTIMIZATION_FAILED", {"run_id": run_id, "error": str(exc)[:200]}
                )
                return

            payload = run.result_payload or {}
            await manager.broadcast(
                "OPTIMIZATION_COMPLETED",
                {
                    "run_id": run.id,
                    "status": run.status.value,
                    "improvement_percentage": run.improvement_percentage,
                    "total_distance_before": run.total_distance_before,
                    "total_distance_after": run.total_distance_after,
                    "assigned_count": run.assigned_count,
                    "unassigned_count": run.unassigned_count,
                    "execution_time_ms": run.execution_time_ms,
                    "plan_source": payload.get("plan_source"),
                },
            )
    finally:
        heartbeat.cancel()


async def _heartbeat(run_id: int, budget: int) -> None:
    """Emit progress while the solver works, so the UI can show real motion."""
    elapsed = 0
    try:
        while True:
            await asyncio.sleep(_HEARTBEAT_SECONDS)
            elapsed += _HEARTBEAT_SECONDS
            await manager.broadcast(
                "OPTIMIZATION_PROGRESS",
                {
                    "run_id": run_id,
                    "elapsed_seconds": elapsed,
                    "budget_seconds": budget,
                    # The solve is a fixed wall-clock budget, so elapsed/budget is
                    # an honest completion fraction rather than a guess.
                    "progress_pct": min(99, round(elapsed / max(1, budget) * 100)),
                },
            )
    except asyncio.CancelledError:  # pragma: no cover - normal shutdown path
        pass


async def get_run(db: AsyncSession, run_id: int) -> OptimizationRun:
    run = (
        await db.execute(select(OptimizationRun).where(OptimizationRun.id == run_id))
    ).scalar_one_or_none()
    if run is None:
        raise not_found("optimization_run", run_id)
    return run


async def list_runs(db: AsyncSession, limit: int = 50) -> list[OptimizationRun]:
    stmt = select(OptimizationRun).order_by(OptimizationRun.created_at.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def accept_plan(db: AsyncSession, run_id: int) -> list[Route]:
    """Materialise a completed optimization run into active planned routes."""
    run = await get_run(db, run_id)
    if run.status != OptimizationStatus.COMPLETED or not run.result_payload:
        raise APIError("PLAN_NOT_READY", "Optimization run is not in a completed state", 409)

    payload = run.result_payload
    horizon = datetime.fromisoformat(payload["horizon"])

    max_route_id = (
        await db.execute(select(Route.id).order_by(Route.id.desc()).limit(1))
    ).scalar_one_or_none() or 0
    created: list[Route] = []
    seq_counter = max_route_id

    for r in payload["routes"]:
        seq_counter += 1
        route = Route(
            route_code=f"RT-{seq_counter:04d}",
            vehicle_id=r["vehicle_id"],
            depot_id=run.depot_id,
            status=RouteStatus.PLANNED,
            total_distance_km=r["total_distance_km"],
            estimated_duration_minutes=r["estimated_duration_minutes"],
            total_load_kg=r["total_load_kg"],
            optimization_score=run.improvement_percentage,
            optimization_run_id=run.id,
            progress_stop_index=-1,
        )
        db.add(route)
        await db.flush()  # get route.id

        for s in r["stops"]:
            eta = horizon + timedelta(minutes=s["eta_minutes_from_start"])
            db.add(
                RouteStop(
                    route_id=route.id,
                    order_id=s["order_id"],
                    stop_sequence=s["stop_sequence"],
                    latitude=s["latitude"],
                    longitude=s["longitude"],
                    estimated_arrival=eta,
                    distance_from_previous_km=s["distance_from_previous_km"],
                )
            )
            order = (await db.execute(select(Order).where(Order.id == s["order_id"]))).scalar_one()
            order.status = OrderStatus.ASSIGNED

        vehicle = (
            await db.execute(select(Vehicle).where(Vehicle.id == r["vehicle_id"]))
        ).scalar_one()
        vehicle.status = VehicleStatus.ASSIGNED
        vehicle.current_load_kg = r["total_load_kg"]
        created.append(route)

    await db.commit()
    for route in created:
        await db.refresh(route)
    return created


async def discard_plan(db: AsyncSession, run_id: int) -> None:
    run = await get_run(db, run_id)
    if run.status == OptimizationStatus.COMPLETED:
        run.status = OptimizationStatus.FAILED
        run.error_message = "Plan discarded by user"
        await db.commit()
