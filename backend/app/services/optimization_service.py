"""Optimization orchestration: load data -> solve -> compare -> persist run,
plus the accept/discard workflow that turns a plan into active routes.

The biggest service, and the only one running a genuinely multi-stage workflow.
The shape of it:

    _load_inputs      resolve and validate the depot, orders, vehicles
          |
    _execute_run      solve TWICE (real solver + greedy baseline),
          |           keep whichever plan is actually better,
          |           store the whole thing as JSON. Changes nothing else.
          |
      [a human reviews the plan]
          |
    accept_plan       replay the stored JSON into real routes
    discard_plan      throw it away

Two ways in: `run_optimization` solves inline while a client waits, and
`start_optimization_job` returns immediately and solves in the background. The
second exists because a good solve wants minutes, which is longer than an HTTP
request should live.
"""
from __future__ import annotations

import asyncio
import time as _time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
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
#
# A module-level set, so it is also the count used for the concurrency cap
# below. Entries are removed by a done-callback, so the set is self-cleaning.
_JOBS: set[asyncio.Task] = set()

# How often a background solve reports progress over the WebSocket. Five seconds
# is frequent enough for a progress bar to look alive without flooding clients.
_HEARTBEAT_SECONDS = 5


def _metrics(result: SolveResult, total_orders: int) -> dict:
    """Summarise one plan into the figures the UI and the run row need.

    Takes `total_orders` rather than deriving it, because a plan's own
    `unassigned` list is not authoritative here: the caller compares two plans
    over the same input, so the unassigned count must be measured against that
    shared total.
    """
    total_distance = round(sum(r.total_distance_km for r in result.routes), 2)
    total_time = round(sum(r.estimated_duration_minutes for r in result.routes), 1)
    assigned = sum(len(r.stops) for r in result.routes)
    return {
        "total_distance_km": total_distance,
        "estimated_duration_minutes": total_time,
        # Only routes with stops exist in `routes`, so this is genuinely
        # "vehicles used", not "vehicles offered".
        "vehicles_used": len(result.routes),
        "assigned_orders": assigned,
        "unassigned_orders": total_orders - assigned,
    }


def _pct(before: float, after: float) -> float:
    """Percentage reduction from `before` to `after`.

    Guards a zero baseline: with nothing to improve on, the honest answer is
    0% rather than a division error or an infinite improvement.

    Note a negative result is meaningful — it says `after` is worse.
    """
    if before <= 0:
        return 0.0
    return round((before - after) / before * 100.0, 1)


# Distance-equivalent cost of putting one more vehicle on the road. This mirrors
# VEHICLE_FIXED_COST in the solver model (300_000 metre-equivalents = 300 km), so
# the two plans are ranked by the same trade-off the solver itself optimises.
#
# Keeping these two constants in step matters: judging the plans by a different
# exchange rate than the solver optimised for could make us reject the plan the
# model considers better. tests/test_plan_selection.py pins the 300.
VEHICLE_COST_KM_EQUIVALENT = 300.0


def _plan_cost(result: SolveResult) -> float:
    """Score a plan the way the solver's own objective does: distance + vehicles.

    Distance alone would always prefer more vehicles (more vans means shorter
    individual routes), so the vehicle term is what makes consolidation count.
    """
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
    # TEST 1 — coverage, which outranks cost entirely. A plan that drives fewer
    # kilometres by simply not delivering things is not an improvement.
    opt_assigned = sum(len(r.stops) for r in optimized.routes)
    base_assigned = sum(len(r.stops) for r in baseline.routes)
    if opt_assigned != base_assigned:
        return (
            (optimized, "solver") if opt_assigned > base_assigned else (baseline, "baseline")
        )

    # TEST 2 — equal coverage, so compare cost. Strict `<` means a tie falls
    # through to the solver: its plan respects constraints the baseline never
    # modelled (time windows), so it is the safer default when nothing separates
    # them on cost.
    if _plan_cost(baseline) < _plan_cost(optimized):
        return baseline, "baseline"
    return optimized, "solver"


def _comparison(baseline: SolveResult, optimized: SolveResult, total_orders: int) -> dict:
    """Build the before/after block stored with the run and shown in the UI.

    Both plans are measured by the same _metrics function, so the percentages
    below are derived from like-for-like figures rather than asserted.
    """
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
    """Flatten a SolvedRoute into JSON-safe primitives for result_payload.

    Written by hand rather than via dataclasses.asdict because the shape
    differs: `coord` is a tuple in the dataclass and is split into separate
    latitude/longitude keys here, which is what the API and the frontend expect.
    """
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

    # The status filters are RULES, not conveniences:
    #   PENDING only   — never re-plan an order already loaded on a van
    #   AVAILABLE only — never dispatch a vehicle that is already out
    # The request's id lists can only NARROW this set, never widen it, so a
    # caller cannot opt out of these constraints by naming ids explicitly.
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

    # Fail early and specifically. 422 rather than 404: the depot exists and the
    # request is well-formed, there is simply nothing to solve.
    if not orders:
        raise APIError("NO_ORDERS", "No pending orders match the request", 422)
    if not vehicles:
        raise APIError("NO_VEHICLES", "No available vehicles match the request", 422)

    return depot, orders, vehicles


async def run_optimization(
    db: AsyncSession, req: OptimizationRequest, time_limit_seconds: int | None = None
) -> OptimizationRun:
    """Solve synchronously — the caller waits for the answer.

    Simple, and fine for small problems. The run row is created BEFORE the solve
    so a failure still leaves an audit record explaining what was attempted,
    rather than vanishing.
    """
    depot, orders, vehicles = await _load_inputs(db, req)
    run = OptimizationRun(
        status=OptimizationStatus.PROCESSING,
        objective=req.objective,
        depot_id=req.depot_id,
        orders_count=len(orders),
        vehicles_count=len(vehicles),
    )
    db.add(run)
    # Commit immediately, so the PROCESSING row is visible to other sessions
    # (and survives a crash) while the solve is under way.
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
    """Do the solve and record the outcome. Shared by the sync and async paths.

    Everything is wrapped so that a failure marks the run FAILED with a reason
    before re-raising — an optimization that blew up should be visible in the
    run log, not just in the server's stderr.
    """
    try:
        # Anchor the planning horizon at the earliest delivery window (or now).
        #
        # The solver works in integer minutes relative to a single origin, so
        # one horizon must be chosen for the whole problem. The earliest window
        # is the natural choice: anchoring later would put some windows in
        # negative time, which the model cannot express.
        window_starts = [o.delivery_window_start for o in orders if o.delivery_window_start]
        horizon = min(window_starts) if window_starts else datetime.now(timezone.utc)

        def to_min(dt: datetime | None) -> int | None:
            """Absolute timestamp -> minutes after the horizon."""
            if dt is None:
                return None  # None means "unconstrained" to the solver
            # Clamped at 0: a window that opened before the horizon is treated
            # as "open now" rather than a negative bound the model would reject.
            return max(0, int((dt - horizon).total_seconds() // 60))

        # Translate ORM rows into the solver's plain dataclasses. This is the
        # boundary that keeps the optimization package database-free.
        order_nodes = [
            OrderNode(
                order_id=o.id,
                order_number=o.order_number,
                coord=(o.latitude, o.longitude),
                demand_kg=o.weight_kg,
                service_time_min=o.service_time_minutes,
                tw_start_min=to_min(o.delivery_window_start),
                tw_end_min=to_min(o.delivery_window_end),
                # The domain method on the enum — 1/2/4/8 — scaling the drop
                # penalty inside the solver.
                priority_weight=o.priority.weight,
            )
            for o in orders
        ]
        vehicle_inputs = [
            VehicleInput(
                vehicle_id=v.id,
                registration_number=v.registration_number,
                capacity_kg=v.capacity_kg,
                # Substitute the default when the column is NULL, so the solver
                # always has a finite range limit to enforce.
                max_route_distance_km=v.max_route_distance_km or 200.0,
            )
            for v in vehicles
        ]
        depot_coord = (depot.latitude, depot.longitude)

        # OR-Tools is CPU-bound; run it off the event loop so the API stays responsive.
        #
        # Without to_thread, the solve would block the single event loop for its
        # entire budget — freezing every other request, including /health.
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

        # Note the comparison is baseline-vs-CHOSEN, not baseline-vs-solver, so
        # the reported improvement describes the plan actually being offered. If
        # the baseline won, the reductions are ~0 rather than negative.
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
            # Stored so accept_plan can turn relative ETAs back into absolute
            # timestamps against the SAME origin the solver used.
            "horizon": horizon.isoformat(),
            # Which heuristic produced the dispatched plan. "baseline" means the
            # solver failed to beat greedy within its time budget — a signal to
            # raise SOLVER_TIME_LIMIT_SECONDS or give the service more CPU.
            "plan_source": plan_source,
            # The solver's own numbers are kept even when the baseline won, so
            # the gap between them is visible after the fact.
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
        # The SOLVER's objective value, even if the baseline was chosen — the
        # baseline has no objective function, so its 0.0 would be misleading.
        run.objective_value = optimized.objective_value
        run.result_payload = payload
    except Exception as exc:  # noqa: BLE001
        # Record the failure durably, then let it propagate. The commit here is
        # essential: without it the rollback would erase the FAILED status too,
        # and the run would be stuck showing PROCESSING forever.
        run.status = OptimizationStatus.FAILED
        run.error_message = str(exc)[:490]  # fits the String(500) column
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
    # clamp(scaled, min, max), written as nested max/min.
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
    #
    # Checked before _load_inputs so a rejected request does no query work.
    if len(_JOBS) >= settings.max_concurrent_optimization_jobs:
        raise APIError(
            "OPTIMIZER_BUSY",
            f"{len(_JOBS)} optimization run(s) already in progress. Wait for one to finish.",
            429,  # Too Many Requests — retryable, unlike a 4xx that never will be
        )

    # Validate synchronously, so a bad request fails now with a clear error
    # rather than in a background task the client cannot see.
    depot, orders, vehicles = await _load_inputs(db, req)
    run = OptimizationRun(
        status=OptimizationStatus.PROCESSING,
        objective=req.objective,
        depot_id=req.depot_id,
        orders_count=len(orders),
        vehicles_count=len(vehicles),
    )
    db.add(run)
    # Commit before spawning, so the background task is guaranteed to find the
    # row when it looks it up in its own session.
    await db.commit()
    await db.refresh(run)

    budget = _budget_for(len(orders))
    # Keep a reference: a bare create_task can be garbage-collected mid-flight.
    #
    # asyncio holds only a weak reference to a running task, so the only thing
    # keeping this alive is membership in _JOBS. The done-callback removes it,
    # which is also what frees a slot for the concurrency cap above.
    task = asyncio.create_task(_run_job(run.id, req, budget))
    _JOBS.add(task)
    task.add_done_callback(_JOBS.discard)

    # Tell every connected client a solve has begun. budget_seconds goes out now
    # so a progress bar knows its scale before the first progress event.
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
    # Start the heartbeat first, so progress is reported even while the inputs
    # are being reloaded.
    heartbeat = asyncio.create_task(_heartbeat(run_id, budget))
    try:
        # A fresh session: the request that created this job has returned, and
        # its session was closed by the get_db dependency.
        async with AsyncSessionLocal() as db:
            run = (
                await db.execute(select(OptimizationRun).where(OptimizationRun.id == run_id))
            ).scalar_one()
            try:
                # Reload the inputs in THIS session. The objects fetched by
                # start_optimization_job belong to a closed session and cannot
                # be used here.
                depot, orders, vehicles = await _load_inputs(db, req)
                run = await _execute_run(db, run, req, depot, orders, vehicles, budget)
            except Exception as exc:  # noqa: BLE001
                # _execute_run already marks FAILED and re-raises; catch so the
                # task never dies silently, and always tell the clients.
                #
                # This matters more in a background task than in a request: an
                # unhandled exception here would be swallowed by asyncio, and the
                # client would poll a PROCESSING row that never resolves.
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
                    # Surfaced to the client so a UI can warn that greedy won.
                    "plan_source": payload.get("plan_source"),
                },
            )
    finally:
        # `finally` so the heartbeat is cancelled on every path — success,
        # failure, or an exception escaping the session block. Otherwise it
        # would keep broadcasting progress for a run that had already finished.
        heartbeat.cancel()


async def _heartbeat(run_id: int, budget: int) -> None:
    """Emit progress while the solver works, so the UI can show real motion."""
    elapsed = 0
    try:
        while True:
            # Sleep FIRST, so no progress event fires at 0% immediately after
            # OPTIMIZATION_STARTED has already said the same thing.
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
                    #
                    # Capped at 99 so it never claims to be finished while the
                    # result is still being written; max(1, ...) avoids dividing
                    # by a zero budget.
                    "progress_pct": min(99, round(elapsed / max(1, budget) * 100)),
                },
            )
    except asyncio.CancelledError:  # pragma: no cover - normal shutdown path
        # Cancellation is how this task always ends, so it is caught and
        # swallowed rather than logged as a failure.
        pass


async def get_run(db: AsyncSession, run_id: int) -> OptimizationRun:
    """One run, or a 404. Used by the polling endpoint and by accept/discard."""
    run = (
        await db.execute(select(OptimizationRun).where(OptimizationRun.id == run_id))
    ).scalar_one_or_none()
    if run is None:
        raise not_found("optimization_run", run_id)
    return run


async def list_runs(db: AsyncSession, limit: int = 50) -> list[OptimizationRun]:
    """Recent runs, newest first — the optimization audit log.

    A hard limit rather than pagination: this is a diagnostic log, and the
    newest 50 answer the question it is asked.
    """
    stmt = select(OptimizationRun).order_by(OptimizationRun.created_at.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def accept_plan(db: AsyncSession, run_id: int) -> list[Route]:
    """Materialise a completed optimization run into active planned routes.

    THE commit point of the whole workflow: until this runs, an optimization has
    changed nothing operationally.

    Replays the stored JSON rather than re-solving. That is deliberate — the
    search is time-bounded, so a re-run could legitimately produce a different
    answer, and a dispatcher must get the plan they approved.
    """
    run = await get_run(db, run_id)
    # Both conditions: a run can be COMPLETED in principle yet have no payload
    # if something went wrong, and accepting that would create empty routes.
    if run.status != OptimizationStatus.COMPLETED or not run.result_payload:
        raise APIError("PLAN_NOT_READY", "Optimization run is not in a completed state", 409)

    # Refuse a second accept.
    #
    # The status check above is not sufficient on its own: accepting does not
    # change the run's status, so a COMPLETED run stays acceptable forever.
    # Without this guard a double-click on Accept dispatches the fleet TWICE —
    # a duplicate route per vehicle, orders re-assigned, and each vehicle's
    # current_load_kg overwritten by the second plan.
    #
    # The routes themselves are the record of having been accepted: every one
    # created below carries this run's id, so their existence is the check. No
    # new column or status is needed.
    already_accepted = (
        await db.execute(
            select(func.count(Route.id)).where(Route.optimization_run_id == run.id)
        )
    ).scalar_one()
    if already_accepted:
        raise APIError(
            "PLAN_ALREADY_ACCEPTED",
            f"This plan was already accepted and created {already_accepted} route(s)",
            409,
        )

    payload = run.result_payload
    # The origin the solver measured from — turns relative ETAs back into
    # absolute timestamps below.
    horizon = datetime.fromisoformat(payload["horizon"])

    # Generate route codes from the current highest id.
    #
    # Same caveat as order numbers: not concurrency-safe. Two simultaneous
    # accepts could derive the same starting point and collide on the UNIQUE
    # route_code. A sequence would be the robust fix.
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
            # PLANNED, not ACTIVE: accepting commits the plan, but the vehicle
            # only starts moving when the simulation picks it up.
            status=RouteStatus.PLANNED,
            total_distance_km=r["total_distance_km"],
            estimated_duration_minutes=r["estimated_duration_minutes"],
            total_load_kg=r["total_load_kg"],
            optimization_score=run.improvement_percentage,
            optimization_run_id=run.id,
            progress_stop_index=-1,  # -1 = still at the depot, nothing done
        )
        db.add(route)
        await db.flush()  # get route.id

        for s in r["stops"]:
            # Relative minutes -> absolute timestamp, against the solver's own
            # horizon so the schedule means what it meant at solve time.
            eta = horizon + timedelta(minutes=s["eta_minutes_from_start"])
            db.add(
                RouteStop(
                    route_id=route.id,
                    order_id=s["order_id"],
                    stop_sequence=s["stop_sequence"],
                    # Coordinates copied from the plan, freezing where the
                    # vehicle was actually sent.
                    latitude=s["latitude"],
                    longitude=s["longitude"],
                    estimated_arrival=eta,
                    distance_from_previous_km=s["distance_from_previous_km"],
                )
            )
            # Take the order out of the planning pool, so a second optimization
            # cannot assign it again. One query per stop — an N+1, but this runs
            # once per accepted plan, not on a hot path.
            order = (await db.execute(select(Order).where(Order.id == s["order_id"]))).scalar_one()
            order.status = OrderStatus.ASSIGNED

        vehicle = (
            await db.execute(select(Vehicle).where(Vehicle.id == r["vehicle_id"]))
        ).scalar_one()
        # ASSIGNED removes it from the AVAILABLE pool the optimizer draws on.
        vehicle.status = VehicleStatus.ASSIGNED
        vehicle.current_load_kg = r["total_load_kg"]
        created.append(route)

    # ONE commit for everything: routes, stops, order statuses and vehicle
    # statuses land together or not at all. A partial commit could leave orders
    # marked ASSIGNED with no route to be on — an inconsistency nothing would
    # later repair.
    await db.commit()
    for route in created:
        await db.refresh(route)
    return created


async def discard_plan(db: AsyncSession, run_id: int) -> None:
    """Reject a plan. Changes no operational state — only the run's own status.

    Note this reuses FAILED for a human decision, with the reason in
    error_message. That conflates "the solver broke" with "we didn't want it";
    a distinct DISCARDED status would be clearer but needs a migration (see the
    note on OptimizationStatus in models/enums.py).

    Silently does nothing if the run was not COMPLETED — discarding an already
    failed run is a no-op rather than an error, so the call is idempotent.
    """
    run = await get_run(db, run_id)
    if run.status == OptimizationStatus.COMPLETED:
        run.status = OptimizationStatus.FAILED
        run.error_message = "Plan discarded by user"
        await db.commit()
