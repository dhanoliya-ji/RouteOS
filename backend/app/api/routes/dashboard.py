"""Dashboard endpoints — the landing page's tiles and activity feed.

Distinct from analytics/: this is "what is happening right now" rather than
"what happened over a period", which is why the summary here has a shorter cache
TTL (15s vs 30s) and takes no date range.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.db.session import get_db
from app.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary")
async def summary(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    """The KPI tiles: pending, out for delivery, delivered today, fleet state,
    distance today, active routes.

    Redis-cached for 15 seconds because it runs about nine COUNT/SUM queries
    and is hit on every page load. Accepting an optimization plan explicitly
    invalidates this key (see optimization.py), so a visible action is never
    followed by stale numbers.
    """
    return await dashboard_service.get_summary(db)


@router.get("/activity")
async def activity(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    """The most recent delivery events, newest first.

    Uncached: it is one indexed query on delivery_events, and this is the panel
    a user most expects to be live.
    """
    return await dashboard_service.recent_activity(db)
