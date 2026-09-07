# `app/optimization/` — the route optimizer

The algorithmic core. Given a depot, a list of orders and a list of vehicles,
decide **which vehicle delivers which orders, and in what order**.

This package is deliberately isolated: it imports no session, opens no
connection, and knows nothing about HTTP. Inputs and outputs are plain
dataclasses, so the whole thing can be unit-tested with a handful of made-up
orders and no database at all — which is exactly what `tests/test_optimization.py`
and `scripts/benchmark.py` do.

---

## The problem, stated precisely

This is a **Capacitated Vehicle Routing Problem with Time Windows** — CVRPTW.
Each constraint in the name is a rule the answer must respect:

```
   Vehicle Routing     every vehicle leaves the depot, visits some
                       orders, and returns to the depot

   Capacitated         a vehicle cannot carry more than capacity_kg

   Time Windows        an order with a 09:00–12:00 window must be
                       visited inside it — arriving early means waiting

   + per-vehicle max route distance
   + per-order service time (a stop takes minutes, not an instant)
   + priorities (if something must be dropped, drop the least urgent)
```

### Why it is hard

One vehicle, `n` orders: `n!` possible visiting orders.

```
    n = 5      120 routes                     instant
    n = 10     3,628,800                      still fine
    n = 15     1.3 x 10^12                    already hopeless
    n = 150    ~10^262                         more than atoms in the universe
```

And that is *one* vehicle. With 20 vehicles you also choose how to split orders
between them. This problem is **NP-hard**: no known algorithm finds the proven
best answer in reasonable time as `n` grows.

So we do not look for the best answer. We look for a **good answer, fast** —
and then we *measure* how good it is, which is what `baseline.py` is for.

---

## The three-step pipeline

```
   depot + orders + vehicles
              │
              ▼
   ┌─────────────────────────────────────────┐
   │ 1. matrix.py                            │
   │    build a distance/time matrix          │
   │    "how far is every point from every    │
   │     other point?"                        │
   └────────────────┬────────────────────────┘
                    │  DistanceMatrix
        ┌───────────┴────────────┐
        ▼                        ▼
   ┌──────────────┐      ┌──────────────────┐
   │ 2. solver.py │      │ 2b. baseline.py  │
   │  OR-Tools    │      │  greedy nearest- │
   │  CVRPTW      │      │  neighbour       │
   │  (smart,     │      │  (dumb, instant) │
   │   seconds)   │      │                  │
   └──────┬───────┘      └────────┬─────────┘
          │  SolveResult           │  SolveResult
          └───────────┬────────────┘
                      ▼
   ┌─────────────────────────────────────────┐
   │ 3. services/optimization_service.py     │
   │    compare the two, keep the winner,    │
   │    report the improvement               │
   └─────────────────────────────────────────┘
```

---

## File index

| File | Job | Lines of note |
|---|---|---|
| `types.py` | The dataclasses in and out. No logic. | Read this first |
| `matrix.py` | Build the distance/duration matrix (OSRM, or haversine fallback) | |
| `baseline.py` | Greedy nearest-neighbour — the honest comparison point | |
| `solver.py` | The real thing: OR-Tools CVRPTW | The dense one |

---

## `types.py` — why plain dataclasses

```
   IN                                OUT
   ──                                ───
   OrderNode                         SolveResult
     order_id, coord, demand_kg        ├── routes: list[SolvedRoute]
     service_time_min                  │     ├── vehicle_id, total_distance_km
     tw_start_min, tw_end_min          │     └── stops: list[SolvedStop]
     priority_weight                   │            order_id, stop_sequence,
                                       │            eta_minutes_from_start
   VehicleInput                        ├── unassigned: list[UnassignedOrder]
     vehicle_id, capacity_kg           │     order_id, reason
     max_route_distance_km             ├── objective_value
                                       └── matrix_source
```

These could have been the SQLAlchemy models. They are not, on purpose:

- The solver never needs a database, so it can be tested in milliseconds.
- `OrderNode` carries `tw_start_min` — **minutes from the planning horizon**,
  not a timestamp. The solver does integer arithmetic on time; converting once
  at the boundary keeps datetime handling out of the hot loop entirely.
- Swapping the storage layer would not touch this package.

The time conversion is worth seeing, because it explains the whole design:

```
   database:  delivery_window_start = 2026-01-14T09:00:00Z
                          │
                          │  horizon = earliest window across all orders
                          │            (or now, if none have windows)
                          ▼
   solver:    tw_start_min = 60      # "60 minutes after planning starts"
```

---

## `matrix.py` — how far is everything from everything

The solver asks "what does it cost to go from node `i` to node `j`?" thousands
of times per second. Computing that on demand would dominate the runtime, so it
is precomputed once into a lookup table.

**Node 0 is always the depot.** Orders are nodes `1..n`, in the order given.
That convention is assumed everywhere in this package.

```
   coords = [depot, order1, order2, order3]

   distances_km =  [ [ 0.0,  4.2,  7.1,  3.3 ],     from depot
                     [ 4.2,  0.0,  5.5,  6.0 ],     from order1
                     [ 7.1,  5.5,  0.0,  2.8 ],     from order2
                     [ 3.3,  6.0,  2.8,  0.0 ] ]    from order3
                       ▲
                       └─ distances_km[2][3] = 2.8 km, order2 -> order3
```

Both matrices are **symmetric** with a zero diagonal, and cost `O(n²)` to build
and store — 150 orders is a 151×151 table, about 23,000 pairs. That is the real
reason a huge order count gets expensive before the solver even starts.

### Two sources, with a fallback

| Source | When | Gives |
|---|---|---|
| **OSRM** `/table` | `USE_OSRM=true`, ≤100 points, service reachable | real road distances and drive times |
| **Haversine** | otherwise, always | great-circle distance × `1.25`, time at `AVERAGE_SPEED_KMH` |

The fallback is not a nicety — it is what makes the optimizer work with no
network at all. `_osrm_matrix` returns `None` on *any* failure and the caller
quietly drops to haversine, logging a warning.

The `1.25` factor is a standard rule of thumb: roads are not straight, so actual
driving distance in a city is roughly a quarter longer than the crow-flies line.
The `≤100` cap exists because the public OSRM demo server rejects large tables.

---

## `baseline.py` — the honest comparison

A greedy **nearest-neighbour** heuristic. It exists so the claim "we saved 23%"
has something real behind it, rather than being measured against a number
somebody made up.

It routes the way a person with a map would:

```
   for each vehicle, in turn:
       stand at the depot
       repeat:
           of the orders that still fit in the remaining capacity,
           pick the CLOSEST one
           if driving there and still getting home would break the
              distance limit -> stop
           go there, drop the parcel, and now stand there instead
       return to the depot
```

Its weakness is the whole point. Greedy takes the best *immediate* step and
never reconsiders, so it strands far-apart leftovers and ends the day with one
long expensive leg home:

```
   greedy: 1 -> 2 -> 3 are all close, grab them...
           ...now the only orders left are across the city.

        depot ──► o1 ──► o2 ──► o3 ─────────────────────────► o4
                                        one huge leg          │
              ◄─────────────────────────────────────────────  ┘

   a real solver sees the whole picture and interleaves the far
   order into a sensible loop instead.
```

Greedy is `O(n²)` per vehicle and runs in milliseconds — so it is cheap enough
to run on *every* optimization, which is what makes the reported improvement a
measurement rather than a marketing figure.

---

## `solver.py` — the real optimizer

Google OR-Tools' routing library. The code builds a **model** describing the
rules, then hands it to a search engine. You do not write the search; you write
the constraints.

### Step 1 — index manager

OR-Tools distinguishes *nodes* (points on the map) from *indices* (positions in a
vehicle's path), because each vehicle has its own start and end. `manager`
translates:

```python
manager = pywrapcp.RoutingIndexManager(n, num_vehicles, 0)
                                       │      │         └─ depot is node 0
                                       │      └─ how many vehicles
                                       └─ how many nodes
node = manager.IndexToNode(index)   # the translation you see everywhere
```

### Step 2 — arc cost: what are we minimising?

Every callback returns an **integer**, because the engine does exact integer
arithmetic. So kilometres become metres and minutes become seconds:

| Objective | Cost of arc `i → j` |
|---|---|
| `MIN_DISTANCE` | metres |
| `MIN_TIME` | seconds |
| `BALANCED` | the average of metres and time-converted-to-metres |

`BALANCED` has to put both on one scale before averaging — `dur_s * 1000 // 60`
converts seconds into "metres you'd cover in that time at 60 km/h".

### Step 3 — dimensions: the constraints

A **dimension** is a quantity that accumulates along a route, and OR-Tools
enforces bounds on it. Three of them:

```
   ┌─────────────────────────────────────────────────────────────┐
   │ "Capacity"   adds demand_kg at each stop                    │
   │              per-vehicle max = capacity_kg                  │
   │              no reset (a van is not emptied mid-route)      │
   ├─────────────────────────────────────────────────────────────┤
   │ "Distance"   adds leg metres                                │
   │              per-vehicle max = max_route_distance_km        │
   │              set on the END node, so it bounds the whole    │
   │              round trip including the leg home              │
   ├─────────────────────────────────────────────────────────────┤
   │ "Time"       adds travel minutes + the service time of the  │
   │              stop being left                                │
   │              each node's arrival is bounded to its window   │
   │              slack allowed = waiting is legal               │
   └─────────────────────────────────────────────────────────────┘
```

The Time dimension is the subtle one. Its `slack` argument is what permits
*waiting*: arriving at 08:40 for a 09:00 window is fine, the vehicle idles. And
because service time is charged on *departure* from a node, a stop's own handling
time correctly delays everything after it but not its own arrival.

Time also gives ETAs for free — the solved value of each node's cumulative Time
variable *is* that stop's arrival:

```python
eta = solution.Value(time_dim.CumulVar(index))
```

No second pass to compute schedules; the constraint solver already did it.

### Step 4 — disjunctions: allowing failure

Some problems are simply infeasible — 300 orders and two vans. Without an escape
hatch the solver would return nothing at all, which is useless: we want the best
partial answer plus an honest list of what could not be served.

So every order gets a **disjunction**: a penalty for not visiting it.

```python
penalty = BASE_DROP_PENALTY * max(1, order.priority_weight)
routing.AddDisjunction([manager.NodeToIndex(node)], penalty)
```

```
   BASE_DROP_PENALTY = 5,000,000        per NORMAL order
   x priority weight  LOW 1 · NORMAL 2 · HIGH 4 · URGENT 8

   dropping one URGENT order  = 40,000,000
   dropping one LOW order     =  5,000,000
```

The magnitudes are what make this work. A penalty must dwarf any plausible
detour, or the solver would happily abandon a far-away order to save a few
kilometres. At five million metre-equivalents, dropping an order is never worth
it unless it is genuinely impossible — and the priority multiplier means the
*last* thing dropped is the most urgent.

### Step 5 — vehicle fixed cost: use fewer vans

```python
VEHICLE_FIXED_COST = 300_000      # = 300 km in metre-equivalents
```

Without this, the cheapest plan by distance alone is often "one vehicle per
order" — lots of tiny routes. Charging 300 km to put *any* van on the road makes
the solver consolidate: a second vehicle has to save more than 300 km of driving
to be worth dispatching.

This number is a policy, not a fact. It encodes "a vehicle-day costs us about
what 300 km of driving costs us", and it is the main dial for trading vehicle
count against total distance.

### Step 6 — search strategy

```python
params.first_solution_strategy      = PATH_CHEAPEST_ARC   (configurable)
params.local_search_metaheuristic   = GUIDED_LOCAL_SEARCH
params.time_limit.FromSeconds(budget)
```

Two phases, and the split matters:

```
   PHASE 1  construct one feasible answer, fast
            PATH_CHEAPEST_ARC: from where you are, take the cheapest
            arc to somewhere unvisited. (Essentially our greedy
            baseline — a starting point, not an answer.)
                            │
                            ▼
   PHASE 2  improve it until the clock runs out
            GUIDED LOCAL SEARCH: repeatedly try small mutations —
            move a stop to another route, reverse a segment, swap
            two stops — keeping improvements. When stuck in a local
            optimum, it *penalises the features* of the current
            solution to push the search into unexplored territory,
            rather than restarting blindly.
                            │
                            ▼
            best answer found when time_limit expires
```

Phase 2 is why the time budget dominates result quality — and why
`solver_first_solution_strategy` is configurable. On a starved CPU, phase 2 gets
few iterations, so the answer is mostly whatever phase 1 produced, and the
choice of construction heuristic suddenly matters a lot.

**This is a heuristic.** It returns high-quality feasible routes, not a proven
optimum, and it may return a *worse* answer than greedy if given too little
time — which is precisely why the service compares the two and keeps the winner.

### Step 7 — reading the solution back

Walk each vehicle's linked list of indices until the end marker:

```python
index = routing.Start(v)
while not routing.IsEnd(index):
    node = manager.IndexToNode(index)
    ...
    index = solution.Value(routing.NextVar(index))
```

`NextVar` is how the answer is represented — a successor pointer per node. Any
vehicle whose start points straight at its end went unused and is skipped, which
is how "vehicles used" falls out without being counted separately.

---

## Where the numbers live

Everything tunable is in `core/config.py`, not hard-coded here:

| Setting | Default | Effect |
|---|---|---|
| `SOLVER_TIME_LIMIT_SECONDS` | 15 | budget for a synchronous `/run` |
| `SOLVER_ASYNC_TIME_LIMIT_SECONDS` | 180 | ceiling for a background job |
| `SOLVER_SECONDS_PER_ORDER` | 1.6 | how the job budget scales with size |
| `SOLVER_FIRST_SOLUTION_STRATEGY` | `PATH_CHEAPEST_ARC` | phase-1 heuristic |
| `ROAD_DISTANCE_FACTOR` | 1.25 | haversine → road distance |
| `AVERAGE_SPEED_KMH` | 30 | distance → time |
| `USE_OSRM` | false | real road network vs haversine |

The two constants that are *not* configurable — `BASE_DROP_PENALTY` and
`VEHICLE_FIXED_COST` — are structural: they only need to sit at the right
*order of magnitude* relative to arc costs, so exposing them would invite
breaking the model rather than tuning it.

---

## Measuring it

```bash
cd backend
python -m scripts.benchmark --sizes 50 100 250 500
```

Prints solve time, optimizer distance, baseline distance, the percentage gain and
the assignment rate at each size. No database involved — it calls this package
directly, so the timings are the algorithm alone.
