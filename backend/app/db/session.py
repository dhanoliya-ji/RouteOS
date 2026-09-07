"""Async SQLAlchemy engine & session factory.

Three things live here, in increasing order of how often you touch them:

    engine             the connection pool. Created once per process.
    AsyncSessionLocal  makes sessions. Used directly by background work.
    get_db()           the FastAPI dependency. Used by every endpoint.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# The connection pool, created once at import time and shared by everything.
#
# Opening a TCP connection to Postgres and authenticating costs milliseconds —
# far too much to pay per request — so the engine keeps connections open and
# lends them out. Creating an engine per request would defeat the entire point.
engine = create_async_engine(
    settings.database_url,
    # Statement logging off; core/logging.py also pins sqlalchemy.engine to
    # WARNING. At INFO every query is logged, which buries everything else.
    echo=False,
    # Send a cheap ping before lending out a pooled connection. A connection
    # can die while idle — Postgres restarting, a firewall or load balancer
    # dropping a long-lived socket — and without this the request that borrows
    # it fails with a confusing "connection closed". Costs one round-trip and
    # turns a mystery error into a transparent reconnect.
    pool_pre_ping=True,
    # 10 kept open permanently, plus up to 20 more under load which are
    # discarded when traffic subsides. Hard ceiling: 30 concurrent connections.
    # Worth keeping in mind against Postgres's own max_connections, especially
    # on small managed instances where that limit can be as low as ~20.
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    # THE load-bearing setting in this file.
    #
    # By default SQLAlchemy marks every attribute stale after a commit, so the
    # next attribute access silently re-queries to refresh it. Under asyncio
    # that implicit IO cannot happen inside normal attribute access, so it
    # raises MissingGreenlet instead — and it breaks the most ordinary pattern
    # there is:
    #
    #     await db.commit()
    #     return order          # serialising order.id would re-query -> boom
    #
    # Turning it off means committed objects keep their loaded values and stay
    # usable afterwards.
    #
    # Caveat: this fixes already-loaded COLUMNS, not unloaded RELATIONSHIPS.
    # Touching `route.stops` when it was never loaded is still a lazy load and
    # still raises, which is why queries use selectinload(). If you hit
    # MissingGreenlet, a missing selectinload is the first thing to check.
    expire_on_commit=False,
    # Don't flush pending changes before every query. Flushes then happen only
    # where the code asks for one (an explicit db.flush(), or a commit), which
    # makes the moment SQL is emitted predictable rather than incidental.
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a database session.

    One session per request. FastAPI runs the generator up to the `yield` before
    the handler, then resumes it afterwards — so the `async with` block below
    brackets the entire request and always returns the connection to the pool,
    including when the handler raised.

    The rollback matters for pooling, not just correctness: a connection handed
    back mid-transaction would carry that open transaction to whoever borrows it
    next. Rolling back leaves it clean.

    Note there is no `commit()` here. Services commit explicitly, because only
    they know where a unit of work actually ends — a service that writes several
    tables (accept_plan) needs them in one transaction, not one per statement.

    Background work cannot use this dependency (there is no request to attach
    to) and opens its own session with `AsyncSessionLocal()` instead — see
    simulation/engine.py and the optimization job runner.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise  # re-raise so the error handlers can shape the response
