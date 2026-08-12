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
_dispatch = require_roles(UserRole.DISPATCHER)


@router.post("/run", response_model=OptimizationRunOut, status_code=201)
async def run_optimization(
    req: OptimizationRequest, db: AsyncSession = Depends(get_db), _=Depends(_dispatch)
):
    """Run OR-Tools VRP optimization. The heavy solve is offloaded to a worker
    thread; the completed run (with routes, metrics and baseline comparison in
    ``result_payload``) is returned. Nothing is persisted as an active route
    until the plan is accepted."""
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
    return await optimization_service.start_optimization_job(db, req)


@router.get("/runs", response_model=list[OptimizationRunOut])
async def list_runs(db: AsyncSession = Depends(get_db), _=Depends(_dispatch)):
    return await optimization_service.list_runs(db)


@router.get("/runs/{run_id}", response_model=OptimizationRunOut)
async def get_run(run_id: int, db: AsyncSession = Depends(get_db), _=Depends(_dispatch)):
    return await optimization_service.get_run(db, run_id)


@router.post("/runs/{run_id}/accept", response_model=list[RouteOut])
async def accept_plan(run_id: int, db: AsyncSession = Depends(get_db), _=Depends(_dispatch)):
    routes = await optimization_service.accept_plan(db, run_id)
    await cache_invalidate("routeos:dashboard:summary")
    return [await route_service.get_route(db, r.id) for r in routes]


@router.post("/runs/{run_id}/discard", response_model=Message)
async def discard_plan(run_id: int, db: AsyncSession = Depends(get_db), _=Depends(_dispatch)):
    await optimization_service.discard_plan(db, run_id)
    return Message(message="Plan discarded")
