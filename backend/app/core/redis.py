"""Redis client + small JSON cache helpers.

Redis is used for three things in RouteOS:
  1. Caching the dashboard summary & analytics (expensive SQL aggregations).
  2. Holding live vehicle state during a simulation (hot, frequently written).
  3. Pub/sub-style fan-out of simulation events to WebSocket clients.

Caching is deliberately selective — only computed, reusable, read-heavy payloads
are cached, each with a short TTL and explicit invalidation on writes.
"""
from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

from app.core.config import settings

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
    return _redis


async def cache_get_json(key: str) -> Any | None:
    try:
        raw = await get_redis().get(key)
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001 - cache must never break the request path
        return None


async def cache_set_json(key: str, value: Any, ttl_seconds: int = 30) -> None:
    try:
        await get_redis().set(key, json.dumps(value, default=str), ex=ttl_seconds)
    except Exception:  # noqa: BLE001
        pass


async def cache_invalidate(*keys: str) -> None:
    try:
        if keys:
            await get_redis().delete(*keys)
    except Exception:  # noqa: BLE001
        pass


async def cache_invalidate_prefix(prefix: str) -> None:
    try:
        r = get_redis()
        async for key in r.scan_iter(match=f"{prefix}*"):
            await r.delete(key)
    except Exception:  # noqa: BLE001
        pass
