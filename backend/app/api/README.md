# `app/api/` — the edge of the system

Where HTTP meets Python. Everything a client can reach is declared here, and
**nothing else**: no business rules, no SQL, no algorithms.

```
   app/api/
   ├── routes/        one module per resource — the endpoints
   └── dependencies/  reusable pre-checks — currently just auth
```

---

## A handler does exactly four things

That is the rule that keeps this layer thin. Read any handler and you should see
these four steps and no fifth:

```python
@router.patch("/{order_id}", response_model=OrderOut)          # 1. declare
async def update_order(
    order_id: int,
    data: OrderUpdate,                                          # 2. validate
    db: AsyncSession = Depends(get_db),
    _=Depends(_manage),                                         # 3. authorize
):
    return await order_service.update_order(db, order_id, data) # 4. delegate
```

1. **Declare** the path, method and response shape.
2. **Validate** the input — by naming a schema type; Pydantic does the work.
3. **Authorize** — by naming a dependency; it raises before the body runs.
4. **Delegate** to a service. One call, usually one line.

Notice what is *absent*: no `try/except` (the handlers in `core/errors.py` catch
`APIError` globally), no manual permission `if`, no query building. When a
handler grows past a few lines it is usually because a business rule leaked into
it, and it belongs in a service.

---

## Request pipeline

Each gate can reject, and the ones that do never reach your code:

```
   HTTP request
        │
        ▼
   ┌──────────────────────────────────────────┐
   │ main.py middleware                       │  request id + latency timer
   └──────────────────┬───────────────────────┘
                      ▼
   ┌──────────────────────────────────────────┐
   │ CORS                                     │  wrong origin ──> blocked
   └──────────────────┬───────────────────────┘
                      ▼
   ┌──────────────────────────────────────────┐
   │ routing: does this path exist?            │  no ──> 404
   └──────────────────┬───────────────────────┘
                      ▼
   ┌──────────────────────────────────────────┐
   │ dependencies/auth.py                      │  no token   ──> 401
   │   decode JWT -> load user -> check role   │  wrong role ──> 403
   └──────────────────┬───────────────────────┘
                      ▼
   ┌──────────────────────────────────────────┐
   │ schemas: parse and validate the body      │  malformed ──> 422
   └──────────────────┬───────────────────────┘
                      ▼
             the handler body runs
                      │
                      ▼
              a service does the work        rule broken ──> APIError (4xx)
                      │
                      ▼
   ┌──────────────────────────────────────────┐
   │ response_model trims the output           │
   └──────────────────┬───────────────────────┘
                      ▼
              JSON + X-Request-ID
```

---

## Permissions

Three roles, in a strict ladder:

```
   ADMIN        everything (bypasses every check)
     │
   DISPATCHER   can change operational data:
     │          orders, vehicles, depots, optimization, simulation
     │
   VIEWER       read-only: lists, detail pages, dashboard, analytics
```

`require_roles(...)` builds a dependency, and its first line is the ladder:

```python
if user.role == UserRole.ADMIN:
    return user          # admin short-circuits every guard
```

So `require_roles(UserRole.DISPATCHER)` means *"dispatcher or admin"*, and no
endpoint needs to list ADMIN explicitly. Route modules bind it once at the top
and reuse it:

```python
_manage = require_roles(UserRole.DISPATCHER)   # orders.py
_dispatch = require_roles(UserRole.DISPATCHER) # optimization.py, simulation.py
```

Two guards exist in total:

| Dependency | Means |
|---|---|
| `get_current_user` | must be logged in; any role will do |
| `require_roles(R)` | must be role `R` — or ADMIN |

`oauth2_scheme` is created with `auto_error=False` on purpose: FastAPI's default
would raise its own bare 401 before our code runs, bypassing the error envelope.
Turning it off lets `get_current_user` raise `APIError` instead, so an auth
failure looks like every other error.

---

## Endpoint catalogue

45 endpoints. All are under `/api/v1` except the three at the bottom.

### Auth — `/auth`
| Method | Path | Who | Does |
|---|---|---|---|
| POST | `/register` | public | Create an account |
| POST | `/login` | public | Email + password → JWT |
| GET | `/me` | logged in | The current user |

Login takes an OAuth2 **form**, not JSON, and the field is called `username`
even though we treat it as an email — that's the OAuth2 password-flow spec, and
following it is what makes the `/docs` Authorize button work.

### Orders — `/orders`
| Method | Path | Who | Does |
|---|---|---|---|
| GET | `` | logged in | Paginated list; filter by status, priority, depot, date, assigned, free-text search |
| GET | `/nearby` | logged in | PostGIS radius search, nearest first |
| GET | `/{id}` | logged in | One order |
| POST | `` | dispatcher | Create |
| PATCH | `/{id}` | dispatcher | Partial update |
| POST | `/{id}/cancel` | dispatcher | Cancel (a state change, not a delete) |
| DELETE | `/{id}` | dispatcher | Delete — only if PENDING/CANCELLED/FAILED |

### Vehicles — `/vehicles`
Same shape: list (filter by status/depot), `/nearby`, get, create, patch, delete.

### Depots — `/depots`
List, get, create, patch, delete.

### Routes — `/routes`
| Method | Path | Who | Does |
|---|---|---|---|
| GET | `` | logged in | List, optionally by status |
| GET | `/{id}` | logged in | One route, stops included |

**Read-only by design.** There is no POST — a route can only come into existence
by accepting an optimization plan.

### Optimization — `/optimization`
| Method | Path | Who | Does |
|---|---|---|---|
| POST | `/run` | dispatcher | Solve **synchronously**; the client waits |
| POST | `/jobs` | dispatcher | Queue a solve, return `202` at once |
| GET | `/runs` | dispatcher | Run history |
| GET | `/runs/{id}` | dispatcher | One run, plan included |
| POST | `/runs/{id}/accept` | dispatcher | Turn the plan into real routes |
| POST | `/runs/{id}/discard` | dispatcher | Throw it away |

Two ways to solve, on purpose. `/run` is simple and fine for small problems;
`/jobs` exists because a good solve wants minutes, which is longer than an HTTP
request should live. See [`../services/README.md`](../services/README.md).

### Simulation — `/simulation`
| Method | Path | Does |
|---|---|---|
| GET | `/status` | Who is moving, and where |
| POST | `/start` / `/stop` | Run the clock |
| POST | `/speed` | 1×–60× |
| POST | `/traffic` | Slow a route down, or break it entirely |
| POST | `/routes/{id}/reoptimize` | Re-sequence the stops not yet delivered |

All dispatcher-only.

### Analytics — `/analytics`, Dashboard — `/dashboard`
Read-only aggregations: summary (date-filterable), orders by status, distance by
vehicle, deliveries over time, optimization savings; dashboard summary and
recent activity.

### Users — `/users`
| GET | `` | **admin only** | List all users |

### Outside `/api/v1`
| Path | Does |
|---|---|
| `/health` | Postgres + Redis reachability, live counts. Unauthenticated — a load balancer has no token |
| `/metrics` | Prometheus scrape |
| `/ws/fleet` | The live event stream |

---

## Route order is load-bearing

In `orders.py` and `vehicles.py`, `/nearby` is declared **before** `/{id}`:

```python
@router.get("/nearby")        # must come first
@router.get("/{order_id}")    # would otherwise swallow it
```

FastAPI matches in declaration order, and `/{order_id}` matches *any* single
segment. Declared the other way round, `GET /orders/nearby` would bind
`order_id="nearby"`, fail to coerce it to `int`, and return a confusing 422
about a path parameter the caller never sent.

**Rule: literal paths before parameterised ones.**

---

## The WebSocket endpoint is different

`ws.py` is the one file that doesn't follow the four-step shape, because a socket
is not a request/response:

```
   client connects
        │
        ▼  manager.connect(ws)  — registered as a listener
   send SNAPSHOT (current state, so the map can draw immediately)
        │
        ▼
   loop: await ws.receive_text()
        │
        │   inbound messages are ignored. The await exists to keep the
        │   connection open and to notice a disconnect.
        │   Everything the client actually wants is pushed to it by
        │   the simulation engine via websocket/manager.py.
        ▼
   disconnect ──> manager.disconnect(ws)
```

The snapshot-then-stream shape matters: a client joining mid-simulation gets the
current world immediately instead of waiting for the next event to learn that
anything exists.

---

## File index

| File | Prefix | Guard |
|---|---|---|
| `dependencies/auth.py` | — | *defines* `get_current_user`, `require_roles` |
| `routes/health.py` | none | public |
| `routes/auth.py` | `/auth` | mixed |
| `routes/users.py` | `/users` | admin |
| `routes/depots.py` | `/depots` | read: any · write: dispatcher |
| `routes/orders.py` | `/orders` | read: any · write: dispatcher |
| `routes/vehicles.py` | `/vehicles` | read: any · write: dispatcher |
| `routes/routes.py` | `/routes` | any (read-only) |
| `routes/optimization.py` | `/optimization` | dispatcher |
| `routes/simulation.py` | `/simulation` | dispatcher |
| `routes/analytics.py` | `/analytics` | any |
| `routes/dashboard.py` | `/dashboard` | any |
| `routes/ws.py` | `/ws` | none (see note below) |

Every router is registered in `main.py`, which is also where the `/api/v1`
prefix is applied — the modules themselves declare only their own prefix, so the
API version lives in exactly one place.

**A known gap:** `/ws/fleet` takes no token. Browsers cannot set an
`Authorization` header on a WebSocket handshake, so the usual dependency does not
apply; the accepted fix is a short-lived ticket passed as a query parameter. Since
the stream is read-only fleet telemetry, this is noted rather than hidden.
