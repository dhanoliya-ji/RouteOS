"""Route reads.

**No create, update or delete.** Routes are written in exactly one place —
optimization_service.accept_plan — and mutated by exactly one other, the
simulation engine. This module only reads them.

Both functions exist mainly to get the eager loading right.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import not_found
from app.models.enums import RouteStatus
from app.models.route import Route


async def list_routes(db: AsyncSession, *, status: RouteStatus | None = None) -> list[Route]:
    """Routes, newest first, optionally filtered by status.

    `status=ACTIVE` is what the live-operations board asks for.

    The selectinload is not optional. RouteOut includes `stops`, and without
    eager loading, serialising each route would trigger a lazy load per route —
    which under asyncio does not silently re-query but raises MissingGreenlet.
    So this would fail, not merely be slow.

    selectinload issues one extra query with `WHERE route_id IN (...)` for all
    the routes at once. That is the N+1 fix: two queries total rather than one
    per route. (A joinedload would instead be a single LEFT JOIN, but it
    duplicates the parent row per stop, which for a 30-stop route means
    30 copies of the route's columns over the wire.)
    """
    stmt = select(Route).options(selectinload(Route.stops)).order_by(Route.created_at.desc())
    if status is not None:
        stmt = stmt.where(Route.status == status)
    return list((await db.execute(stmt)).scalars().all())


async def get_route(db: AsyncSession, route_id: int) -> Route:
    """One route with its stops, or a 404.

    Also called by the optimization accept endpoint: accept_plan creates Route
    rows but leaves their `stops` relationship unloaded, so each is re-read
    through here to build a complete response.

    Stops come back ordered by stop_sequence — guaranteed by the relationship's
    `order_by` in models/route.py, so no caller has to sort them.
    """
    route = (
        await db.execute(
            select(Route).options(selectinload(Route.stops)).where(Route.id == route_id)
        )
    ).scalar_one_or_none()
    if route is None:
        raise not_found("route", route_id)
    return route
