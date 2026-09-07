# `app/services/` — business logic

The layer that knows the **rules**. Not "how do I store an order" (that's
`models/`) and not "is this JSON valid" (that's `schemas/`), but:

> *A DELIVERED order may not be edited. A plan becomes real routes only when
> someone accepts it. Traffic may only be applied to a route that is actually
> moving.*

Eight modules, each owning one area. This is the widest importer in the codebase
— see [`../README.md`](../README.md) — and that's expected: combining the lower
layers into a rule is the whole job.

---

## What belongs here

```
   ┌──────────────────────────────────────────────────────────────┐
   │  api/       "is the caller allowed in?"        401 / 403     │
   │  schemas/   "is the input well-formed?"        422           │
   │  services/  "is this action legal right now?"  409 and kin   │  <- here
   │  models/    "what does Postgres enforce?"      constraints   │
   └──────────────────────────────────────────────────────────────┘
```

A weight of `-5` is malformed — a schema rejects it. Editing a DELIVERED order
is perfectly well-formed but forbidden by the *state* of the world, and only a
service can know that:

```python
if order.status in (OrderStatus.DELIVERED, OrderStatus.CANCELLED):
    raise APIError("ORDER_IMMUTABLE", ..., status_code=409)
```

Services raise `APIError` and never touch a `Response`. That's what lets the
same function be called from an HTTP handler, a background job, or a test.

---

## File index

| Module | Owns | Notable |
|---|---|---|
| `order_service.py` | Order CRUD, filtering, radius search | Status rules on edit/cancel/delete |
| `fleet_service.py` | Vehicle CRUD, vehicles nearby | Builds its own PostGIS point |
| `depot_service.py` | Depot CRUD | Keeps `location` in step with lat/lng |
| `route_service.py` | Reading routes | Read-only — routes are *created* by accepting a plan |
| `optimization_service.py` | Solve, compare, persist, accept/discard | The biggest and most interesting |
| `simulation_service.py` | Start/stop, traffic, re-optimization | Wraps the engine |
| `dashboard_service.py` | The KPI tiles | Redis-cached |
| `analytics_service.py` | Charts and aggregations | SQL-side aggregation |

---

## `optimization_service.py` — the one to read

Everything else is CRUD with rules. This module orchestrates a genuinely
multi-stage workflow, and it is worth following end to end.

### Stage 1 — load and validate

`_load_inputs()` resolves the request into real rows, and refuses early:

```
   depot_id  ──> does the depot exist?                    404 if not
   order_ids ──> PENDING orders at that depot only        422 if none
   vehicle_ids ─> AVAILABLE vehicles at that depot only   422 if none
```

Filtering to `PENDING` and `AVAILABLE` is a rule, not a convenience: you must
not re-plan an order already on a van, nor dispatch a vehicle already out.

### Stage 2 — solve twice, on a thread

```python
optimized = await asyncio.to_thread(solve_vrp, ...)
baseline  = await asyncio.to_thread(nearest_neighbour, ...)
```

`asyncio.to_thread` is essential. OR-Tools is CPU-bound C++ that does not yield;
calling it directly on the event loop would freeze *every* request — including
the health check — for the whole budget. Handing it to a worker thread keeps the
loop free.

### Stage 3 — keep the better plan

The part that would look like a bug without explanation: we run a *dumb*
algorithm alongside the smart one and are willing to ship its answer.

```python
chosen, plan_source = _better_plan(baseline, optimized, len(orders))
```

The reason is that the solver is a heuristic under a wall-clock budget. Given
too little CPU for the problem size, guided local search can time out while
still *worse* than greedy — and shipping that would mean dispatching a plan a
human with a map would have beaten. Running both and keeping the winner is
standard portfolio practice.

Ranking uses two tests, in order:

```
   1. Does one plan serve MORE orders?          -> that one wins.
      A cheaper plan that strands deliveries is not better.

   2. Otherwise compare cost:
         cost = total_distance_km + 300 × vehicles_used
      Ties go to the solver.
```

The `300` is not arbitrary — it mirrors `VEHICLE_FIXED_COST` (300,000
metre-equivalents) inside the solver's own objective. Using the same exchange
rate means both plans are judged by the trade-off the solver was *optimising
for*, rather than by a different yardstick that could pick the plan the model
considers worse.

`plan_source` is recorded in the result. Seeing `"baseline"` is a real
operational signal: the solver failed to beat greedy in its budget, so raise the
time limit or give the service more CPU.

### Stage 4 — persist, don't apply

A completed run stores the whole plan as JSONB in
`optimization_runs.result_payload` and changes **nothing** operationally. No
route rows, no order status changes.

```
   solve ──> OptimizationRun(COMPLETED, result_payload={...})
                            │
                            │   a human looks at it
                     ┌──────┴───────┐
                     ▼              ▼
                  accept          discard
```

That is what makes review possible: a dispatcher sees the proposal, the baseline
comparison and the unassigned list *before* anything is committed. Because the
full plan is stored, accepting is a pure state transition — no re-solve, and no
risk of getting a different answer the second time (the search is time-bounded,
so a re-run genuinely could differ).

### Stage 5 — accept

`accept_plan()` turns stored JSON into live rows:

```
   for each planned route:
       create Route(status=PLANNED)
       flush                        <- needed: we need route.id for the stops
       for each stop:
           create RouteStop(estimated_arrival = horizon + eta_minutes)
           order.status = ASSIGNED
       vehicle.status = ASSIGNED
       vehicle.current_load_kg = the planned load
   commit                           <- all of it, or none of it
```

Two details:

- **`horizon`** is stored in the payload, so relative ETAs ("47 minutes in")
  become absolute timestamps against the same origin the solver used.
- **One commit at the end.** Routes, stops, order statuses and vehicle statuses
  land as a single transaction, so a failure halfway cannot leave orders marked
  ASSIGNED with no route to be on.

### Background jobs

`/run` solves inline; `/jobs` returns `202` immediately and solves in the
background. The second exists because a good solve wants minutes:

```
   POST /jobs
      │
      ├─ validate, save a PROCESSING row, return 202
      │
      └─ asyncio.create_task(_run_job)
               │
               ├─ heartbeat task: broadcast progress every 5s
               │
               ├─ opens its OWN DB session
               │     the request's session is gone by now
               │
               └─ solve -> save -> broadcast OPTIMIZATION_COMPLETED
```

Three things in here are defensive, and each guards a specific failure:

1. **`_JOBS: set[asyncio.Task]`** — asyncio holds only *weak* references to
   tasks, so a bare `create_task` can be garbage-collected mid-solve. Keeping a
   strong reference in a module-level set (and discarding it in a done-callback)
   is the documented fix.
2. **`max_concurrent_optimization_jobs`** — each solve pins a core for minutes.
   Without a cap, repeated clicks on a public demo pile up CPU-bound work and
   starve both the solves and the API. Over the cap returns `429`.
3. **The progress percentage is honest.** The budget is a fixed wall-clock
   limit, so `elapsed / budget` is a true completion fraction rather than a
   guess — capped at 99 so it never claims to be done early.

The budget itself scales with problem size:

```python
budget = clamp(order_count × 1.6, min=30s, max=180s)
```

Search difficulty grows with stop count, so a flat budget would make a 20-order
run sit waiting long after it converged.

---

## `simulation_service.py` — wrapping the engine

Mostly thin pass-throughs to `simulation/engine.py`, plus two real pieces of
logic.

**`apply_traffic`** validates that the route is ACTIVE *and* known to the engine,
sets the speed factor, then estimates the knock-on delay and flags deliveries
that will now miss their window — broadcast as `ROUTE_DELAYED`.

**`reoptimize_route`** is the interesting one: re-plan a route that is already
half-driven.

```
   route 7, mid-delivery
   ┌─────┬─────┬─────┬─────┬─────┐
   │  1  │  2  │  3  │  4  │  5  │
   │ DONE│ DONE│ PEND│ PEND│ PEND│
   └─────┴─────┴──┬──┴─────┴─────┘
                  │  only these are re-solved
                  ▼
   a single-vehicle VRP, starting from the vehicle's
   CURRENT GPS POSITION — not the depot
                  │
                  ▼
   pending stops get new sequence numbers, continuing
   from the highest completed one; the engine reloads
   the route on its next tick
```

Starting from the live position rather than the depot is what makes the result
usable: the van is where it is, and a plan that assumes otherwise is fiction.
Completed stops keep their sequence numbers and their history.

Note the eager `selectinload(Route.vehicle)` in that query. It is load-bearing,
and commented as such: the vehicle is read later to get its position, and a lazy
load at that point raises `MissingGreenlet` under async.

---

## Caching: dashboard and analytics

Only two services cache, and only their expensive aggregations:

| Key | TTL | Why |
|---|---|---|
| `routeos:dashboard:summary` | 15 s | ~9 COUNT/SUM queries, read on every page load |
| `routeos:analytics:summary:{start}:{end}` | 30 s | heavier still, and date-ranged |

The TTLs are short on purpose: an operations dashboard may be a few seconds
stale, but not a minute. `optimization.py`'s accept handler additionally
invalidates the dashboard key explicitly, because accepting a plan changes the
KPIs immediately and waiting out the TTL would show stale numbers right after a
visible action.

Everything else is uncached — including the four other analytics endpoints
(`orders_by_status`, `distance_by_vehicle`, `deliveries_over_time`,
`optimization_savings`). Each is a single indexed `GROUP BY`, so they are cheap;
caching them would add invalidation complexity for very little gain.

### Aggregation happens in SQL

```python
select(Order.status, func.count(Order.id)).group_by(Order.status)
```

Not "fetch all orders and count them in Python". The difference is a few bytes
over the wire versus every row in the table, and it is the single most important
habit in this layer. `deliveries_over_time` goes further, using
`func.date_trunc("day", ...)` plus a `CASE` so Postgres does the day-bucketing
and the conditional count in one pass.

---

## Two things to be aware of

**1. `on_time_delivery_rate` measures promises kept, not deliveries made.**

It joins each completed stop to its order and compares `actual_arrival` against
`delivery_window_end` — which is why `route_stops` keeps the actual arrival
beside the solver's estimate.

Two exclusions are deliberate: a stop with no `actual_arrival` has not been
delivered yet, and an order with no window was never promised anything. So the
figure answers *"of what we promised, how much did we hit"* rather than *"how
much did we deliver"*.

It used to do the latter by accident — counting every `DELIVERY_COMPLETED`
event over delivered orders, which is the same quantity twice and so reported
~100% regardless. `tests/test_dashboard_api.py` now pins the real behaviour.

**2. `distance_per_delivery_km` reads oddly but is correct.**

```python
avg_route_distance / max(1, delivered) * total_completed
```

Rearranged, that is `(avg_route_distance × total_completed) / delivered` —
total distance across completed routes, per delivery. Correct, but the operand
order makes it look like a mistake.
