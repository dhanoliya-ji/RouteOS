# RouteOS — Intelligent Logistics & Fleet Optimization Platform

RouteOS is a full-stack logistics platform that plans **capacity- and time-window-aware
multi-vehicle delivery routes** using constraint-based Vehicle Routing Problem (VRP)
optimization, PostGIS geospatial data, and **backend-driven real-time delivery simulation**
streamed to the browser over WebSockets.

It is a real, end-to-end application: **Frontend → REST/WebSocket API → Services →
PostgreSQL/PostGIS → OR-Tools optimizer → Simulation engine → live map.** No fake buttons,
no hard-coded metrics — every number shown is computed from generated routes and stored data.

---

## Problem statement

Given N delivery orders, M vehicles, one or more depots, vehicle capacities, geographic
coordinates, delivery priorities and time windows, and road travel times — assign orders to
vehicles and sequence stops to **minimize total distance / travel time / vehicles used /
late deliveries**, subject to operational constraints (capacity, time windows, service time,
max route distance). RouteOS solves this with Google OR-Tools and quantifies the gain against
a naive nearest-neighbour baseline.

---

## Key features

- **Auth & RBAC** — JWT login, roles `ADMIN` / `DISPATCHER` / `VIEWER`, protected routes.
- **Order / Fleet / Depot management** — full CRUD, filtering, search, pagination.
- **PostGIS geospatial** — geography POINT columns, `ST_DWithin`/`ST_Distance` "vehicles/orders
  nearby" endpoints, GiST spatial indexes.
- **OR-Tools VRP engine** — capacity dimension, time-window dimension, per-vehicle distance
  limit, priority-scaled drop penalties, three objectives (`MIN_DISTANCE`, `MIN_TIME`, `BALANCED`).
- **Baseline comparison** — measured distance/time/vehicle reduction vs nearest-neighbour.
- **Route Planner** — select orders + vehicles, optimize, review plan + map, **Accept / Discard**.
- **Real-time simulation** — backend moves vehicles along routes; the browser only renders
  what the backend publishes over `/ws/fleet`. Speed 1× / 5× / 20×.
- **Traffic disruption + re-optimization** — apply moderate/severe/breakdown to a live route,
  see delay + at-risk deliveries, then re-sequence only the remaining stops.
- **Dashboard & Analytics** — KPI cards + charts, all backed by SQL aggregation, Redis-cached.
- **Observability** — structured JSON logs with request IDs, `/health`, `/metrics` (Prometheus).

---

## Screenshots

Run the app (below) and capture:

- `/login` — demo account picker
- `/` — operations dashboard (KPIs + charts)
- `/planner` — optimize + baseline comparison + route polylines
- `/live` — live vehicle movement, traffic + reoptimize

_(Placeholder — add images to `docs/` and link them here.)_

---

## Technology stack

| Layer | Tech |
|---|---|
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, React Router, TanStack Query, Zustand, Leaflet + OpenStreetMap, Recharts |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2 (async), Pydantic v2, Alembic |
| Data | PostgreSQL 16 + PostGIS 3.4, Redis 7 |
| Optimization | Google OR-Tools (CP-SAT routing), Haversine fallback, optional OSRM `/table` |
| Realtime | FastAPI WebSockets, asyncio simulation loop |
| Infra | Docker, Docker Compose, nginx (frontend) |
| Tests | Pytest (+ real OR-Tools), Vitest-ready frontend, `tsc` typecheck |

---

## Optimization algorithm (VRP)

The solver ([`backend/app/optimization/solver.py`](backend/app/optimization/solver.py)) builds a
routing model where node 0 is the depot and nodes 1..n are orders:

- **Arc cost** = distance (`MIN_DISTANCE`), duration (`MIN_TIME`), or a blend (`BALANCED`).
  A per-vehicle **fixed cost** discourages using more vehicles than needed.
- **Capacity dimension** enforces each vehicle's `capacity_kg` (`AddDimensionWithVehicleCapacity`).
- **Distance dimension** caps each vehicle's `max_route_distance_km`.
- **Time dimension** carries travel + service time and enforces delivery windows (waiting allowed);
  node arrival times become each stop's ETA.
- **Disjunctions** let orders be dropped at a **priority-scaled penalty** (URGENT ≫ LOW), so the
  solver serves everything feasible and reports the rest with a reason
  (`CAPACITY_EXCEEDED`, `NO_AVAILABLE_VEHICLE`, `TIME_WINDOW_INFEASIBLE`, …).
- **Search**: `PATH_CHEAPEST_ARC` first solution + `GUIDED_LOCAL_SEARCH` metaheuristic under a
  wall-clock limit (`SOLVER_TIME_LIMIT_SECONDS`).

> This is a **heuristic** solver run under a time budget — high-quality feasible routes, **not
> proven-optimal**. VRP is NP-hard; RouteOS uses practical operations-research heuristics.

### Distance matrix
Primary source is **OSRM** `/table` when `USE_OSRM=true` and reachable. Otherwise RouteOS falls
back to **Haversine × `ROAD_DISTANCE_FACTOR`** (default 1.25, a common urban road-network
approximation) with travel time = distance ÷ `AVERAGE_SPEED_KMH`. The fallback guarantees the
optimizer always runs offline. See [`matrix.py`](backend/app/optimization/matrix.py).

---

## How PostGIS is used
- `depots.location` and `orders.location` are `GEOGRAPHY(POINT, 4326)`, written with
  `ST_SetSRID(ST_MakePoint(lon,lat), 4326)` and indexed with GiST.
- `GET /vehicles/nearby` and `GET /orders/nearby` use `ST_DWithin` for radius filtering and
  `ST_Distance` for true-distance sorting. See [`geospatial/queries.py`](backend/app/geospatial/queries.py).

## How WebSockets work
A single in-process [`ConnectionManager`](backend/app/websocket/manager.py) fans out events to all
clients on `/ws/fleet`. The **simulation engine is the only writer** of movement; the frontend
renders `VEHICLE_LOCATION_UPDATED`, `ORDER_STATUS_UPDATED`, `ROUTE_STATUS_UPDATED`,
`DELIVERY_COMPLETED`, `ROUTE_DELAYED`, `ROUTE_REOPTIMIZED`.

## How route simulation works
[`SimulationEngine`](backend/app/simulation/engine.py) runs an asyncio loop that advances each
active vehicle along its route polyline (depot → stops → depot), mutating **real DB state**
(order/route/vehicle status, `vehicle_location_history`, `delivery_events`) and broadcasting each
change. Traffic events apply a per-route speed factor; re-optimization rebuilds only the remaining
(undelivered) stops and the engine reloads the route on its next tick.

## Why Redis is used
Selective caching only: the **dashboard summary** and **analytics aggregations** (multiple
COUNT/SUM queries, read on every page load) are cached with short TTLs and invalidated on relevant
writes. Redis also backs live state/fan-out. See [`core/redis.py`](backend/app/core/redis.py).

---

## Database schema
Tables: `users`, `depots`, `vehicles`, `orders`, `routes`, `route_stops`, `optimization_runs`,
`vehicle_location_history`, `delivery_events`. Indexes on status/priority/foreign-keys/`created_at`
and GiST spatial indexes on geography columns. Enums stored as strings. See
[`backend/app/models/`](backend/app/models/) and the initial migration
[`alembic/versions/0001_initial.py`](backend/alembic/versions/0001_initial.py) (enables PostGIS
before creating tables).

---

## Setup & running

### 1. Docker (recommended)
```bash
cp .env.example .env
docker compose up --build
```
The backend entrypoint waits for Postgres, runs Alembic migrations, and seeds demo data
automatically. Then open:

- Frontend: http://localhost:5173
- API docs (Swagger): http://localhost:8000/docs
- Health: http://localhost:8000/health · Metrics: http://localhost:8000/metrics

### 2. Local dev (without Docker)
Requires a running PostGIS + Redis (you can still use `docker compose up postgres redis`).
```bash
# Backend
cd backend
python -m venv .venv && source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt
export DATABASE_URL=postgresql+asyncpg://routeos:routeos_dev_password@localhost:5432/routeos
export DATABASE_URL_SYNC=postgresql+psycopg://routeos:routeos_dev_password@localhost:5432/routeos
export REDIS_URL=redis://localhost:6379/0
alembic upgrade head
python -m scripts.seed_data
uvicorn app.main:app --reload

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

### Environment variables
See [`.env.example`](.env.example) — Postgres/Redis URLs, `SECRET_KEY`, CORS origins,
`USE_OSRM` / `ROAD_DISTANCE_FACTOR` / `AVERAGE_SPEED_KMH` / `SOLVER_TIME_LIMIT_SECONDS`, and demo
credentials. **No secrets are committed**; `.env` is gitignored.

---

## Demo credentials
| Role | Email | Password |
|---|---|---|
| Admin | `admin@routeos.dev` | `admin12345` |
| Dispatcher | `dispatcher@routeos.dev` | `dispatch12345` |
| Viewer | `viewer@routeos.dev` | `viewer12345` |

Seed data: **1 depot (Delhi NCR / Okhla), 20 vehicles, ~150 orders** across Delhi, Gurugram,
Noida, Ghaziabad, Faridabad. All customer data is fictional.

Generate more orders for load testing:
```bash
docker compose exec backend python -m scripts.generate_demo_orders --count 500 --depot 1
```

---

## End-to-end demo (matches the acceptance flow)
1. Log in as **dispatcher**.
2. **Dashboard** shows ~150 pending orders, 20 vehicles, KPIs.
3. **Route Planner** → "Select 50" orders, "Select all vehicles".
4. **Optimize Routes** → OR-Tools runs; map shows polylines + baseline comparison + improvement %.
5. **Accept Plan** → routes persist, orders → `ASSIGNED`, vehicles → `ASSIGNED`.
6. **Live Operations** → **Start Simulation @ 20×** → vehicles move; orders → `OUT_FOR_DELIVERY` → `DELIVERED`.
7. Select an active route → apply **severe** traffic → delay + at-risk warning appears.
8. **Reoptimize** → remaining stops re-sequenced; map + ETAs update.
9. Simulation completes → vehicle returns to depot, route `COMPLETED`, vehicle `AVAILABLE`.
10. **Analytics** reflects completed deliveries and optimization savings.

---

## API documentation
Interactive Swagger/OpenAPI at `/docs`. Route groups: `/api/v1/auth`, `/users`, `/depots`,
`/orders`, `/vehicles`, `/routes`, `/optimization`, `/simulation`, `/analytics`, `/dashboard`,
plus `/ws/fleet`. Errors use a structured envelope:
```json
{ "error": { "code": "ORDER_NOT_FOUND", "message": "Order 1024 does not exist", "details": {} } }
```

## Testing
```bash
cd backend && python -m pytest -q          # 9 tests (real OR-Tools + geospatial)
cd frontend && npx tsc --noEmit && npm run build
```
Optimization tests assert the spec's critical invariants: capacity separation (60kg+50kg not on a
100kg vehicle), depot start/end, no duplicate visits, time-window feasibility, and optimizer ≤ baseline.

## Performance benchmarking
```bash
cd backend && SOLVER_TIME_LIMIT_SECONDS=3 python -m scripts.benchmark --sizes 50 100 250
```
Measured on a 12-vehicle fleet, 3s solver budget (synthetic NCR orders):

| Orders | Solve (ms) | Optimized km | Baseline km | Distance gain | Assigned |
|---:|---:|---:|---:|---:|---:|
| 50 | ~3000 | 261.6 | 346.8 | **24.6%** | 50/50 |
| 100 | ~3000 | 375.0 | 576.4 | **34.9%** | 100/100 |
| 250 | ~3000 | 786.5 | 981.3 | **19.9%** | 250/250 |

Numbers vary with random seed/hardware/time budget — regenerate before quoting.

---

## Design decisions & trade-offs
- **Async FastAPI + threadpool for the solver.** OR-Tools is CPU-bound, so solves run via
  `asyncio.to_thread` to keep the event loop responsive without a separate worker service.
  Production would move this to a task queue (Arq/Celery) — see ARCHITECTURE.
- **Single in-process simulation loop** — simple and correct for one operator/one region;
  the DB remains the source of truth so state survives reconnects.
- **Haversine fallback over hard OSRM dependency** — the project runs fully offline; OSRM is opt-in.
- **Selective Redis caching** — only expensive, reusable read paths; not blind caching.

## Known limitations
- Simulation and traffic are **simulated**, not real GPS — clearly labelled as such.
- Optimization is **heuristic**, not proven-optimal (VRP is NP-hard).
- Single simulation instance and single depot are the tested happy path (multi-depot schema exists).
- No ML/AI is used; the engine is operations-research / constraint optimization.

## Future improvements
Multi-depot optimization, pickup+delivery pairs, EV range constraints, stop transfer between
vehicles, background job queue for very large solves, address geocoding, CSV import/export.

## Resume description
> Built RouteOS, an intelligent logistics & fleet-optimization platform for multi-vehicle delivery
> planning using constraint-based Vehicle Routing Problem optimization (Google OR-Tools),
> PostgreSQL/PostGIS geospatial data, and real-time backend-driven route simulation over
> WebSockets (FastAPI, React/TypeScript, Redis, Docker), with baseline benchmarking that quantified
> ~20–35% reductions in fleet distance.

See [`architecture/ARCHITECTURE.md`](architecture/ARCHITECTURE.md) for the full system design and
scaling strategy.
