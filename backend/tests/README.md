# `backend/tests/` — the test suite

119 tests in two tiers.

```bash
cd backend
pytest                                    # 119 with a database, 58 without
pytest -q                                 # quiet
pytest tests/test_orders_api.py -v        # one file
pytest -k permission                      # one topic
pytest --durations=10                     # find the slow ones
```

---

## The two tiers

**Tier 1 — no database (58 tests, always run).** Algorithms, configuration,
security primitives and the permission matrix. Nothing to install, nothing to
start.

**Tier 2 — a real PostGIS database (61 tests, skipped without one).** The API
and business-rule tests.

Why Tier 2 needs real Postgres rather than SQLite: the app stores `Geography`
columns and `JSONB`, and calls `date_trunc` and `ST_DWithin`. SQLite has none of
them, so testing on it would mean stubbing out the very code paths under test —
tests that pass while proving little. Instead these skip cleanly, so `pytest`
still works on a machine with no Docker:

```
58 passed, 61 skipped
```

Start a database and they run:

```bash
docker run -d --name routeos-test-db \
  -e POSTGRES_USER=routeos -e POSTGRES_PASSWORD=routeos_test \
  -e POSTGRES_DB=routeos_test -p 55432:5432 postgis/postgis:16-3.4
```

Port **55432**, not 5432, so it cannot collide with a Postgres you already have
running. Point elsewhere with `TEST_DATABASE_URL`.

---

## What is covered

| File | Tests | Tier | Covers |
|---|---:|---|---|
| `test_config.py` | 10 | 1 | Settings normalisation, CORS regex safety |
| `test_geospatial.py` | 4 | 1 | Distance maths, greedy capacity handling |
| `test_optimization.py` | 5 | 1 | The solver's constraints, against the real OR-Tools |
| `test_plan_selection.py` | 6 | 1 | Which of the two plans gets dispatched |
| `test_security.py` | 15 | 1 | Password hashing, JWT signing and rejection |
| `test_permissions.py` | 18 | 1 | The role ladder, and a role × endpoint matrix |
| `test_orders_api.py` | 28 | 2 | Order CRUD, status rules, paging, PostGIS search |
| `test_optimization_api.py` | 22 | 2 | Solve → review → accept → discard |
| `test_dashboard_api.py` | 11 | 2 | The on-time rate, and the KPI tiles |

### Still not covered

Worth knowing before trusting a green run:

- **The simulation engine.** Movement, the tick loop, and arrival handling have
  no tests — they need a database *and* control of time.
- **The WebSocket.** No test connects to `/ws/fleet` or asserts an event payload.
- **Vehicle, depot and route endpoints.** Only orders got the full CRUD
  treatment; the others are covered for permissions only.
- **Analytics.** The chart aggregations are untested. (The dashboard summary now is.)
- **Frontend pages.** The frontend has its own suite (`npm test`, 83 tests)
  covering the API client, stores, hooks and primitives — but no page.

---

## How the fixtures work

All in `conftest.py`.

```
db_engine   an engine, against a schema created once per process
    │
db          a session in a transaction that is ALWAYS rolled back
    │
client      an httpx client whose requests use that same session
    │
as_dispatcher   the same client, signed in as a DISPATCHER
```

**Isolation comes from the transaction, not from recreating the schema.** Every
test runs inside one that is rolled back afterwards, so tests share a schema and
still cannot see each other's data. Because `client` overrides `get_db` with the
test's own session, a row the test creates is visible to the request, and a row
the request creates is visible to the test — then all of it disappears.

Two details in there are load-bearing, and commented as such:

- **The schema is built once per process behind a module flag**, not by a
  session-scoped fixture. asyncpg binds a connection to the event loop that
  created it, so a session-scoped async fixture is a `ScopeMismatch` against
  function-scoped tests.
- **The teardown rollback is guarded.** A test that provokes a constraint
  violation has already had its transaction aborted, and rolling back again
  warns.

`as_dispatcher` overrides `get_current_user` rather than logging in for real.
Logging in would work, but it would make every CRUD test depend on the auth
flow, so one auth bug would fail dozens of unrelated tests. Authentication has
its own file.

---

## Conventions worth following

**Assert properties, not exact answers — for anything heuristic.**
`test_optimization.py` never asserts a specific route. The solver is a
time-bounded heuristic whose output legitimately varies between versions and
machines, but "no van is overloaded" must hold every time. Its quality floor
allows the solver to be 5% *worse* than greedy, deliberately: demanding a win
would make the suite fail on a loaded machine rather than on a regression.

**Document behaviour you did not fix.** Two tests pin defects rather than
wishes, and say so in their docstrings:

- `test_a_missing_depot_raises_rather_than_answering` — nothing handles
  `IntegrityError`, so a foreign-key violation escapes as an unhandled
  exception instead of a clean 409. Reachable in production through the
  `MAX(id)` order-number race too.
- `test_missing_expiry_is_still_decodable` — PyJWT enforces `exp` only when the
  claim is present. Nothing here mints a token without one, but the guarantee
  should not be overstated.

A test that asserts the behaviour you *want* while the code does something else
is worse than no test: it fails for the right reason once and then gets
disabled.

**Push an objective against the constraint it should not beat.** The capacity
test uses `MIN_DISTANCE` precisely *because* the cheapest plan by distance would
overload one van — so the test proves the constraint wins rather than that the
objective happened to agree with it.

---

## Three tests found real bugs

Both are fixed, and both are the argument for having written any of this.

**Cross-field validation returned 500 instead of 422.** A rule enforced by a
`@model_validator` raising `ValueError` — the delivery-window check — put the
raw exception object in pydantic's `ctx`, which `json.dumps` cannot serialise.
The 422 was never sent and the field-level detail was lost. Fixed with
`jsonable_encoder` in `core/errors.py`.

**A plan could be accepted twice.** Accepting left the run `COMPLETED`, and
that status was the only guard, so a double-click dispatched the fleet twice —
duplicate routes, orders re-assigned, vehicle loads overwritten. Fixed by
guarding on the routes' own `optimization_run_id`, which needed no migration.

**The on-time rate was ~100% by construction.** It counted every
DELIVERY_COMPLETED event over delivered orders — the same quantity twice — and a
clamp hid the rest. Now it compares each stop's `actual_arrival` against its
order's `delivery_window_end`. Confirmed as a real regression test by restoring
the old code: 5 of the 11 new tests fail against it.

---

## Timing

About two minutes with a database, and where it goes is worth knowing:

| Cost | Why |
|---|---|
| ~60s | `test_optimization.py` runs real OR-Tools searches. Inherent. |
| ~35s | `test_optimization_api.py` also solves, with the budget turned down to 1s per test via `monkeypatch`. |
| ~5s | One test hits `/health`, which genuinely probes Postgres and Redis. |
| ~3s each | Two permission rows are slow because the simulation endpoints open their **own** session rather than taking `get_db`, so the stub cannot reach them. |

`pytest -k "not optimization"` runs everything else in a few seconds while
iterating.
