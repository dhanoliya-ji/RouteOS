"""Shared pytest fixtures.

The suite comes in two tiers, and this file is the boundary between them.

**Tier 1 — no database.** The algorithm tests, the security tests and the
permission matrix. They need nothing, run in seconds, and always execute.

**Tier 2 — a real database.** The API and CRUD tests, which need PostGIS: the
app stores `Geography` columns, `JSONB` payloads and calls `date_trunc` and
`ST_DWithin`. SQLite has none of those, so testing on it would mean stubbing out
the very code paths under test. These fixtures therefore look for a real
Postgres and **skip cleanly** when there is not one, so `pytest` still works on
a machine with no Docker.

Start one:

    docker run -d --name routeos-test-db \\
      -e POSTGRES_USER=routeos -e POSTGRES_PASSWORD=routeos_test \\
      -e POSTGRES_DB=routeos_test -p 55432:5432 postgis/postgis:16-3.4

Port 55432 rather than 5432, so it cannot collide with a Postgres you are
already running. Override with TEST_DATABASE_URL if yours lives elsewhere.
"""
from __future__ import annotations

import asyncio
import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# NOTE: no `event_loop` fixture here.
#
# Overriding it is deprecated in pytest-asyncio 0.25 (the pinned version) in
# favour of the asyncio_default_fixture_loop_scope setting in pytest.ini. The
# old override sat in this file harmlessly only because nothing was async; it
# would have started warning the moment an async fixture appeared, which is
# exactly what the Tier 2 fixtures below are.

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://routeos:routeos_test@localhost:55432/routeos_test",
)


def _database_is_reachable(url: str) -> bool:
    """Can we connect at all? Used to decide skip-versus-run.

    Deliberately cheap and side-effect free — it opens a connection, runs
    nothing, and closes. Any failure means "no test database", which is a valid
    state rather than an error.
    """

    async def _probe() -> bool:
        engine = create_async_engine(url, poolclass=None)
        try:
            async with engine.connect():
                return True
        except Exception:
            return False
        finally:
            await engine.dispose()

    try:
        return asyncio.run(_probe())
    except Exception:
        return False


# Evaluated once at import time, so the skip reason is decided before any test
# runs rather than being re-probed per test.
DB_AVAILABLE = _database_is_reachable(TEST_DATABASE_URL)

requires_db = pytest.mark.skipif(
    not DB_AVAILABLE,
    reason=(
        f"no test database at {TEST_DATABASE_URL} — "
        "see the docker run command in tests/conftest.py"
    ),
)


# The schema is built once per pytest process, not once per test. A module flag
# rather than a session-scoped fixture, because asyncpg connections are bound to
# the event loop that created them: a session-scoped async fixture would run on
# a different loop from the function-scoped tests that use it, which
# pytest-asyncio rejects outright as a ScopeMismatch.
_SCHEMA_READY = False


@pytest_asyncio.fixture
async def db_engine():
    """An engine for this test, against a schema created once per process.

    Builds the schema from `Base.metadata` rather than by running Alembic:
    these tests are about application behaviour, and migration correctness is a
    separate concern. PostGIS has to be enabled first, exactly as migration
    0001 does it — a `Geography` column cannot be created without it.

    Creating an engine per test is cheap; it is connections that cost, and each
    test opens exactly one.
    """
    if not DB_AVAILABLE:
        pytest.skip("no test database")

    global _SCHEMA_READY

    from sqlalchemy import text

    from app.db.base import Base
    import app.models  # noqa: F401 — registers every table on Base.metadata

    engine = create_async_engine(TEST_DATABASE_URL)

    if not _SCHEMA_READY:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            # Drop first, so a schema left over from an interrupted run cannot
            # make this one fail in confusing ways.
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        _SCHEMA_READY = True

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture
async def db(db_engine) -> AsyncSession:
    """A session per test, wrapped in a transaction that is always rolled back.

    This is what keeps the tests independent without recreating the schema for
    each one: every test sees a clean database, and nothing it writes survives.

    The session is bound to a live connection with an open transaction, so the
    `commit()` calls inside the services commit to that transaction rather than
    to the database. The outer rollback then discards the lot.
    """
    async with db_engine.connect() as conn:
        outer = await conn.begin()
        session = async_sessionmaker(
            bind=conn, expire_on_commit=False, class_=AsyncSession, autoflush=False
        )()
        try:
            yield session
        finally:
            await session.close()
            # Guarded: a test that provoked a database error (a foreign-key or
            # unique violation) has already had its transaction aborted, so the
            # outer one is gone. Rolling back unconditionally then emits
            # "transaction already deassociated from connection".
            if outer.is_active:
                await outer.rollback()


@pytest_asyncio.fixture
async def client(db):
    """An HTTP client whose requests share the test's transaction.

    Overriding `get_db` is the whole trick: the app's endpoints receive the same
    session the test holds, so data the test creates is visible to the request,
    and data the request creates is visible to the test — then all of it is
    rolled back together.
    """
    import httpx
    from httpx import ASGITransport

    from app.db.session import get_db
    from app.main import app

    async def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def as_dispatcher(client):
    """`client`, signed in as a DISPATCHER.

    Bypasses the login endpoint and overrides `get_current_user` directly.
    Logging in for real would work, but it would make every CRUD test depend on
    the auth flow — so a failure there would fail dozens of unrelated tests.
    Authentication has its own file.
    """
    from app.api.dependencies.auth import get_current_user
    from app.main import app
    from app.models.enums import UserRole
    from app.models.user import User

    app.dependency_overrides[get_current_user] = lambda: User(
        id=1,
        name="Test Dispatcher",
        email="dispatcher@test.dev",
        password_hash="x",
        role=UserRole.DISPATCHER,
        is_active=True,
    )
    yield client


@pytest_asyncio.fixture
async def depot(db):
    """A saved depot. Almost everything needs one — it is the root of the model."""
    from app.geospatial.queries import make_point
    from app.models.depot import Depot

    d = Depot(
        name="Test Hub",
        address="1 Test Road",
        latitude=28.5478,
        longitude=77.2733,
        # The PostGIS point as well as the floats, matching what depot_service
        # does — so radius queries work against seeded rows.
        location=make_point(28.5478, 77.2733),
    )
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return d
