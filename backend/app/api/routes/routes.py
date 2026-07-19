from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.db.session import get_db
from app.models.enums import RouteStatus
from app.schemas.route import RouteOut
from app.services import route_service

router = APIRouter(prefix="/routes", tags=["routes"])


@router.get("", response_model=list[RouteOut])
async def list_routes(
    status: RouteStatus | None = None,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    return await route_service.list_routes(db, status=status)


@router.get("/{route_id}", response_model=RouteOut)
async def get_route(route_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    return await route_service.get_route(db, route_id)
