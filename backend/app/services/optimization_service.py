"""Optimization orchestration: load data -> solve -> compare -> persist run,
plus the accept/discard workflow that turns a plan into active routes."""
from __future__ import annotations

import asyncio
import time as _time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import APIError, not_found
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


async def run_optimization(db: AsyncSession, req: OptimizationRequest) -> OptimizationRun:
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
            solve_vrp, depot_coord, order_nodes, vehicle_inputs, req.objective
        )
        baseline = await asyncio.to_thread(
            nearest_neighbour, depot_coord, order_nodes, vehicle_inputs
        )
        elapsed_ms = int((_time.perf_counter() - started) * 1000)

        comparison = _comparison(baseline, optimized, len(orders))
        opt_metrics = comparison["optimized"]

        payload = {
            "objective": req.objective.value,
            "routes": [_route_to_dict(r) for r in optimized.routes],
            "unassigned": [
                {"order_id": u.order_id, "order_number": u.order_number, "reason": u.reason}
                for u in optimized.unassigned
            ],
            "comparison": comparison,
            "objective_value": optimized.objective_value,
            "execution_time_ms": elapsed_ms,
            "matrix_source": optimized.matrix_source,
            "horizon": horizon.isoformat(),
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
