"""Authentication and authorization tests (no database).

The role system had no tests, and it is the only thing standing between a
VIEWER and the delete button. These cover it in two ways:

1. **The guard logic directly** — `require_roles` and `get_current_user` are
   ordinary functions, so the ladder can be tested by calling them.
2. **The HTTP surface** — a role x endpoint matrix asserting that a caller
   without the right role is rejected.

Both are DB-free, and part 2 is DB-free *because* of what it tests: an
authorization guard rejects before the handler runs, so no query is ever
reached. That is also why this file only asserts the **rejection** direction.
Proving an allowed role gets a correct *answer* needs real data, and lives in
the DB-backed tests.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies.auth import get_current_user, require_roles
from app.core.errors import APIError
from app.db.session import get_db
from app.main import app
from app.models.enums import UserRole
from app.models.user import User


# --- fakes -------------------------------------------------------------------
# get_current_user takes a session and runs one query. Rather than stand up a
# database for that, hand it an object that answers the one call it makes.


class _FakeResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeDb:
    """Answers `await db.execute(...)` with a preset user (or None)."""

    def __init__(self, user):
        self._user = user

    async def execute(self, *_args, **_kwargs):
        return _FakeResult(self._user)


class _NoDb:
    """A session that refuses instantly.

    Used for the "allowed role" assertions below. Without it those requests
    fall through to the real engine and sit waiting on a DNS lookup for a
    Postgres host that is not there — which made this file take a minute
    instead of a second, and made the tests quietly dependent on how fast the
    network fails.

    Refusing here states the intent outright: this file provides no database.
    """

    async def execute(self, *_args, **_kwargs):
        raise RuntimeError("no database in this test - see _NoDb")

    async def commit(self):
        raise RuntimeError("no database in this test - see _NoDb")

    async def rollback(self):
        return None

    def add(self, *_args, **_kwargs):
        return None


def _user(role: UserRole, *, active: bool = True, uid: int = 1) -> User:
    """An unsaved User. Never touches a session, so no database is involved."""
    return User(
        id=uid,
        name="Test",
        email=f"{role.value.lower()}@test.dev",
        password_hash="not-a-real-hash",
        role=role,
        is_active=active,
    )


# --- the role ladder ---------------------------------------------------------


class TestRequireRoles:
    """`require_roles` builds a guard. These call the guard directly."""

    @pytest.mark.parametrize("role", [UserRole.VIEWER, UserRole.DISPATCHER, UserRole.ADMIN])
    async def test_admin_passes_every_guard(self, role):
        """ADMIN short-circuits before the role list is even consulted.

        This one line of the implementation is the entire ladder: it is why no
        endpoint has to spell out require_roles(DISPATCHER, ADMIN), and why
        nobody can lock admins out by forgetting the second argument.
        """
        guard = require_roles(role)
        admin = _user(UserRole.ADMIN)
        assert await guard(user=admin) is admin

    async def test_matching_role_passes(self):
        guard = require_roles(UserRole.DISPATCHER)
        dispatcher = _user(UserRole.DISPATCHER)
        assert await guard(user=dispatcher) is dispatcher

    async def test_non_matching_role_is_forbidden(self):
        guard = require_roles(UserRole.DISPATCHER)
        with pytest.raises(APIError) as exc:
            await guard(user=_user(UserRole.VIEWER))
        # 403, not 401: we know who they are, and signing in again will not help.
        assert exc.value.status_code == 403
        assert exc.value.code == "FORBIDDEN"

    async def test_a_guard_may_accept_several_roles(self):
        guard = require_roles(UserRole.DISPATCHER, UserRole.VIEWER)
        for role in (UserRole.DISPATCHER, UserRole.VIEWER):
            assert await guard(user=_user(role)) is not None


class TestGetCurrentUser:
    """Token -> User, and the four ways it refuses."""

    async def test_missing_token_is_unauthenticated(self):
        with pytest.raises(APIError) as exc:
            await get_current_user(token=None, db=_FakeDb(None))
        assert exc.value.status_code == 401
        assert exc.value.code == "NOT_AUTHENTICATED"

    async def test_undecodable_token_is_rejected(self):
        with pytest.raises(APIError) as exc:
            await get_current_user(token="not-a-jwt", db=_FakeDb(None))
        assert exc.value.status_code == 401
        assert exc.value.code == "INVALID_TOKEN"

    async def test_valid_token_for_a_missing_user_is_rejected(self):
        """The token decodes, but the account is gone."""
        from app.core.security import create_access_token

        token = create_access_token(999, "ADMIN")
        with pytest.raises(APIError) as exc:
            await get_current_user(token=token, db=_FakeDb(None))
        assert exc.value.status_code == 401

    async def test_deactivated_user_is_rejected(self):
        """The revocation path, and the reason for the per-request lookup.

        A JWT stays valid until it expires — 24 hours by default — so without
        this check, disabling an account would leave it working for a day. The
        is_active test IS the revocation mechanism.
        """
        from app.core.security import create_access_token

        token = create_access_token(1, "ADMIN")
        inactive = _user(UserRole.ADMIN, active=False)
        with pytest.raises(APIError) as exc:
            await get_current_user(token=token, db=_FakeDb(inactive))
        assert exc.value.status_code == 401
        # Same code as a missing user, deliberately: the client's situation is
        # identical, and distinguishing them would leak whether an account exists.
        assert exc.value.code == "NOT_AUTHENTICATED"

    async def test_active_user_is_returned(self):
        from app.core.security import create_access_token

        token = create_access_token(1, "DISPATCHER")
        expected = _user(UserRole.DISPATCHER)
        assert await get_current_user(token=token, db=_FakeDb(expected)) is expected


# --- the HTTP surface --------------------------------------------------------

API = "/api/v1"

# Endpoints that change something, and the LOWEST role allowed to call them.
# Read off the route modules; see backend/app/api/README.md.
WRITE_ENDPOINTS = [
    ("POST", f"{API}/orders", UserRole.DISPATCHER),
    ("PATCH", f"{API}/orders/1", UserRole.DISPATCHER),
    ("POST", f"{API}/orders/1/cancel", UserRole.DISPATCHER),
    ("DELETE", f"{API}/orders/1", UserRole.DISPATCHER),
    ("POST", f"{API}/vehicles", UserRole.DISPATCHER),
    ("PATCH", f"{API}/vehicles/1", UserRole.DISPATCHER),
    ("POST", f"{API}/depots", UserRole.DISPATCHER),
    ("PATCH", f"{API}/depots/1", UserRole.DISPATCHER),
    ("POST", f"{API}/optimization/jobs", UserRole.DISPATCHER),
    ("POST", f"{API}/optimization/runs/1/accept", UserRole.DISPATCHER),
    # These two are the slowest rows in the file, for a structural reason worth
    # knowing: the simulation engine opens its OWN session with
    # AsyncSessionLocal() rather than taking the get_db dependency, so the stub
    # below cannot reach it and the request waits on a real connection attempt.
    # That is also why the engine can run outside a request at all.
    ("POST", f"{API}/simulation/start", UserRole.DISPATCHER),
    ("POST", f"{API}/simulation/traffic", UserRole.DISPATCHER),
    # Deleting infrastructure is admin-only, unlike deleting an order — a depot
    # cascades to its whole fleet. See api/routes/README.md.
    ("DELETE", f"{API}/vehicles/1", UserRole.ADMIN),
    ("DELETE", f"{API}/depots/1", UserRole.ADMIN),
    ("GET", f"{API}/users", UserRole.ADMIN),
]


@pytest.fixture
def client():
    """A TestClient with no dependency overrides."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def as_role():
    """Sign in as a role, without a database or a real token.

    Overrides get_current_user, which FastAPI substitutes even though it is
    reached indirectly through require_roles — so the ladder still runs, on a
    user we chose.
    """

    def _apply(role: UserRole):
        app.dependency_overrides[get_current_user] = lambda: _user(role)
        # Also stub the session, so a request that gets past the guard fails
        # immediately instead of waiting on an unreachable database.
        app.dependency_overrides[get_db] = lambda: _NoDb()
        # raise_server_exceptions=False so a request that passes the guard and
        # then fails for want of a database becomes a 500 RESPONSE rather than
        # propagating the exception out of the client. Without it, the
        # "allowed role" assertions below cannot run at all.
        return TestClient(app, raise_server_exceptions=False)

    yield _apply
    app.dependency_overrides.clear()


class TestUnauthenticated:
    def test_no_token_is_rejected_everywhere(self, client):
        """Every protected endpoint refuses an anonymous caller."""
        for method, path, _ in WRITE_ENDPOINTS:
            res = client.request(method, path, json={})
            assert res.status_code == 401, f"{method} {path} allowed an anonymous caller"
            assert res.json()["error"]["code"] in ("NOT_AUTHENTICATED", "INVALID_TOKEN")

    def test_reads_also_require_a_token(self, client):
        for path in (f"{API}/orders", f"{API}/vehicles", f"{API}/routes",
                     f"{API}/dashboard/summary", f"{API}/analytics/summary"):
            assert client.get(path).status_code == 401, f"{path} was readable anonymously"

    def test_public_endpoints_stay_public(self, client):
        """/health takes no token, because a load balancer has none to give.

        The slowest test here (~5s): /health genuinely probes Postgres and
        Redis, and with neither running it waits for both to fail. That is the
        endpoint doing its job, so the cost is accepted rather than mocked away
        — the point being asserted is only that it needs no credentials.
        """
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 200


class TestRoleMatrix:
    def test_viewer_cannot_write_anything(self, as_role):
        """The headline guarantee: a VIEWER is read-only across the whole API."""
        client = as_role(UserRole.VIEWER)
        for method, path, _ in WRITE_ENDPOINTS:
            res = client.request(method, path, json={})
            assert res.status_code == 403, f"a VIEWER reached {method} {path}"
            assert res.json()["error"]["code"] == "FORBIDDEN"

    def test_dispatcher_cannot_delete_infrastructure(self, as_role):
        """The asymmetry is deliberate, so it gets a test of its own.

        A dispatcher may delete an order — one row of operational data — but
        not a vehicle or a depot, because deleting a depot cascades to its
        vehicles, orders and routes.
        """
        client = as_role(UserRole.DISPATCHER)
        for method, path, needed in WRITE_ENDPOINTS:
            if needed is not UserRole.ADMIN:
                continue
            res = client.request(method, path, json={})
            assert res.status_code == 403, f"a DISPATCHER reached admin-only {method} {path}"

    def test_the_guard_lets_the_right_role_through(self, as_role):
        """Authorization passes; the request then fails for other reasons.

        Asserts only that the response is NOT an auth rejection. Past the
        guard, these reach code that needs a database this test deliberately
        does not provide, so the observed status is a 422 (the body failed
        validation first) or a 500 (the query could not connect) — either of
        which proves the guard admitted the caller.

        The limitation is worth stating: because a 500 satisfies this
        assertion, it confirms authorization only, not that the endpoint works.
        Proving a correct answer is the DB-backed tests' job.
        """
        for method, path, needed in WRITE_ENDPOINTS:
            client = as_role(needed)
            res = client.request(method, path, json={})
            assert res.status_code not in (401, 403), (
                f"a {needed.value} was blocked from {method} {path}"
            )

    def test_admin_is_never_blocked(self, as_role):
        """The ADMIN bypass, at the HTTP level rather than the function level."""
        client = as_role(UserRole.ADMIN)
        for method, path, _ in WRITE_ENDPOINTS:
            res = client.request(method, path, json={})
            assert res.status_code not in (401, 403), f"an ADMIN was blocked from {method} {path}"
