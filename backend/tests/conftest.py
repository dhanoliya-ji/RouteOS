"""Shared pytest fixtures.

Nearly empty, and that is a consequence of the design rather than an oversight:
no test here needs a database, an HTTP client or a running server, so there is
no setup to share. See README.md for what that buys and what it leaves untested.
"""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(scope="session")
def event_loop():
    """A session-scoped event loop.

    LEGACY. Overriding `event_loop` is deprecated in pytest-asyncio 0.25 (the
    pinned version) in favour of the `asyncio_default_fixture_loop_scope`
    setting in pytest.ini.

    It is harmless today only because nothing in the suite is async, so the
    fixture is never actually requested. It will start warning — and eventually
    break — as soon as an async test is added, which is exactly when someone
    would be least expecting it.
    """
    loop = asyncio.new_event_loop()
    yield loop
    # Explicitly closed rather than left to the garbage collector, which would
    # otherwise emit a ResourceWarning about an unclosed loop.
    loop.close()
