# Glossary

Every term you will hit reading this codebase, in one place. Skim it once; come
back when a word stops making sense.

Grouped by where the term comes from: the delivery business, the optimization
problem, this codebase specifically, then the tools underneath.

---

## The delivery business

| Term | Means |
|---|---|
| **Depot** | The warehouse a fleet works out of. Every route starts and ends here. In the solver it is always **node 0**. The demo has one. |
| **Order** | One delivery to make: an address, a weight, a priority, optionally a time window. The unit of work. |
| **Vehicle** | A van, with a weight capacity and a maximum distance it may drive in a day. |
| **Route** | One vehicle's work for the day: depot → a sequence of stops → depot. |
| **Stop** | One call on a route — "visit order 1043, fourth in line". Carries both the *planned* and *actual* arrival time. |
| **Plan** | A **proposed** set of routes, not yet real. Produced by a solve; becomes routes only when a human accepts it. |
| **Dispatcher** | The person who reviews plans and sends vans out. The main user, and the role most endpoints require. |
| **Service time** | How long a stop itself takes — parking, walking to the door, a signature. Charged separately from travel time. |
| **Time window** | "Deliver between 09:00 and 12:00." Arriving early is allowed; the van waits. |
| **Unassigned** | An order no vehicle could serve. Reported with a reason rather than silently dropped. |

---

## The optimization problem

| Term | Means |
|---|---|
| **VRP** | **V**ehicle **R**outing **P**roblem. Given a depot, orders and vehicles, find the cheapest set of routes. |
| **CVRPTW** | The variant RouteOS actually solves: **C**apacitated VRP with **T**ime **W**indows. Each letter is a constraint — vans have a weight limit, and deliveries have deadlines. |
| **NP-hard** | No known algorithm finds the *provably* best answer in reasonable time as the problem grows. One van and 15 orders already has 1.3 × 10¹² possible sequences. So RouteOS looks for a good answer fast, then measures how good it is. |
| **Heuristic** | An algorithm that finds a good answer without proving it is the best. The solver is one, which is why it can be beaten. |
| **Objective** | What to minimise: `MIN_DISTANCE`, `MIN_TIME`, or `BALANCED` (a blend). Chosen per run. |
| **Baseline** | The deliberately dumb comparison algorithm — greedy nearest-neighbour. Run on **every** optimization so the reported saving is a measurement against a real alternative. |
| **Nearest-neighbour** | The baseline's strategy: from where you are, go to the closest order that still fits; repeat. Fast, and reliably beatable — it strands far-apart orders. |
| **Local search** | How the solver improves an answer: make a small change (move a stop to another route, reverse a segment), keep it if better, repeat until the clock runs out. |
| **Guided local search** | The specific metaheuristic used. When stuck, it *penalises the features* of the current answer to push the search somewhere new rather than restarting blindly. |
| **First-solution strategy** | How the very first (rough) answer is built, before improvement starts. `PATH_CHEAPEST_ARC` — essentially the greedy baseline. Matters a lot when there is little CPU, because then most of the answer *is* the first solution. |

### OR-Tools vocabulary

These four words appear all over `solver.py` and mean nothing outside it.

| Term | Means |
|---|---|
| **Node** vs **index** | A *node* is a place on the map (0 = depot, 1..n = orders). An *index* is a position in a particular vehicle's path. They differ because each vehicle has its own start and end, and `manager.IndexToNode()` translates. |
| **Dimension** | A quantity that accumulates along a route and can be bounded. RouteOS uses three: `Capacity` (kg on board), `Distance` (metres driven), `Time` (minutes elapsed). |
| **Slack** | Permission for a dimension to *wait*. Slack on the Time dimension is exactly what makes arriving early for a window legal. |
| **Disjunction** | Permission to **skip** a node, at a price. Without it, one unservable order makes the whole problem infeasible and the solver returns nothing at all. |
| **Drop penalty** | The price of skipping. 5,000,000 for a NORMAL order, multiplied by priority weight. Deliberately enormous: a long urban leg costs ~30,000, so dropping an order is never worth a detour. |

---

## This codebase specifically

Terms you will not find in a textbook — they are RouteOS's own.

| Term | Means | Where |
|---|---|---|
| **`plan_source`** | Which algorithm produced the dispatched plan: `"solver"` or `"baseline"`. **Seeing `"baseline"` means the solver failed to beat greedy in its time budget** — a signal to raise the budget or add CPU, not a bug. | `optimization_service.py` |
| **Portfolio** | Running two algorithms and shipping whichever won. Why the baseline is not merely a benchmark — it is a real candidate. | `_better_plan()` |
| **Horizon** | The single origin all solver times are measured from — the earliest delivery window in the batch. The solver works in *integer minutes after the horizon*, never in timestamps. Stored with the plan so relative ETAs can be turned back into real times on accept. | `_execute_run()` |
| **Vehicle fixed cost** | 300,000 "metre-equivalents" (= 300 km) charged for putting *any* van on the road, so the solver consolidates instead of using one van per order. A **policy dial**, not a measurement. | `solver.py` |
| **Tick** | One iteration of the simulation loop — one real second, in which every active van moves a little. | `simulation/engine.py` |
| **Speed multiplier** vs **speed factor** | Two different dials, easily confused. *Multiplier* (1×–60×) is how fast the **clock** runs — a demo control. *Factor* (1.0 / 0.6 / 0.35 / 0.0) is how fast a **vehicle** goes — traffic. A breakdown is a factor of zero. | `engine.py` |
| **Matrix source** | Where distances came from: `"osrm"` (real road network) or `"haversine"` (straight line × 1.25). Reported because it changes how much the numbers can be trusted. | `optimization/matrix.py` |
| **Accept / discard** | The human decision that turns a plan into routes, or throws it away. The whole reason solving and dispatching are separate steps. | `accept_plan()` |
| **Progress cursor** | `routes.progress_stop_index` — the index of the last completed stop, `-1` at the depot. Denormalised so the UI can draw progress without scanning every stop. | `models/route.py` |

---

## The tools underneath

| Term | Means |
|---|---|
| **PostGIS** | The Postgres extension that makes the database understand geography — "which orders are within 5 km of here" as a query, using a spatial index. |
| **`Geography` vs `Geometry`** | PostGIS column types. `Geography` treats coordinates as points on a globe and answers distances in **metres**; `Geometry` treats them as a flat plane and answers in **degrees**, which cannot be compared across latitudes. RouteOS uses `Geography`. |
| **GiST** | The index type that makes proximity search fast. A normal B-tree index cannot help, because it orders one dimension at a time and "nearby" is two-dimensional. |
| **SRID 4326** | The coordinate system: ordinary GPS latitude/longitude, the same thing a phone reports. |
| **Haversine** | The formula for distance along the surface of a sphere. You cannot use Pythagoras on latitude and longitude, because a degree of longitude shrinks as you move away from the equator. |
| **Road factor** | The 1.25 multiplier turning straight-line distance into approximate driving distance. Roads bend. It is the offline fallback for when OSRM is unavailable. |
| **OSRM** | An external routing service that returns *real* road distances and drive times. Off by default, because the public demo server is unreliable. |
| **Alembic** | Manages database schema changes as an ordered chain of migration scripts. |
| **Pydantic schema** | Validates what comes into the API and defines what goes out. Deliberately separate from the database models, so an internal column cannot leak into a response. |
| **JWT** | The signed token proving who you are, sent on every request. Signed, **not encrypted** — anyone holding it can read the contents, so it carries identifiers and never secrets. |
| **TanStack Query** | The frontend's server-state cache. Owns anything that lives in the database; `useState` owns only per-screen UI state. |
| **Zustand** | The frontend's store for the two pieces of global *client* state: who is signed in, and the toast queue. |
| **`MissingGreenlet`** | The error you get when async SQLAlchemy tries to load something lazily. Almost always a missing `selectinload()` on a relationship. See [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md). |

---

## Status values

The state machines are drawn in
[`architecture/BACKEND-FLOWS.md`](architecture/BACKEND-FLOWS.md); this is just
the vocabulary.

**Order** — `PENDING` (the only status the optimizer will plan for) →
`ASSIGNED` → `OUT_FOR_DELIVERY` → `DELIVERED` / `FAILED`, or `CANCELLED`.

**Vehicle** — `AVAILABLE` (the only status the optimizer will use) →
`ASSIGNED` → `IN_TRANSIT`, back to `AVAILABLE`. `MAINTENANCE` and `OFFLINE` are
set by hand and are how a van is taken out of planning.

**Route** — `PLANNED` → `ACTIVE` → `COMPLETED`, or `CANCELLED`.

**Stop** — `PENDING` → `ARRIVED` → `COMPLETED`, or `SKIPPED`. The
`PENDING`/`COMPLETED` split is what makes mid-route re-optimization safe: only
pending stops are re-planned.

**Optimization run** — `PENDING` → `PROCESSING` → `COMPLETED` / `FAILED`.
(Note `FAILED` is also reused for a plan a human discarded.)
