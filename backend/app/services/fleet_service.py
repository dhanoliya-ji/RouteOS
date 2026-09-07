"""Vehicle (fleet) business logic.

Thinner than order_service: a vehicle has no lifecycle rules of its own — it is
reference data that the optimizer and simulation read. The interesting function
is `vehicles_nearby`, which has to work without a geography column.
"""
from __future__ import annotations

from geoalchemy2 import Geography
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import not_found
from app.models.enums import VehicleStatus
from app.models.vehicle import Vehicle
from app.schemas.vehicle import VehicleCreate, VehicleUpdate


async def list_vehicles(
    db: AsyncSession, *, status: VehicleStatus | None = None, depot_id: int | None = None
) -> list[Vehicle]:
    """The fleet, optionally filtered by status and/or home depot.

    Unpaginated by design — a fleet is tens or hundreds of rows, and both the
    map and the fleet board want all of them at once.

    Ordered by id so the list is stable between calls; an unordered SELECT may
    return rows in any order, which makes a UI list appear to shuffle.
    """
    stmt = select(Vehicle).order_by(Vehicle.id)
    if status is not None:
        stmt = stmt.where(Vehicle.status == status)
    if depot_id is not None:
        stmt = stmt.where(Vehicle.home_depot_id == depot_id)
    return list((await db.execute(stmt)).scalars().all())


async def get_vehicle(db: AsyncSession, vehicle_id: int) -> Vehicle:
    """Fetch one vehicle, or raise a 404. Used by the update/delete paths too."""
    v = (await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id))).scalar_one_or_none()
    if v is None:
        raise not_found("vehicle", vehicle_id)
    return v


async def create_vehicle(db: AsyncSession, data: VehicleCreate) -> Vehicle:
    """Add a vehicle.

    No geography point to build, unlike orders and depots — vehicles store
    plain coordinates only (see the note in models/vehicle.py), so the schema's
    fields map straight onto the model.
    """
    v = Vehicle(**data.model_dump())
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


async def update_vehicle(db: AsyncSession, vehicle_id: int, data: VehicleUpdate) -> Vehicle:
    """Partially update a vehicle.

    No status guard here, unlike orders: any field may change at any time. That
    is intentional — this is how a vehicle is pulled out of service mid-day by
    setting MAINTENANCE or OFFLINE, which excludes it from future planning
    (the optimizer only considers AVAILABLE vehicles).

    Note the consequence: changing `capacity_kg` while the vehicle is out on a
    route does not re-validate the route it is already running. The plan was
    computed against the old capacity and is not revisited.
    """
    v = await get_vehicle(db, vehicle_id)
    # exclude_unset: only the fields the client actually sent.
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(v, key, value)
    await db.commit()
    await db.refresh(v)
    return v


async def delete_vehicle(db: AsyncSession, vehicle_id: int) -> None:
    """Remove a vehicle permanently. (Admin-only at the endpoint.)

    No status guard, so a vehicle can be deleted mid-route. The database
    handles the consequence rather than this code: routes.vehicle_id is
    ON DELETE SET NULL, so the route survives with no vehicle attached and the
    delivery history is preserved. Its location history, being meaningless
    without the vehicle, cascades away.
    """
    v = await get_vehicle(db, vehicle_id)
    await db.delete(v)
    await db.commit()


async def vehicles_nearby(
    db: AsyncSession,
    latitude: float,
    longitude: float,
    radius_km: float,
    *,
    only_available: bool = False,
    limit: int = 50,
) -> list[tuple[Vehicle, float]]:
    """Vehicles within a radius, nearest first. Returns (vehicle, km) pairs.

    Structurally the same as orders_nearby, but it has to build its geometry
    inside the query — which is the whole reason this function is longer.
    """
    # Vehicles keep plain lat/lng; build a geography point on the fly for PostGIS.
    #
    # WHY there is no stored column to use: a vehicle's position is rewritten
    # every second by the simulation, and maintaining a geography column plus
    # its GiST index on every tick would cost more in writes than the index
    # saves on reads.
    #
    # THE TRADE-OFF, stated plainly: a computed expression cannot use a spatial
    # index, so this is a full table scan. Fine for a fleet of tens or hundreds;
    # at millions of vehicles the calculus reverses and they would want a real
    # geography column.
    #
    # The cast to Geography is required: ST_MakePoint alone yields a *geometry*,
    # whose ST_Distance answers in degrees. Casting makes the answer metres.
    veh_point = func.cast(
        # lon, lat — PostGIS argument order, not the human one.
        func.ST_SetSRID(func.ST_MakePoint(Vehicle.current_longitude, Vehicle.current_latitude), 4326),
        Geography(),
    )
    target = func.cast(func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326), Geography())
    dist = func.ST_Distance(veh_point, target)

    stmt = (
        select(Vehicle, dist.label("dist_m"))
        # Skip vehicles that have never reported a position: ST_MakePoint with
        # a NULL argument returns NULL, and every comparison against it would
        # be NULL (i.e. false) — so this is really about not scanning rows that
        # cannot match.
        .where(Vehicle.current_latitude.is_not(None))
        # ST_DWithin still expresses intent and lets Postgres discard rows early
        # even without an index to accelerate it.
        .where(func.ST_DWithin(veh_point, target, radius_km * 1000.0))
        .order_by(dist)
        .limit(limit)
    )
    if only_available:
        # The dispatcher's real question is usually "who can take this job?",
        # which means AVAILABLE only.
        stmt = stmt.where(Vehicle.status == VehicleStatus.AVAILABLE)

    rows = (await db.execute(stmt)).all()
    # Metres to kilometres, so the API speaks one unit throughout.
    return [(row[0], row[1] / 1000.0) for row in rows]
