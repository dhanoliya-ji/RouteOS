"""Vehicle (fleet) endpoints.

Same shape as orders.py — see that module for the annotated pattern. Two
differences worth noting: there is no pagination (a fleet is tens or hundreds,
not thousands), and DELETE is admin-only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user, require_roles
from app.db.session import get_db
from app.models.enums import UserRole, VehicleStatus
from app.schemas.common import Message
from app.schemas.vehicle import NearbyVehicle, VehicleCreate, VehicleOut, VehicleUpdate
from app.services import fleet_service

router = APIRouter(prefix="/vehicles", tags=["vehicles"])
_manage = require_roles(UserRole.DISPATCHER)


@router.get("", response_model=list[VehicleOut])
async def list_vehicles(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
    status: VehicleStatus | None = None,
    depot_id: int | None = None,
):
    """The fleet, optionally filtered.

    Returns a plain list, not a Page: a fleet is small and bounded, and the
    UI's map and fleet board both want all of it at once.
    """
    return await fleet_service.list_vehicles(db, status=status, depot_id=depot_id)


# Literal path before the parameterised one — see the note in orders.py.
@router.get("/nearby", response_model=list[NearbyVehicle])
async def vehicles_nearby(
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    # A wider default than orders (10 km vs 5): vehicles are far sparser than
    # deliveries, so a tight radius usually finds nothing.
    radius_km: float = Query(10.0, gt=0, le=500),
    # The dispatcher's real question is normally "who can take this job?",
    # which means AVAILABLE only.
    only_available: bool = False,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    """Vehicles within a radius, nearest first.

    Unlike /orders/nearby this cannot use a spatial index — vehicles have no
    geography column, so the service builds a point inside the query and
    Postgres scans. Acceptable at fleet scale; see app/geospatial/README.md.
    """
    rows = await fleet_service.vehicles_nearby(
        db, latitude, longitude, radius_km, only_available=only_available
    )
    return [
        NearbyVehicle(**VehicleOut.model_validate(v).model_dump(), distance_km=round(d, 3))
        for v, d in rows
    ]


@router.get("/{vehicle_id}", response_model=VehicleOut)
async def get_vehicle(vehicle_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    return await fleet_service.get_vehicle(db, vehicle_id)


@router.post("", response_model=VehicleOut, status_code=201)
async def create_vehicle(data: VehicleCreate, db: AsyncSession = Depends(get_db), _=Depends(_manage)):
    return await fleet_service.create_vehicle(db, data)


@router.patch("/{vehicle_id}", response_model=VehicleOut)
async def update_vehicle(
    vehicle_id: int, data: VehicleUpdate, db: AsyncSession = Depends(get_db), _=Depends(_manage)
):
    """Partial update.

    This is also how a vehicle is taken out of service: setting status to
    MAINTENANCE or OFFLINE excludes it from planning, since the optimizer only
    considers AVAILABLE vehicles.
    """
    return await fleet_service.update_vehicle(db, vehicle_id, data)


@router.delete("/{vehicle_id}", response_model=Message)
async def delete_vehicle(vehicle_id: int, db: AsyncSession = Depends(get_db), _=Depends(require_roles(UserRole.ADMIN))):
    """Remove a vehicle. **Admin only.**

    Guards inline with require_roles(ADMIN) rather than the module's _manage,
    so the elevated requirement is visible right here rather than having to be
    inferred from an alias defined at the top of the file.

    Admin-level because a vehicle is infrastructure, not day-to-day data. Its
    routes survive the deletion with vehicle_id set to NULL, so history is
    preserved — but the fleet record itself is gone.
    """
    await fleet_service.delete_vehicle(db, vehicle_id)
    return Message(message="Vehicle deleted")
