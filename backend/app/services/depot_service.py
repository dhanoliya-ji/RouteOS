"""Depot business logic.

The simplest service: plain CRUD with one responsibility beyond it — keeping
the PostGIS `location` column in step with the plain lat/lng pair.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import not_found
from app.geospatial.queries import make_point
from app.models.depot import Depot
from app.schemas.depot import DepotCreate, DepotUpdate


async def list_depots(db: AsyncSession) -> list[Depot]:
    """Every depot. No filters, no pagination — there are only ever a handful.

    Ordered by id so the list is stable across calls.
    """
    return list((await db.execute(select(Depot).order_by(Depot.id))).scalars().all())


async def get_depot(db: AsyncSession, depot_id: int) -> Depot:
    """Fetch one depot, or raise a 404.

    Also used by optimization_service._load_inputs, which is why a missing
    depot surfaces as a clean 404 from an optimization request too.
    """
    depot = (await db.execute(select(Depot).where(Depot.id == depot_id))).scalar_one_or_none()
    if depot is None:
        raise not_found("depot", depot_id)
    return depot


async def create_depot(db: AsyncSession, data: DepotCreate) -> Depot:
    """Create a depot and its geography point."""
    depot = Depot(
        **data.model_dump(),
        # The one thing this service adds over a bare insert. A depot's point
        # matters more than any single order's: it is node 0 of every
        # optimization run from this hub.
        location=make_point(data.latitude, data.longitude),
    )
    db.add(depot)
    await db.commit()
    await db.refresh(depot)
    return depot


async def update_depot(db: AsyncSession, depot_id: int, data: DepotUpdate) -> Depot:
    """Partially update a depot, rebuilding its point if it moved.

    No status guard — a depot has no lifecycle. Note that moving a depot does
    NOT re-plan the routes already running from it: their stops hold copied
    coordinates and their distances were computed against the old position.
    """
    depot = await get_depot(db, depot_id)

    # exclude_unset: only the fields the client actually sent, so an omitted
    # field is left alone rather than reset to a default.
    payload = data.model_dump(exclude_unset=True)
    for key, value in payload.items():
        setattr(depot, key, value)

    # Same pattern as order_service.update_order: test the payload KEYS, not the
    # values, so "sent a new latitude" is distinguished from "not mentioned".
    # Reads back from `depot` so sending one coordinate combines correctly with
    # the existing other one.
    if "latitude" in payload or "longitude" in payload:
        depot.location = make_point(depot.latitude, depot.longitude)

    await db.commit()
    await db.refresh(depot)
    return depot


async def delete_depot(db: AsyncSession, depot_id: int) -> None:
    """Delete a depot. **The most destructive operation in the system.**

    Every foreign key pointing at depots is ON DELETE CASCADE, so this removes
    the depot's vehicles, its orders and its routes — and, transitively, those
    routes' stops and those vehicles' location history.

    There is no guard here and no soft-delete. The protection is entirely at the
    endpoint, which requires ADMIN. Worth knowing before calling this function
    from anywhere new.
    """
    depot = await get_depot(db, depot_id)
    await db.delete(depot)
    await db.commit()
