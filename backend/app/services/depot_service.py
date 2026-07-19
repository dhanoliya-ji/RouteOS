from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import not_found
from app.geospatial.queries import make_point
from app.models.depot import Depot
from app.schemas.depot import DepotCreate, DepotUpdate


async def list_depots(db: AsyncSession) -> list[Depot]:
    return list((await db.execute(select(Depot).order_by(Depot.id))).scalars().all())


async def get_depot(db: AsyncSession, depot_id: int) -> Depot:
    depot = (await db.execute(select(Depot).where(Depot.id == depot_id))).scalar_one_or_none()
    if depot is None:
        raise not_found("depot", depot_id)
    return depot


async def create_depot(db: AsyncSession, data: DepotCreate) -> Depot:
    depot = Depot(
        **data.model_dump(),
        location=make_point(data.latitude, data.longitude),
    )
    db.add(depot)
    await db.commit()
    await db.refresh(depot)
    return depot


async def update_depot(db: AsyncSession, depot_id: int, data: DepotUpdate) -> Depot:
    depot = await get_depot(db, depot_id)
    payload = data.model_dump(exclude_unset=True)
    for key, value in payload.items():
        setattr(depot, key, value)
    if "latitude" in payload or "longitude" in payload:
        depot.location = make_point(depot.latitude, depot.longitude)
    await db.commit()
    await db.refresh(depot)
    return depot


async def delete_depot(db: AsyncSession, depot_id: int) -> None:
    depot = await get_depot(db, depot_id)
    await db.delete(depot)
    await db.commit()
