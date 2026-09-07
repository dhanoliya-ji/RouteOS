"""Route endpoints — **read-only**.

There is no POST, PATCH or DELETE here, and that is the design rather than an
omission. A route comes into existence only by accepting an optimization plan
(POST /optimization/runs/{id}/accept), so the absence of a write endpoint is
what makes a route unforgeable through the API.

Correspondingly, app/schemas/route.py defines no RouteCreate.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.db.session import get_db
from app.models.enums import RouteStatus
from app.schemas.route import RouteOut
from app.services import route_service

router = APIRouter(prefix="/routes", tags=["routes"])
# No _manage alias: nothing here writes, so being signed in is enough.


@router.get("", response_model=list[RouteOut])
async def list_routes(
    status: RouteStatus | None = None,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    """All routes, newest first, optionally filtered by status.

    `status=ACTIVE` is what the live-operations board uses. The service
    eager-loads each route's stops, so every item arrives complete — which is
    why this is unpaginated: the response is bounded by the number of routes,
    but each carries its full stop list.
    """
    return await route_service.list_routes(db, status=status)


@router.get("/{route_id}", response_model=RouteOut)
async def get_route(route_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    """One route with its ordered stops.

    Stops arrive sorted by stop_sequence — guaranteed by the relationship's
    order_by in the model, not by the client sorting them.
    """
    return await route_service.get_route(db, route_id)
