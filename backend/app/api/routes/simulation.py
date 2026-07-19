from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import require_roles
from app.db.session import get_db
from app.models.enums import UserRole
from app.services import simulation_service

router = APIRouter(prefix="/simulation", tags=["simulation"])
_dispatch = require_roles(UserRole.DISPATCHER)


class SpeedBody(BaseModel):
    speed_multiplier: float = 1.0


class TrafficBody(BaseModel):
    route_id: int
    severity: str  # clear | moderate | severe | breakdown


@router.get("/status")
async def status(_=Depends(_dispatch)):
    return simulation_service.simulation_status()


@router.post("/start")
async def start(body: SpeedBody, _=Depends(_dispatch)):
    return await simulation_service.start_simulation(body.speed_multiplier)


@router.post("/stop")
async def stop(_=Depends(_dispatch)):
    return await simulation_service.stop_simulation()


@router.post("/speed")
async def speed(body: SpeedBody, _=Depends(_dispatch)):
    return simulation_service.set_speed(body.speed_multiplier)


@router.post("/traffic")
async def traffic(body: TrafficBody, db: AsyncSession = Depends(get_db), _=Depends(_dispatch)):
    return await simulation_service.apply_traffic(db, body.route_id, body.severity)


@router.post("/routes/{route_id}/reoptimize")
async def reoptimize(route_id: int, db: AsyncSession = Depends(get_db), _=Depends(_dispatch)):
    return await simulation_service.reoptimize_route(db, route_id)
