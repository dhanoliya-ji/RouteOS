"""Redis client + small JSON cache helpers.

Redis is used for three things in RouteOS:
  1. Caching the dashboard summary & analytics (expensive SQL aggregations).
  2. Holding live vehicle state during a simulation (hot, frequently written).
  3. Pub/sub-style fan-out of simulation events to WebSocket clients.

Caching is deliberately selective — only computed, reusable, read-heavy payloads
are cached, each with a short TTL and explicit invalidation on writes.

The rule this whole module follows: **Redis is an optimisation, never a
dependency.** Every function below swallows its exceptions, so if Redis is
missing, slow or broken the app keeps working — a read degrades to "recompute
from Postgres" and a write degrades to "don't bother". Postgres remains the only
source of truth, so a cache failure can never produce a wrong answer, only a
slower one.
"""
from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

from app.core.config import settings

# The shared client, created on first use rather than at import time.
#
# Lazy because importing this module must not require a reachable Redis — tests
# and CLI scripts import the app without one. `None` until someone actually
# needs it.
_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    """Return the process-wide Redis client, creating it once.

    One client, shared: the library maintains its own connection pool
    internally, so building a client per call would defeat the pooling and leak
    connections.
    """
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            # Hand back `str` instead of `bytes`, so callers can json.loads()
            # directly without decoding at every use site.
            decode_responses=True,
        )
    return _redis


async def cache_get_json(key: str) -> Any | None:
    """Read and parse a cached JSON value. `None` means "not usable".

    Note that a miss, an unreachable Redis and a corrupt value all return the
    same `None`. That is intentional: the caller's response to all three is
    identical — recompute it — so distinguishing them would only add a branch
    nobody would act on.
    """
    try:
        raw = await get_redis().get(key)
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001 - cache must never break the request path
        return None


async def cache_set_json(key: str, value: Any, ttl_seconds: int = 30) -> None:
    """Store a value as JSON with an expiry.

    `ex=ttl_seconds` is what makes the cache self-correcting: every entry dies
    on its own, so a bug in invalidation causes staleness measured in seconds
    rather than forever. A cache with no TTL is a liability.

    `default=str` so values Python cannot natively serialise — datetimes,
    Decimals, enums — degrade to their string form instead of raising. These
    payloads are already API-shaped, so string is the right fallback.
    """
    try:
        await get_redis().set(key, json.dumps(value, default=str), ex=ttl_seconds)
    except Exception:  # noqa: BLE001
        pass


async def cache_invalidate(*keys: str) -> None:
    """Drop specific keys, for when a write makes them wrong immediately.

    Used after accepting an optimization plan: the dashboard KPIs change at
    that instant, and waiting out the TTL would show a dispatcher stale numbers
    right after a visible action.

    The `if keys` guard avoids calling DELETE with no arguments, which Redis
    rejects as a syntax error.
    """
    try:
        if keys:
            await get_redis().delete(*keys)
    except Exception:  # noqa: BLE001
        pass


async def cache_invalidate_prefix(prefix: str) -> None:
    """Drop every key under a prefix.

    Needed where the key embeds parameters and so is not a single known string
    — the analytics keys include their date range
    ("routeos:analytics:summary:2026-01-01:2026-01-31"), so one write can
    invalidate an unbounded number of cached variants.

    Uses `scan_iter`, not `keys`. `KEYS pattern` blocks the entire Redis server
    while it walks the keyspace, which on a shared instance is a real outage;
    `SCAN` returns in small batches and lets other clients interleave.
    """
    try:
        r = get_redis()
        async for key in r.scan_iter(match=f"{prefix}*"):
            await r.delete(key)
    except Exception:  # noqa: BLE001
        pass
