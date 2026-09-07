"""Optimization endpoints — solve, review, then accept or discard.

This module exposes a **two-phase workflow**, which is the part worth
understanding:

    1. SOLVE     produces a plan and stores it. Changes nothing operationally.
    2. REVIEW    a human looks at the plan, its baseline comparison and the
                 orders it could not serve.
    3. DECIDE    accept -> the plan becomes real routes
                 discard -> it is thrown away

Nothing is dispatched without step 3. That is why solving and accepting are
separate endpoints rather than one call.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import require_roles
from app.core.redis import cache_invalidate
from app.db.session import get_db
from app.models.enums import UserRole
from app.schemas.common import Message
from app.schemas.optimization import (
    OptimizationRequest,
    OptimizationRunOut,
)
from app.schemas.route import RouteOut
from app.services import optimization_service, route_service

router = APIRouter(prefix="/optimization", tags=["optimization"])
# Every endpoint here is dispatcher-level: this is the module that commits the
# fleet to work.
_dispatch = require_roles(UserRole.DISPATCHER)


@router.post("/run", response_model=OptimizationRunOut, status_code=201)
async def run_optimization(
    req: OptimizationRequest, db: AsyncSession = Depends(get_db), _=Depends(_dispatch)
):
    """Run OR-Tools VRP optimization. The heavy solve is offloaded to a worker
    thread; the completed run (with routes, metrics and baseline comparison in
    ``result_payload``) is returned. Nothing is persisted as an active route
    until the plan is accepted."""
    # Synchronous: the client waits for the solve. Simple, and fine for small
    # problems — but bounded by SOLVER_TIME_LIMIT_SECONDS (15s) because an HTTP
    # request cannot be held open for the minutes a large solve wants. Use
    # /jobs below for anything substantial.
    run = await optimization_service.run_optimization(db, req)
    return run


@router.post("/jobs", response_model=OptimizationRunOut, status_code=202)
async def start_optimization(
    req: OptimizationRequest, db: AsyncSession = Depends(get_db), _=Depends(_dispatch)
):
    """Queue an optimization and return the PROCESSING run immediately.

    The solve happens in the background, so its time budget is not capped by how
    long a client can hold an HTTP connection open. Poll ``GET /runs/{id}`` or
    subscribe to the ``OPTIMIZATION_*`` WebSocket events for progress.
    """
    # 202 Accepted, not 201: the work has been accepted but is not finished, and
    # the body describes a run in progress rather than a completed resource.
    # May return 429 if too many solves are already running — each pins a CPU.
    return await optimization_service.start_optimization_job(db, req)


@router.get("/runs", response_model=list[OptimizationRunOut])
async def list_runs(db: AsyncSession = Depends(get_db), _=Depends(_dispatch)):
    """Run history, newest first — the optimization audit log."""
    return await optimization_service.list_runs(db)


@router.get("/runs/{run_id}", response_model=OptimizationRunOut)
async def get_run(run_id: int, db: AsyncSession = Depends(get_db), _=Depends(_dispatch)):
    """One run, including its full plan in `result_payload`.

    This is both the progress endpoint (poll while status is PROCESSING) and
    the review endpoint (read the plan once COMPLETED).
    """
    return await optimization_service.get_run(db, run_id)


@router.post("/runs/{run_id}/accept", response_model=list[RouteOut])
async def accept_plan(run_id: int, db: AsyncSession = Depends(get_db), _=Depends(_dispatch)):
    """Turn a stored plan into live PLANNED routes.

    The single moment the system commits to a plan: routes and stops are
    created, orders become ASSIGNED and vehicles become ASSIGNED — all in one
    transaction. Replays the saved JSON rather than re-solving, so the plan
    approved is exactly the plan dispatched.
    """
    routes = await optimization_service.accept_plan(db, run_id)

    # Drop the cached dashboard summary. Accepting changes the KPIs this
    # instant, and the cache has a 15s TTL — long enough that a dispatcher
    # would click Accept and then see stale numbers.
    #
    # Done here rather than in the service because it is a presentation
    # concern: the service's job is to make the plan real, not to know which
    # caches a UI keeps.
    await cache_invalidate("routeos:dashboard:summary")

    # Re-read each route through route_service so the response includes its
    # eager-loaded stops. accept_plan returns the Route rows it created, but
    # their `stops` relationship is not loaded, and serialising RouteOut
    # straight from them would trigger a lazy load and raise MissingGreenlet.
    return [await route_service.get_route(db, r.id) for r in routes]


@router.post("/runs/{run_id}/discard", response_model=Message)
async def discard_plan(run_id: int, db: AsyncSession = Depends(get_db), _=Depends(_dispatch)):
    """Reject a plan.

    No cache invalidation needed: discarding changes no operational state, only
    the run's own status. (The service records this as FAILED with an
    explanatory message — see the note on OptimizationStatus in models/enums.py
    about that conflation.)
    """
    await optimization_service.discard_plan(db, run_id)
    return Message(message="Plan discarded")
