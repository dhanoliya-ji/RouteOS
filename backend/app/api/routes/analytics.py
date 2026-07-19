from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.db.session import get_db
from app.services import analytics_service

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/summary")
async def summary(
    start: date | None = None,
    end: date | None = None,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    return await analytics_service.summary_metrics(db, start, end)


@router.get("/orders-by-status")
async def orders_by_status(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    return await analytics_service.orders_by_status(db)


@router.get("/distance-by-vehicle")
async def distance_by_vehicle(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    return await analytics_service.distance_by_vehicle(db)


@router.get("/deliveries-over-time")
async def deliveries_over_time(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    return await analytics_service.deliveries_over_time(db)


@router.get("/optimization-savings")
async def optimization_savings(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    return await analytics_service.optimization_savings(db)
