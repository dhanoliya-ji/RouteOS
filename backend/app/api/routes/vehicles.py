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
    return await fleet_service.list_vehicles(db, status=status, depot_id=depot_id)


@router.get("/nearby", response_model=list[NearbyVehicle])
async def vehicles_nearby(
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    radius_km: float = Query(10.0, gt=0, le=500),
    only_available: bool = False,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
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
    return await fleet_service.update_vehicle(db, vehicle_id, data)


@router.delete("/{vehicle_id}", response_model=Message)
async def delete_vehicle(vehicle_id: int, db: AsyncSession = Depends(get_db), _=Depends(require_roles(UserRole.ADMIN))):
    await fleet_service.delete_vehicle(db, vehicle_id)
    return Message(message="Vehicle deleted")
