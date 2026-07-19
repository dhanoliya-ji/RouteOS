from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import not_found
from app.models.enums import RouteStatus
from app.models.route import Route


async def list_routes(db: AsyncSession, *, status: RouteStatus | None = None) -> list[Route]:
    stmt = select(Route).options(selectinload(Route.stops)).order_by(Route.created_at.desc())
    if status is not None:
        stmt = stmt.where(Route.status == status)
    return list((await db.execute(stmt)).scalars().all())


async def get_route(db: AsyncSession, route_id: int) -> Route:
    route = (
        await db.execute(
            select(Route).options(selectinload(Route.stops)).where(Route.id == route_id)
        )
    ).scalar_one_or_none()
    if route is None:
        raise not_found("route", route_id)
    return route
