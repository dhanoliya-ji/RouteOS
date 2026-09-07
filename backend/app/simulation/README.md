# `app/simulation/` — the vehicle-movement clock

One file, one class, one loop. It makes vehicles *move*: every second it advances
each active vehicle along its route, writes the new state to the database, and
announces what happened over the WebSocket.

Without this, RouteOS could plan routes but nothing would ever be delivered.

---

## The rule that shapes everything: the backend is authoritative

The frontend **never invents movement**. It draws exactly what it is told.

```
   ┌──────────────────────────────────────────────────────────┐
   │  engine tick (backend)                                   │
   │    move the vehicle · write the row · broadcast          │
   └───────────────────────────┬──────────────────────────────┘
                               │  VEHICLE_LOCATION_UPDATED
                               ▼
   ┌──────────────────────────────────────────────────────────┐
   │  browser: put the marker at the coordinates received      │
   └──────────────────────────────────────────────────────────┘
```

The tempting alternative — let the browser animate between waypoints — is what
makes a demo a lie: two tabs would disagree, a refresh would jump, and the
database would not match the map. Doing the movement server-side means the map,
the order statuses and the analytics are all views of the same rows.

---

## What one tick does

`TICK_SECONDS = 1.0` — one real second per tick.

```
   every 1 second:
      open ONE database session for the whole tick
      for each vehicle not yet finished:
          1. how far did it travel?
                 km = AVERAGE_SPEED_KMH
                      × (1s × speed_multiplier / 3600)
                      × speed_factor
          2. walk it that far along its waypoint list
                 crossing a waypoint fires an arrival
          3. write the new lat/lng onto the vehicle row
          4. maybe append a location-history breadcrumb
          5. broadcast VEHICLE_LOCATION_UPDATED
      commit once
```

The two multipliers are different things, and both matter:

| Factor | Set by | Means |
|---|---|---|
| `speed_multiplier` | the user, 1×–60× | how fast the *clock* runs — a demo dial |
| `speed_factor` | traffic events | how fast the *vehicle* goes — 1.0 clear, 0.6 moderate, 0.35 severe, **0.0 breakdown** |

A breakdown is simply a speed factor of zero: the vehicle stops being advanced
but stays in the simulation, keeping its position and its pending stops.

One session per tick, one commit per tick — not per vehicle. Twenty vehicles
moving is one transaction, not twenty.

---

## Movement is interpolation along a waypoint list

A route becomes a flat list of points, depot at both ends:

```
   waypoints = [ depot, stop₁, stop₂, stop₃, depot ]
                   0      1      2      3      4

   position is tracked as two numbers:
      seg_index      which segment we are on   (0 = depot -> stop₁)
      dist_into_seg  how far along it, in km

   ┌────────────────────────────────────────────────┐
   │  depot ●───────────────●  stop₁                │
   │        │◄── 2.4 km ───►│                        │
   │        │      ▲                                 │
   │        │      └─ dist_into_seg = 1.8            │
   │                    fraction = 1.8 / 2.4 = 0.75  │
   │                                                 │
   │  lat = depot.lat + (stop₁.lat - depot.lat)×0.75 │
   │  lng = depot.lng + (stop₁.lng - depot.lng)×0.75 │
   └────────────────────────────────────────────────┘
```

Straight **linear interpolation** between consecutive points. Vehicles therefore
travel in straight lines, not along roads — a deliberate simplification, and the
reason `haversine_km` is used here without the `1.25` road factor: the engine is
measuring the line it actually draws.

If a tick's distance overshoots the current segment, the loop consumes the
remainder and continues into the next — so a fast `speed_multiplier` can cross
several stops in one tick and fire each arrival in turn.

---

## What happens at a waypoint

Crossing a waypoint is where database state actually changes:

```
                    reached a waypoint
                           │
              ┌────────────┴─────────────┐
              ▼                          ▼
      it has an order_id           it is the final depot
      ── a delivery ──             ── route finished ──
              │                          │
   order.status  = DELIVERED      route.status = COMPLETED
   stop.status   = COMPLETED      route.completed_at = now
   stop.actual_arrival = now      route.actual_duration_minutes
   route.progress_stop_index         = completed - started
        = stop.stop_sequence      vehicle.status = AVAILABLE
   + DeliveryEvent row            vehicle.current_load_kg = 0
              │                          │
   broadcast ORDER_STATUS_UPDATED  broadcast ROUTE_STATUS_UPDATED
   broadcast DELIVERY_COMPLETED    sim.finished = True
```

Recording `actual_arrival` next to the stop's `estimated_arrival` is what makes
after-the-fact schedule analysis possible — the estimate came from the solver,
the actual came from here.

---

## Two state machines

**A vehicle:**

```
   AVAILABLE ──accept a plan──> ASSIGNED ──simulation starts──> IN_TRANSIT
       ▲                                                            │
       └──────────────── returns to depot ──────────────────────────┘

   (MAINTENANCE and OFFLINE exist in the enum but are set by hand,
    not by the engine — they are how you take a van out of planning.)
```

**A route:**

```
   PLANNED ──engine picks it up──> ACTIVE ──back at depot──> COMPLETED
                                                  │
                                             CANCELLED (manual)
```

`_load_routes()` promotes both at once: it selects routes in `PLANNED` **or**
`ACTIVE`, flips them to `ACTIVE`, sets `started_at` if unset, and marks their
vehicles `IN_TRANSIT`. Including `ACTIVE` in that query is what makes the engine
restartable — stopping and starting again resumes routes already underway
instead of ignoring them.

---

## Location history is sampled, not streamed

```python
if sim.history_accum_km >= 0.3:      # roughly every 300 m
    db.add(VehicleLocationHistory(...))
```

Positions broadcast every second; they are only *persisted* every ~300 m of
travel. Writing a history row per vehicle per second would be 20 rows/second for
a 20-van fleet — over a million rows a day — for a track no one needs at
one-second resolution. Distance-based sampling keeps the shape of the journey at
a small fraction of the writes.

Note it samples on **distance, not time**: a vehicle stuck in severe traffic
writes fewer rows, which is correct — it isn't going anywhere.

---

## `reload_route` — re-planning something already moving

When `simulation_service.reoptimize_route()` rewrites a route's pending stops,
the engine has to pick up the new order without teleporting the van:

```python
sim = await self._build_sim(db, route_id, only_remaining=True)
if prev:
    sim.position     = prev.position        # keep where it actually is
    sim.speed_factor = prev.speed_factor    # keep any traffic still applied
self._vehicles[route_id] = sim
```

`only_remaining=True` rebuilds the waypoint list from **PENDING stops only**, so
completed deliveries are not revisited. Carrying `position` and `speed_factor`
across is what makes the swap invisible: the vehicle continues from where it is,
still slowed by whatever traffic was in force.

---

## Data structures

```python
_vehicles: dict[int, VehicleSim]     # keyed by route_id, NOT vehicle_id
```

Keyed by route because the simulation follows a *journey*. One vehicle could
have several routes over a day, and `apply_traffic` / `reload_route` both address
a route.

| Class | Holds |
|---|---|
| `Waypoint` | `latitude`, `longitude`, `order_id` (**`None` means depot**), `stop_id` |
| `VehicleSim` | the whole live cursor: waypoints, `seg_index`, `dist_into_seg`, `position`, `speed_factor`, `finished`, `history_accum_km` |
| `SimulationEngine` | the `dict` of sims, the asyncio task, `speed_multiplier`, `running` |

`Waypoint.order_id is None` is the depot test used throughout — that is how the
arrival handler tells a delivery from a homecoming.

`engine` at the bottom of the module is a **singleton**. One clock per process,
imported by `api/routes/health.py`, `api/routes/ws.py`, `main.py` and
`simulation_service.py` — all of them see the same object.

---

## Lifecycle

```
   start(speed)  ──> load PLANNED/ACTIVE routes, build sims
                     nothing to simulate? return {"started": false}
                     otherwise create the asyncio task
                         │
                         ▼
   _run()        ──> while running and active_count > 0: tick, sleep 1s
                         │
                         ▼
   stop()        ──> cancel the task, broadcast SIMULATION_STOPPED
```

The loop also exits **on its own** once every vehicle is finished — the
`active_count > 0` condition — and the `finally` block broadcasts
`SIMULATION_STOPPED` either way, so a client is told whether the run was
cancelled or simply ran out of work.

---

## Known limits

Stated plainly, since they are simplifications rather than bugs:

- **Straight lines, not roads.** Vehicles cut across the map. Following real
  geometry would mean an OSRM `/route` call per leg and storing the polyline.
- **One flat speed.** `AVERAGE_SPEED_KMH` for everything, modified only by a
  traffic factor. No road classes, no time-of-day effects.
- **In-process, single-instance.** The loop lives in the API process, so a
  second replica would run a second clock over the same rows. Scaling out means
  moving this to a dedicated worker with a lock or a leader election.
- **State is not restored on restart.** `seg_index` and `dist_into_seg` live only
  in memory, so a restart resumes vehicles from their depot. The
  *durable* facts — delivered orders, completed stops — survive, because those
  are in Postgres.
