# Troubleshooting

Symptom first, then cause. Every error string here is one this codebase
actually produces — grep for it and you will find where.

---

## It will not start

### `docker compose up` fails, or Postgres exits immediately

**Almost always a port clash.** If you already have Postgres or Redis running
locally, ports 5432 and 6379 are taken and the containers cannot bind them.

The override already exists — set it in your `.env`:

```bash
POSTGRES_HOST_PORT=55432
REDIS_HOST_PORT=56379
```

These change only the **host** side of the mapping. Nothing inside the compose
network changes, so `DATABASE_URL` still points at `postgres:5432` and needs no
edit. Check what is holding a port with:

```bash
# Windows
netstat -ano | findstr :5432
# macOS / Linux
lsof -i :5432
```

### The backend container starts, then exits

Read its logs — the entrypoint is deliberately loud:

```bash
docker compose logs backend | head -40
```

| Log line | Means |
|---|---|
| `Waiting for Postgres at …` then a timeout | It cannot reach the database. If the host looks wrong, `DATABASE_URL` is wrong — the entrypoint parses the host out of it. |
| `type "geography" does not exist` | PostGIS is missing. The `postgres` service must be the **postgis** image, not plain `postgres`. |
| `alembic … Target database is not up to date` | Migrations did not run. `docker compose exec backend alembic upgrade head`. |

### The frontend loads but every request fails

Open the browser console. If you see **CORS**, the backend is not allowing your
origin.

There is **no dev proxy** — the browser calls the backend directly, so the
backend's allow-list has to include wherever the frontend is served from. It
defaults to `localhost:5173`. If you moved the dev server, follow it:

```bash
BACKEND_CORS_ORIGINS=http://localhost:5174
```

---

## It runs, but something looks wrong

### The optimizer reports 0% improvement

Look at `plan_source` in the result. If it says `"baseline"`, **this is working
as designed**: the solver did not beat the greedy plan inside its time budget,
so the better plan was dispatched and the improvement over greedy is
correctly ~0.

The Route Planner shows an amber notice when this happens rather than pretending
otherwise. Fixes, in order of effect:

1. Give the solve more time — `SOLVER_ASYNC_TIME_LIMIT_SECONDS` (default 180)
   or `SOLVER_SECONDS_PER_ORDER` (default 1.6).
2. Give the container more CPU. A shared-CPU instance is the usual cause.
3. Use `/optimization/jobs` rather than `/optimization/run` — the synchronous
   endpoint is capped at 15s because a client is waiting.

### Orders come back unassigned

The reason is reported per order. It is a hint, not a diagnosis — the solver
does not say *why* it dropped a node, so the cause is inferred.

| Reason | Means | Do |
|---|---|---|
| `CAPACITY_EXCEEDED` | Heavier than **any** vehicle in the fleet | Split the order, or add a bigger van |
| `NO_AVAILABLE_VEHICLE` | The fleet filled up | Add vehicles, or select fewer orders |
| `TIME_WINDOW_INFEASIBLE` | The window cannot be reached in time | Widen the window |
| `ROUTE_DURATION_EXCEEDED` | Would breach a per-vehicle distance limit | Raise `max_route_distance_km` |

### The map is blank

- **Grey with no tiles** — no internet. Tiles come from OpenStreetMap and the
  Leaflet stylesheet from a CDN; both need a network.
- **Nothing at all, not even grey** — the container has no height. Leaflet
  measures its element to decide which tiles to load, and a height of zero
  renders nothing *with no error*. Every map here needs an ancestor with a real
  height.
- **Broken marker images** — `src/utils/map.ts` was not imported. Importing it
  is what repairs Leaflet's icon URLs under a bundler; it is a module-level side
  effect, so an "unused" import there is load-bearing.

### Vehicles do not move

Check in this order:

1. Are there `ACTIVE` routes? The engine only picks up `PLANNED` or `ACTIVE`
   ones — accept a plan first.
2. Was the simulation started? `POST /simulation/start`, or the button on Live
   Operations. It answers `{"started": false, "reason": …}` when there is
   nothing to run — that is a normal reply, not an error.
3. Is the WebSocket connected? Live Operations shows a green dot. Red means
   positions are not arriving, and the map will sit still even with a running
   simulation.
4. Is a van broken down? A `breakdown` traffic event sets its speed factor to
   zero. Apply `clear` to release it.

### The dashboard numbers look stale

The summary is Redis-cached for **15 seconds** and analytics for **30**.
Accepting a plan explicitly invalidates the dashboard key, so a visible action
is never followed by stale figures — but a change made elsewhere can take a
few seconds to appear.

The frontend adds its own 10-second `staleTime`, so worst case is a short lag.
Mutations invalidate their query keys, which bypasses it.

### The on-time delivery rate is always ~100%

**A known defect, not a coincidence.** The query counts every
`DELIVERY_COMPLETED` event and divides by delivered orders — and the engine
writes one such event per delivery, so the ratio is ~100% by construction. It
is currently a completion rate wearing the wrong label.

Measuring the real thing means comparing `route_stops.actual_arrival` against
`orders.delivery_window_end`. Both are stored, so the data is there. See
`backend/app/services/README.md`.

---

## Errors from the API

Every failure shares one envelope, so a client needs one parser:

```json
{ "error": { "code": "ORDER_IMMUTABLE", "message": "…", "details": {} } }
```

`code` is stable and safe to branch on. `message` is prose and may be reworded.

| Code | Status | Means |
|---|---|---|
| `NOT_AUTHENTICATED` | 401 | No token, or the user is gone or disabled |
| `INVALID_TOKEN` | 401 | Signature bad, malformed, or expired |
| `INVALID_CREDENTIALS` | 401 | Wrong email or password (one message for both, deliberately — so accounts cannot be enumerated) |
| `USER_DISABLED` | 403 | Correct password, `is_active` false |
| `FORBIDDEN` | 403 | Signed in, wrong role. Logging in again will not help |
| `EMAIL_TAKEN` | 409 | Address already registered |
| `NO_ORDERS` / `NO_VEHICLES` | 422 | Nothing eligible matched. Remember the backend only ever considers **PENDING** orders and **AVAILABLE** vehicles — passing ids narrows that set, never widens it |
| `ORDER_IMMUTABLE` | 409 | Cannot edit a `DELIVERED` or `CANCELLED` order |
| `ORDER_IN_PROGRESS` | 409 | Cannot cancel one already out for delivery |
| `ORDER_DELETE_FORBIDDEN` | 409 | Only `PENDING`/`CANCELLED`/`FAILED` orders may be deleted |
| `PLAN_NOT_READY` | 409 | Accepting a run that is not `COMPLETED`, or has no stored plan |
| `ROUTE_NOT_ACTIVE` | 409 | Traffic and re-optimize need a route that is ACTIVE **and** known to the engine. After a restart a route can be the former without the latter |
| `ROUTE_HAS_NO_VEHICLE` | 409 | Its vehicle was deleted — the FK is `SET NULL`, so the route survived |
| `OPTIMIZER_BUSY` | 429 | Concurrent solve cap reached. Each solve pins a CPU for minutes; raise `MAX_CONCURRENT_OPTIMIZATION_JOBS` if the machine can take it |
| `INVALID_SEVERITY` | 422 | Must be `clear`, `moderate`, `severe` or `breakdown` |
| `VALIDATION_ERROR` | 422 | Malformed body. `details.errors` names the offending field |

Every `*_NOT_FOUND` (`ORDER_NOT_FOUND`, `DEPOT_NOT_FOUND`, …) is a 404 with the
id in `details`.

---

## Developing

### `MissingGreenlet`

The one async-SQLAlchemy error you will meet. It means something tried to load
lazily, which async cannot do implicitly.

**Cause, in order of likelihood:**

1. **A relationship was not eager-loaded.** Add `selectinload`:
   ```python
   select(Route).options(selectinload(Route.stops))
   ```
   This is the answer nearly every time.
2. Reading an attribute after a commit in a session that expires on commit —
   already prevented here by `expire_on_commit=False`, but worth knowing.

Note the setting fixes already-loaded **columns**, not unloaded
**relationships**. Relationships still need `selectinload`.

### Alembic wants to drop every table

`Base.metadata` is empty, so autogenerate thinks nothing should exist. The
model modules must be imported for their classes to register — that is what the
apparently-unused `import app.models` in `alembic/env.py` is for. Do not
"clean it up".

### Autogenerate emitted a drop plus an add for a rename

Expected: autogenerate diffs, it does not read intent, and it cannot see that a
column was renamed. **As generated it will delete the data.** Replace the pair
by hand:

```python
op.alter_column("orders", "old_name", new_column_name="new_name")
```

Always read a generated migration before running it.

### A Tailwind class has no effect

Tailwind scans source **text** and generates only what it finds, so a class
assembled at runtime is purged. Write the whole class literally, or use inline
styles — which is why the Leaflet markers in `utils/map.ts` do.

### The frontend build fails but the app ran fine in dev

`npm run build` runs `tsc --noEmit` first, and `npm run dev` does not
typecheck. Run `npm run lint` while developing to catch it earlier.

### Changing a `VITE_*` variable did nothing

Vite inlines `import.meta.env` values at **build** time. Restart `npm run dev`,
or rebuild the image — for Docker they are `--build-arg`, not runtime env vars.

---

## Resetting

```bash
# Wipe the database and reseed from scratch (destroys all data)
docker compose down -v
docker compose up

# Reseed without wiping — safe, and a no-op if a depot already exists
docker compose exec backend python -m scripts.seed_data

# Add more orders for a bigger optimization problem
docker compose exec backend python -m scripts.generate_demo_orders --count 500 --depot 1
```

`seed_data` is idempotent: it bails out if any depot exists, which is why it can
run on every container start without duplicating a fleet. So it will **not**
restore orders you deleted while the depot remains — use
`generate_demo_orders` for that, or `down -v` for a clean slate.

---

## Still stuck

| Check | Where |
|---|---|
| Are Postgres and Redis reachable? | `GET /health` — reports each separately |
| What did the request actually do? | Logs are JSON, one line per request, with an `X-Request-ID` echoed in the response header. Grep that id to see every line for that request |
| Is the solve progressing? | `GET /optimization/runs/{id}`, or watch the `OPTIMIZATION_PROGRESS` WebSocket events |
| What does the API expect? | `/docs` — the interactive OpenAPI explorer, with a working Authorize button |
