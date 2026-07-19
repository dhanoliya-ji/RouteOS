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
    db_ok = False
    redis_ok = False
    try:
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
        "websocket_connections": manager.count,
        "simulation_running": sim_engine.running,
    }
