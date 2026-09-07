# `app/api/routes/` — the endpoint modules

Twelve modules, one per resource. Each declares an `APIRouter`, and `main.py`
mounts them all under `/api/v1` (except `health` and `ws`).

The **endpoint catalogue** — every path, method and required role — is in
[`../README.md`](../README.md). This file is about the conventions the modules
share, so a new one looks like the existing ones.

---

## The shape of a module

Every file follows the same six-line preamble:

```python
from __future__ import annotations          # 1. postponed annotations

router = APIRouter(prefix="/orders", tags=["orders"])   # 2. prefix + tag
_manage = require_roles(UserRole.DISPATCHER)            # 3. bind the guard once

@router.get("", response_model=Page[OrderOut])          # 4. declare
async def list_orders(
    db: AsyncSession = Depends(get_db),                 # 5. inject
    _=Depends(get_current_user),
):
    return await order_service.list_orders(db, ...)     # 6. delegate
```

| Convention | Why |
|---|---|
| `prefix` on the router | the path appears once, not on every decorator |
| `tags=[...]` | groups the endpoints into sections in `/docs` |
| guard bound to a module-level `_name` | one place to change who may write |
| `response_model=` on every route | trims the output *and* documents it in OpenAPI |
| `from __future__ import annotations` | lets `X | None` work as a type hint on older runtimes |

The router declares only its **own** prefix. `/api/v1` is applied by `main.py`,
so the API version lives in exactly one place and a new version means one line
there rather than twelve edits.

---

## File index

| Module | Delegates to | Shape |
|---|---|---|
| `health.py` | *nothing* — reads live objects | public, unauthenticated |
| `auth.py` | `core/security` + direct query | register / login / me |
| `users.py` | direct query | admin-only list |
| `depots.py` | `depot_service` | full CRUD |
| `orders.py` | `order_service` | full CRUD + paging + `/nearby` + `/cancel` |
| `vehicles.py` | `fleet_service` | full CRUD + `/nearby` |
| `routes.py` | `route_service` | read-only |
| `optimization.py` | `optimization_service` | run / jobs / accept / discard |
| `simulation.py` | `simulation_service` | start / stop / speed / traffic / reoptimize |
| `analytics.py` | `analytics_service` | five read-only aggregations |
| `dashboard.py` | `dashboard_service` | summary + activity |
| `ws.py` | `websocket/manager` + engine | the WebSocket |

`orders.py` is the fullest example of the pattern — read it first.

---

## Delete permissions are asymmetric on purpose

Not an oversight, and it lines up with the database's cascade rules:

| Endpoint | Role | Because |
|---|---|---|
| `DELETE /orders/{id}` | **dispatcher** | one row of day-to-day operational data |
| `DELETE /vehicles/{id}` | **admin** | infrastructure — and routes referencing it survive with `vehicle_id = NULL` |
| `DELETE /depots/{id}` | **admin** | `CASCADE` takes its vehicles, orders *and* routes with it |

Deleting a depot is the most destructive operation the API offers — see the
delete-rules table in [`../../models/README.md`](../../models/README.md) — so it
sits behind the highest role. Note these two use `Depends(require_roles(ADMIN))`
inline rather than the module's `_manage`, which is what makes the exception
visible at the call site.

`orders.py` also offers `POST /{id}/cancel` alongside `DELETE`. They are
different acts: cancel is a *state transition* that keeps the record and its
history; delete removes it, and the service only permits that for
PENDING/CANCELLED/FAILED orders.

---

## Two patterns worth recognising

**1. Composing a response model from another.**

`/nearby` returns an order *plus* its distance. Rather than redefine every
field, the handler spreads one model into the other:

```python
NearbyOrder(**OrderOut.model_validate(o).model_dump(), distance_km=round(d, 3))
```

`NearbyOrder` simply subclasses `OrderOut` and adds `distance_km`, so the two
can never drift apart. The same pattern appears in `vehicles.py` with
`NearbyVehicle`.

**2. Inline request bodies for non-resource actions.**

Most bodies live in `schemas/`. `simulation.py` declares two locally:

```python
class SpeedBody(BaseModel):
    speed_multiplier: float = 1.0

class TrafficBody(BaseModel):
    route_id: int
    severity: str          # clear | moderate | severe | breakdown
```

They are control-plane commands, not persisted resources — nothing else needs
them, so keeping them next to their only use is clearer than a file in
`schemas/`. That said, `severity: str` is validated only inside the service
(which raises `INVALID_SEVERITY`); as a `Literal["clear", "moderate", "severe",
"breakdown"]` it would be rejected at the schema boundary instead, and would
show the valid values in `/docs`.

---

## Where cache invalidation happens

`optimization.py`'s accept handler is the one route that touches the cache
directly:

```python
routes = await optimization_service.accept_plan(db, run_id)
await cache_invalidate("routeos:dashboard:summary")
```

Accepting a plan changes the dashboard KPIs immediately, and the summary is
cached for 15 seconds — long enough that a dispatcher would click Accept and see
stale numbers. Invalidating here means the next page load recomputes.

It sits in the route rather than the service because it is a *presentation*
concern: the service's job is to make the plan real, not to know which caches a
UI keeps.

---

## Adding a module

1. Create `app/api/routes/thing.py` with a prefixed, tagged router.
2. Put the logic in `app/services/thing_service.py` — **not** in the handler.
3. Define `ThingCreate/Update/Out` in `app/schemas/thing.py`.
4. Register it in `main.py`: `app.include_router(thing.router, prefix=API)`.
5. Declare literal paths **before** parameterised ones (`/nearby` before
   `/{id}`) — see the note in [`../README.md`](../README.md).

Step 4 is the one that's easy to miss: without it the module imports cleanly,
the tests pass, and the endpoints simply do not exist.
