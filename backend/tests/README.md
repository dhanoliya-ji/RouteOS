# `backend/tests/` — the test suite

25 tests across four files. **None of them touch a database, a network or an
HTTP server.**

```bash
cd backend
pytest              # ~60 seconds
pytest -q           # quiet
pytest tests/test_optimization.py -v      # one file
pytest -k capacity                        # one topic
```

The runtime is almost entirely OR-Tools: `test_optimization.py` runs real
solves, and a real solve takes seconds by design.

---

## What is tested, and what isn't

That every test is DB-free is a deliberate consequence of the architecture, not
an accident. Because `optimization/` takes plain dataclasses instead of ORM rows
(see [`../app/optimization/README.md`](../app/optimization/README.md)), the
interesting logic can be tested by constructing a few objects.

```
   TESTED (pure logic, no I/O)
   ┌────────────────────────────────────────────────────────┐
   │ core/config.py         URL + CORS normalisation        │
   │ geospatial/distance.py haversine, road factor, time    │
   │ optimization/solver.py the real OR-Tools model         │
   │ optimization/baseline.py greedy capacity handling      │
   │ services/optimization_service._better_plan             │
   └────────────────────────────────────────────────────────┘

   NOT TESTED (needs a database, a client, or a clock)
   ┌────────────────────────────────────────────────────────┐
   │ every API endpoint          (no httpx/TestClient tests)│
   │ every CRUD service          (needs a session)          │
   │ auth: hashing, JWT, RBAC    (no test at all)           │
   │ simulation/engine.py        (needs a DB + time)        │
   │ websocket/manager.py        (needs sockets)            │
   │ accept_plan / discard_plan  (needs a DB)               │
   └────────────────────────────────------------------------┘
```

The gap is worth naming honestly: the suite covers the *algorithms* well and the
*plumbing* not at all. The highest-value additions would be endpoint tests via
`httpx.AsyncClient` and a role-permission matrix test, both of which need a
throwaway database.

---

## File index

| File | Tests | Covers |
|---|---|---|
| `conftest.py` | — | shared fixtures |
| `test_config.py` | 8 | settings normalisation, CORS regex safety |
| `test_geospatial.py` | 4 | distance maths + greedy capacity |
| `test_optimization.py` | 5 | the solver's constraints |
| `test_plan_selection.py` | 6 | which plan gets dispatched |

---

## `test_config.py` — testing deployment wiring

Unusual to unit-test configuration, but these encode things that broke real
deploys:

- **A managed `postgres://` URL must yield both driver URLs.** One env var in,
  an asyncpg URL for the app and a psycopg URL for Alembic out.
- **A bare hostname must expand to a full origin.** Some hosts inject a peer
  service's hostname with no scheme, and CORS matching is exact — a bare host
  would never match a browser's `Origin` header.
- **The CORS regex must reject lookalikes.** This one is security-relevant, and
  the test says so:

  ```
  allowed:  https://routeos-frontend.onrender.com
            https://routeos-frontend-x9k2.onrender.com
  rejected: https://onrender.com.evil.com      <- suffix spoofing
            http://routeos-frontend.onrender.com  <- not TLS
            https://sub.routeos.onrender.com      <- extra label
  ```

  A pattern anchored only at the start would admit the first of those. The
  `$` anchor is what makes it safe, and the test is what keeps it there.

Each test passes `_env_file=None` so an ambient `.env` cannot make the
assertions non-deterministic.

---

## `test_optimization.py` — pinning solver constraints

These run the actual solver and assert on properties, not exact answers — the
right approach for a heuristic, whose output may legitimately change between
versions or CPU speeds:

| Test | Asserts |
|---|---|
| capacity separation | 60 kg + 50 kg never share a 100 kg van |
| structural validity | sequences ordered, no order visited twice, assigned ∪ unassigned covers the input exactly and the two don't overlap |
| impossible order | a 500 kg order for a 100 kg fleet is reported `CAPACITY_EXCEEDED`, not silently dropped |
| time windows | a stop with a 0–30 min window gets an ETA ≤ 30 |
| quality floor | the solver is within 5% of greedy or better |

Two design notes:

**`pytest.importorskip("ortools")`** at the top — the suite degrades to skipping
these rather than erroring if OR-Tools isn't installed.

**The quality floor is `base_dist * 1.05`, not `base_dist`.** Deliberately
loose, because the solver is a time-bounded heuristic: demanding it always win
would make the test flaky on a slow or loaded machine. It catches a real
regression (the solver becoming dramatically worse) without failing on noise.

---

## `test_plan_selection.py` — the most interesting file

Tests `_better_plan`, the rule deciding whether to dispatch the solver's plan or
the greedy baseline's. The cases read as documentation of the policy:

```
   solver beats baseline on distance and vehicles   -> solver
   solver ran out of time and came back worse       -> baseline
   one plan serves more orders, even if pricier     -> that one
   exact tie                                        -> solver
   100 km longer but one fewer vehicle              -> solver
```

The last is the one that pins the model's own trade-off, and the final test
states it as a number:

```python
assert _plan_cost(one_vehicle)  == 310.0     # 10 km + 1 × 300
assert _plan_cost(two_vehicles) == 610.0     # 10 km + 2 × 300
```

A vehicle is priced at 300 km. That mirrors `VEHICLE_FIXED_COST` inside the
solver, so both plans are judged by the trade-off the solver was optimising for.
If someone changes one constant and not the other, this test fails — which is
exactly what it is for.

The second case is documented as **observed behaviour**, not a hypothetical: a
CPU-starved instance really did return a longer plan on the same vehicle count.

---

## Two things to know about the setup

**1. `asyncio_mode = auto`** (in `pytest.ini`) means an `async def` test needs no
`@pytest.mark.asyncio` decorator. Nothing in the current suite is actually async,
but the setting is what lets you add one without ceremony.

**2. The `event_loop` fixture in `conftest.py` is legacy.**

```python
@pytest.fixture(scope="session")
def event_loop(): ...
```

Overriding `event_loop` is deprecated in pytest-asyncio 0.25 (the pinned
version) in favour of `asyncio_default_fixture_loop_scope`. It is harmless today
because no test is async, and the fixture is simply never requested — but it
will warn, and eventually break, once async tests are added. The modern
equivalent is a line in `pytest.ini`, not a fixture.

**3. `aiosqlite` is an unused dependency.**

It sits in `requirements.txt` under the test section, presumably intended for
in-memory database tests. No test imports it, and nothing in the app references
SQLite. It should either be used — it is the obvious way to close the CRUD
coverage gap above — or dropped.
