# `backend/` — the RouteOS API server

A single Python process (FastAPI on uvicorn) that does three jobs at once:

1. **Serves the REST API** — CRUD for orders, vehicles, depots, routes; login and
   role checks; dashboard and analytics numbers.
2. **Runs the route optimizer** — takes a pile of pending orders plus the
   available fleet and computes who delivers what, in what order.
3. **Runs the delivery simulation** — an internal clock that walks each vehicle
   along its route, updates the database as deliveries complete, and pushes every
   change to connected browsers over a WebSocket.

Everything below is about how those three fit together.

---

## The layer cake

Read this top to bottom. **Arrows only ever point downward** — a lower layer never
imports an upper one. That single rule is what keeps the code navigable: if you
want to know what calls a function, you look *up*; what it depends on, *down*.

```
   ┌──────────────────────────────────────────────────────────────┐
   │  HTTP request                              WebSocket client  │
   └───────────────────────┬──────────────────────────┬───────────┘
                           │                          │
   ═══════════════════════ │ ════════════════════════ │ ═══════════
    LAYER 1                ▼                          ▼
    app/api/          routes/*.py                  routes/ws.py
                      "read the request,
                       check permission,
                       call a service,
                       return the answer"
                      + dependencies/  <- auth: who are you, may you do this?
   ═══════════════════════ │ ═══════════════════════════════════════
    LAYER 2                ▼
    app/schemas/      Pydantic models
                      "is this input valid? what shape goes back out?"
   ═══════════════════════ │ ═══════════════════════════════════════
    LAYER 3                ▼
    app/services/     the actual business rules
                      "a DELIVERED order may not be edited"
                      "accepting a plan turns it into real routes"
   ══════════ │ ═══════════ │ ══════════ │ ═══════════ │ ═══════════
    LAYER 4   ▼             ▼            ▼             ▼
              optimization/  geospatial/  simulation/   websocket/
              solver +       distance +   the moving    broadcast to
              greedy         PostGIS      vehicles      all browsers
              baseline       helpers      clock
   ══════════ │ ═══════════════════════════════════════════════════
    LAYER 5   ▼
    app/models/       SQLAlchemy tables (the shape of the database)
    app/db/           engine + session (how we talk to it)
   ═══════════════════════ │ ═══════════════════════════════════════
    LAYER 6                ▼
    app/core/         config, logging, errors, security, redis, metrics
                      "plumbing every layer above is allowed to use"
   ═══════════════════════ │ ═══════════════════════════════════════
                           ▼
                  PostgreSQL + PostGIS          Redis
                  (the durable truth)           (cache + hot state)
```

`app/core/` is the one exception to "arrows point down": *any* layer may import it,
because config and logging are needed everywhere. It imports nothing from `app/`
in return, so it can never create a cycle.

---

## Following one request all the way through

Take `POST /api/v1/orders` — a dispatcher creating a delivery. Each numbered step
is a real file you can open.

```
 1. main.py                      request arrives; middleware stamps an
                                 X-Request-ID and starts a latency timer
                                        │
 2. api/routes/orders.py         router matched: create_order()
                                        │
 3. api/dependencies/auth.py     decode the JWT -> load the User
                                 is the role DISPATCHER or ADMIN?  ── no ─> 403
                                        │ yes
 4. schemas/order.py             OrderCreate validates the body:
                                 latitude in [-90, 90], weight > 0,
                                 window_end after window_start  ── bad ─> 422
                                        │ ok
 5. services/order_service.py    the business rules:
                                   - reserve the next ORD-00042 number
                                   - build the PostGIS point for the address
                                        │
 6. geospatial/queries.py        make_point(lat, lon) -> SQL expression
                                        │
 7. models/order.py              the Order row takes shape
 8. db/session.py                INSERT + COMMIT
                                        │
 9. schemas/order.py             OrderOut trims the row to public fields
                                 (no internal columns leak)
                                        │
10. main.py                      log the line as JSON, record the Prometheus
                                 metric, return 201
```

Steps 3, 4 and 9 are all *guard rails* — the route handler itself is barely
five lines, because a handler's only job is to wire the guards to a service.

---

## The three engines

The API is the obvious one. The other two are easy to miss, so they're worth
naming explicitly:

```
  ENGINE 1 — REST API                      request in, answer out
  ───────────────────────────────────────────────────────────────────
  Lives in api/ + services/. Purely reactive: nothing happens unless
  a client asks. This is 80% of the files.


  ENGINE 2 — the optimizer                 minutes of CPU per run
  ───────────────────────────────────────────────────────────────────
  Lives in optimization/, driven by services/optimization_service.py.

  A solve pins a CPU core for a long time, so it must NOT happen inside
  a request. Instead:

     POST /optimization/jobs  ──> save a PROCESSING row, return 202
                                  immediately, and hand the work to a
                                  background asyncio task
                                            │
                              solver runs in a worker thread
                              (asyncio.to_thread, so the event loop
                               stays free to serve other requests)
                                            │
                              progress heartbeats every 5s over WS
                                            │
                              save the finished plan as JSON, broadcast
                              OPTIMIZATION_COMPLETED

  The plan is NOT live yet. A human accepts or discards it — see
  services/optimization_service.py.


  ENGINE 3 — the simulation                one tick per second, forever
  ───────────────────────────────────────────────────────────────────
  Lives in simulation/engine.py. A single asyncio loop that wakes up
  every second, moves each active vehicle a little further along its
  route, writes the new state to the database, and broadcasts it.

  The backend is the ONLY source of truth for movement. The frontend
  draws what it is told and never invents a position.
```

---

## Folder index

Every folder has its own `README.md` with the same structure — what it does,
who calls it, what it depends on, and a file-by-file table.

| Folder | One-line job |
|---|---|
| [`app/`](app/) | The application package. Its README holds the import rules. |
| [`app/api/`](app/api/) | HTTP and WebSocket endpoints. Thin: validate, authorize, delegate. |
| [`app/core/`](app/core/) | Cross-cutting plumbing: settings, logging, errors, JWT, Redis, metrics. |
| [`app/db/`](app/db/) | The async database engine, the session factory, the declarative base. |
| [`app/geospatial/`](app/geospatial/) | Distance maths (haversine) and PostGIS query builders. |
| [`app/models/`](app/models/) | SQLAlchemy ORM classes — one per table. The database schema in Python. |
| [`app/optimization/`](app/optimization/) | The VRP solver, the greedy baseline it's measured against, the distance matrix. |
| [`app/schemas/`](app/schemas/) | Pydantic request/response models. The API's public contract. |
| [`app/services/`](app/services/) | Business logic. Every rule that isn't a database constraint lives here. |
| [`app/simulation/`](app/simulation/) | The vehicle-movement clock. |
| [`app/websocket/`](app/websocket/) | Connection registry + fan-out broadcast. |
| [`alembic/`](alembic/) | Database migrations. |
| [`scripts/`](scripts/) | Hand-run CLI tools: seed, demo data, solver benchmark. |
| [`tests/`](tests/) | pytest suite. |

---

## Root files

| File | Purpose |
|---|---|
| `requirements.txt` | Pinned dependencies. Exact versions, because two of them need it — see the comments in the file about `passlib` and `bcrypt`. |
| `Dockerfile` | Builds the backend image on `python:3.12-slim`. Installs `build-essential` because `asyncpg` and `bcrypt` compile C extensions. |
| `docker-entrypoint.sh` | Runs *before* the server: wait for Postgres to accept TCP, `alembic upgrade head`, seed demo data, then `exec` uvicorn. |
| `alembic.ini` | Alembic config. `sqlalchemy.url` is deliberately blank — `alembic/env.py` fills it from `Settings` so there's one source of truth for the DB URL. |
| `pytest.ini` | Test config. `asyncio_mode = auto` means async test functions need no decorator. |
| `.dockerignore` | Keeps `__pycache__`, `.venv` and friends out of the build context. |

---

## Running it

```bash
# From the repo root — brings up Postgres, Redis, backend and frontend together.
docker compose up

# Or locally, with Postgres and Redis already running:
cd backend
pip install -r requirements.txt
alembic upgrade head              # create the tables
python -m scripts.seed_data       # demo users, depot, fleet, orders
uvicorn app.main:app --reload

# Tests. 58 need nothing; 50 more need a PostGIS database and skip without
# one — see tests/README.md for the container command.
pytest
```

Once it's up:

| URL | What you get |
|---|---|
| `/docs` | Interactive OpenAPI explorer — every endpoint, try-it-out included |
| `/health` | Whether Postgres and Redis are reachable, plus live connection counts |
| `/metrics` | Prometheus scrape: request counts, latency histogram, active simulations |
| `/ws/fleet` | The live event stream |
