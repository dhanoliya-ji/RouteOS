"""Liveness / readiness endpoint.

The one route module with no service layer and no auth. Both are deliberate:
a health check reports on live objects, and a load balancer probing it has no
token to present.

Mounted WITHOUT the /api/v1 prefix (see main.py), so the probe URL does not move
when the API version does.
"""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.core.redis import get_redis
from app.db.session import engine
from app.simulation.engine import engine as sim_engine
from app.websocket.manager import manager

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    """Report whether our two dependencies are reachable.

    Always returns HTTP 200, even when degraded — the body carries the verdict.
    That is a choice: a 5xx here would make a load balancer pull the instance
    out of rotation, when an instance with a broken Redis can still serve most
    traffic correctly (the cache degrades to recompute). A caller that wants
    strict readiness should test the `status` field.
    """
    db_ok = False
    redis_ok = False

    # Each check is isolated in its own try, so a broken Postgres does not
    # prevent us reporting on Redis — the point of the endpoint is to say which
    # dependency is at fault.
    try:
        # `SELECT 1` is the cheapest possible round trip: it proves the
        # connection is live and authenticated without touching a table.
        # Uses engine.connect() rather than a session because no ORM,
        # transaction or identity map is wanted here.
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False

    try:
        redis_ok = bool(await get_redis().ping())
    except Exception:  # noqa: BLE001
        redis_ok = False

    status = "ok" if db_ok and redis_ok else "degraded"
    return {
        "status": status,
        "database": "up" if db_ok else "down",
        "redis": "up" if redis_ok else "down",
        # Read straight off the in-process singletons. This is the one place
        # the API layer legitimately reaches past services (see app/README.md):
        # routing an attribute read through a service would add a file that
        # does nothing but forward it.
        "websocket_connections": manager.count,
        "simulation_running": sim_engine.running,
    }
