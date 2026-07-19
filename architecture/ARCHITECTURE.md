# RouteOS — Architecture

This document explains how RouteOS is built and how it would evolve from a single-region demo
(hundreds of deliveries/day) to a global system (millions/day). It clearly separates **what is
implemented today** from **how production scale would evolve**.

---

## 1. System architecture

```
        ┌───────────────────────┐
        │   React + TypeScript  │  Vite · Tailwind · TanStack Query · Zustand
        │  Dashboard / Maps / …  │  Leaflet (OSM) · Recharts
        └───────────┬───────────┘
             REST (JSON)  +  WebSocket (/ws/fleet)
        ┌───────────▼───────────┐
        │        FastAPI        │  request-id middleware · JSON logs · /metrics
        │      API layer        │  JWT auth · RBAC · structured errors
        └───────────┬───────────┘
     ┌──────────────┼───────────────┬────────────────┐
     ▼              ▼               ▼                ▼
 Order/Fleet   Optimization    Simulation      Analytics/Dashboard
 services       service         engine          services
     └──────────────┼───────────────┴────────────────┘
             ┌───────▼────────┐          ┌─────────────┐
             │ PostgreSQL +   │          │   Redis     │
             │   PostGIS      │          │ cache + live│
             └────────────────┘          └─────────────┘
```

**Layering is strict**: API routers only validate/authorize and delegate; business logic lives in
`services/`; persistence in models + SQLAlchemy; optimization, geospatial, simulation and websocket
concerns are isolated modules. No business logic in route handlers.

**Implemented today:** one FastAPI process hosting API + WebSocket + the asyncio simulation loop;
one Postgres, one Redis, one nginx-served frontend — all via Docker Compose.

---

## 2. Database architecture

- Relational core: `users, depots, vehicles, orders, routes, route_stops, optimization_runs,
  vehicle_location_history, delivery_events`.
- **PostGIS** geography columns (`SRID 4326`) on `depots` and `orders`, GiST-indexed.
- Secondary B-tree indexes on `status`, `priority`, foreign keys and `created_at` for the common
  filter/paginate paths.
- Enums stored as strings for readability and forward-compatibility.
- `optimization_runs.result_payload` (JSONB) stores the full plan so **Accept/Discard** is a pure
  state transition — no recompute.
- Migrations via **Alembic**; the initial migration enables the PostGIS extension **before**
  creating any geography column.

**Scale evolution:** partition `orders`, `vehicle_location_history` and `delivery_events` by
time and/or region/depot; move high-write telemetry to a time-series store; add read replicas for
analytics; archive cold history to object storage.

---

## 3. API architecture

- Versioned REST under `/api/v1`; resource-oriented routes with `GET/POST/PATCH/DELETE`.
- Pagination + filtering on list endpoints; Pydantic v2 request/response validation.
- Uniform error envelope `{ "error": { code, message, details } }` and correct status codes.
- JWT bearer auth; `require_roles(...)` dependency enforces RBAC (ADMIN ⊇ DISPATCHER ⊇ VIEWER).
- Observability: per-request ID + latency in JSON logs; Prometheus `/metrics`; `/health` checks DB+Redis.

**Scale evolution:** put the API behind a gateway/load balancer, run N stateless replicas, add rate
limiting and API keys for machine clients, and split read/write paths.

---

## 4. Optimization workflow

```
orders + vehicles + depot
        │
        ▼  build coordinate list (depot=0, orders=1..n)
Distance/Duration matrix  ── OSRM /table  ──(fallback)── Haversine × road factor
        │
        ▼  OrderNode / VehicleInput (demands, windows, priorities, limits)
OR-Tools RoutingModel
   • arc cost by objective        • capacity dimension
   • per-vehicle distance cap     • time-window dimension (+ service time)
   • priority-scaled disjunctions • vehicle fixed cost
        │  PATH_CHEAPEST_ARC → GUIDED_LOCAL_SEARCH (time-limited)
        ▼
SolveResult (routes, stops+ETAs, unassigned+reasons, objective value)
        │  + nearest-neighbour baseline for comparison
        ▼
OptimizationRun persisted (metrics, improvement %, result_payload)
        │  Accept →
Route + RouteStop rows; orders→ASSIGNED; vehicles→ASSIGNED
```

The CPU-bound solve runs in a **worker thread** (`asyncio.to_thread`) so the event loop stays
responsive. **Scale evolution:** move solves to a dedicated worker pool / task queue
(Arq/Celery with Redis broker), shard problems **per depot/region**, cache routing matrices, and
run large instances on bigger compute with longer budgets.

---

## 5. Geospatial architecture

- Coordinates persisted both as plain lat/lng (for cheap serialization) and as PostGIS geography
  (for spatial queries), kept in sync on write.
- Radius search via `ST_DWithin` (index-friendly) + `ST_Distance` ordering.
- Distance/time matrices abstract OSRM vs Haversine behind one interface.

**Scale evolution:** precompute/caches distance matrices per region; adopt a dedicated routing
cluster (self-hosted OSRM/Valhalla) with tiled data; use geohash/region sharding.

---

## 6. Real-time WebSocket flow

```
SimulationEngine tick ──► mutate DB state ──► ConnectionManager.broadcast(event)
                                                     │
                                        all /ws/fleet clients
                                                     │
                                        React updates markers/feed
```

Events: `VEHICLE_LOCATION_UPDATED`, `ORDER_STATUS_UPDATED`, `ROUTE_STATUS_UPDATED`,
`DELIVERY_COMPLETED`, `ROUTE_DELAYED`, `ROUTE_REOPTIMIZED`, plus a `SNAPSHOT` on connect. The
frontend never fabricates movement — it only renders backend truth, and reconnects with backoff.

**Scale evolution:** replace the in-process manager with **Redis Pub/Sub or Kafka** so any API
replica can publish and every socket server can subscribe; add a presence/room model per
region/operator; offload to a managed pub/sub or a socket gateway.

---

## 7. Simulation architecture

- One asyncio loop advances every ACTIVE vehicle along `depot → stops → depot` at
  `AVERAGE_SPEED_KMH × speed_multiplier × traffic_factor`.
- On reaching a stop: order→DELIVERED, stop→COMPLETED, `progress_stop_index` advances, events fire,
  periodic `vehicle_location_history` snapshots persist.
- On returning to depot: route→COMPLETED (records actual duration), vehicle→AVAILABLE.
- Traffic sets a per-route speed factor; **re-optimization** re-solves only PENDING stops from the
  vehicle's current position and the loop reloads the route on its next tick.

**Scale evolution:** replace the simulator with real GPS ingestion (MQTT/HTTP → stream), one
consumer group per region; the same event contract drives the same frontend.

---

## 8. Redis usage
- **Cache**: dashboard summary + analytics aggregations (short TTL, explicit invalidation on writes).
- **Live/ephemeral state & fan-out** for the fleet.
- Deliberately **not** a cache-everything layer — only expensive, reusable, read-heavy payloads.

**Scale evolution:** separate cache vs broker vs pub/sub instances; add cache-aside with
versioned keys and stampede protection.

---

## 9. Failure handling
- Cache reads/writes never break the request path (best-effort try/except).
- OSRM failure transparently falls back to Haversine.
- Optimization failures mark the run `FAILED` with a message; DB writes use transactions.
- WebSocket clients auto-reconnect; dead sockets are pruned on broadcast.
- `/health` degrades (not 500s) when DB/Redis are down so orchestrators can react.

**Scale evolution:** circuit breakers around routing providers, idempotent job processing with
retries + dead-letter queues, graceful draining on deploy, multi-AZ Postgres with failover.

---

## 10. Scaling strategy — 100/day → 1,000,000/day

| Concern | Today (implemented) | Production evolution |
|---|---|---|
| API | 1 FastAPI process | N stateless replicas behind LB/gateway |
| Optimization | threadpool solve, sync response | task queue + worker pool, per-region sharding, matrix cache |
| Realtime | in-process WebSocket manager | Redis/Kafka pub-sub, dedicated socket tier |
| Data | single Postgres+PostGIS | read replicas, time/region partitioning, TS store for telemetry |
| Routing | OSRM public / Haversine | self-hosted OSRM/Valhalla cluster, tiled + cached |
| Simulation | asyncio loop | real GPS stream consumers per region |
| Caching | single Redis | segmented cache/broker/pubsub clusters |
| Deploy | Docker Compose | containers on an orchestrator (**Kubernetes only at large scale**) |

**Partition by region/depot** is the primary horizontal axis: VRP is solved per depot, telemetry
and routes shard by region, and pub/sub rooms are per region — so the system scales close to
linearly by adding regions/workers rather than by re-architecting.

We deliberately **do not** over-engineer the local build: it stays a single `docker compose up`.
Kubernetes, Kafka, and read replicas are described as the growth path, not shipped prematurely.
