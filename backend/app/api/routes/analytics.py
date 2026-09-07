"""Analytics endpoints — read-only aggregations behind the charts.

All five are GETs available to any signed-in user, including VIEWER: these
report on the past and change nothing.

Each delegates to a single SQL aggregation. The work happens in Postgres via
GROUP BY / SUM / AVG rather than by fetching rows and counting in Python — see
app/services/README.md.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.db.session import get_db
from app.services import analytics_service

router = APIRouter(prefix="/analytics", tags=["analytics"])
# No role alias — nothing here writes, so being signed in is sufficient.


@router.get("/summary")
async def summary(
    start: date | None = None,
    end: date | None = None,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    """The KPI panel: utilisation, averages per route and per delivery,
    unassigned rate, and mean optimizer improvement.

    Both bounds are optional and independent — passing only `start` means
    "since then". This is the only analytics endpoint that is cached (30s, keyed
    by the date range), because it runs the most queries by far.
    """
    return await analytics_service.summary_metrics(db, start, end)


@router.get("/orders-by-status")
async def orders_by_status(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    """Order counts per status — one GROUP BY, for the pie chart.

    Uncached, like the three below: a single indexed GROUP BY is cheap enough
    that caching would add invalidation complexity for little gain.
    """
    return await analytics_service.orders_by_status(db)


@router.get("/distance-by-vehicle")
async def distance_by_vehicle(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    """Distance driven per vehicle, busiest first.

    An outer join, so a vehicle that has never been routed still appears with
    zero rather than vanishing from the chart.
    """
    return await analytics_service.distance_by_vehicle(db)


@router.get("/deliveries-over-time")
async def deliveries_over_time(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    """Daily totals and delivered counts for the trend line.

    Postgres does the day-bucketing (date_trunc) and the conditional count
    (CASE) in one pass, and the service reverses the rows so the series reads
    oldest-first for plotting.
    """
    return await analytics_service.deliveries_over_time(db)


@router.get("/optimization-savings")
async def optimization_savings(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    """Before/after distance per optimization run.

    Reads the stored baseline comparison from each run, so the savings shown
    are the measurements taken at solve time — not recomputed from current data,
    which could no longer reflect the fleet as it was.
    """
    return await analytics_service.optimization_savings(db)
