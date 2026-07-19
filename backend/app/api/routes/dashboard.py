from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.db.session import get_db
from app.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary")
async def summary(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    return await dashboard_service.get_summary(db)


@router.get("/activity")
async def activity(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    return await dashboard_service.recent_activity(db)
