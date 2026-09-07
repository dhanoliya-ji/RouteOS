"""Plain data structures passed in/out of the optimization engine.

These are intentionally decoupled from SQLAlchemy models and Pydantic schemas so
the solver can be unit-tested in isolation with no DB.

That decoupling is the single most useful property of this package. Because the
solver's whole interface is these dataclasses:

  * `tests/test_optimization.py` builds a problem in three lines and asserts on
    the answer — no database, no fixtures, no HTTP.
  * `scripts/benchmark.py` can time the algorithm alone, with no query noise.
  * changing the storage layer cannot touch the solver.

The shape of the contract:

    IN   depot: Coord
         orders: list[OrderNode]
         vehicles: list[VehicleInput]

    OUT  SolveResult
           routes:     list[SolvedRoute]   -> each with list[SolvedStop]
           unassigned: list[UnassignedOrder]

Dataclasses rather than plain dicts because a typo in `demand_kg` should be an
error at the call site, not a `KeyError` deep inside the solver — and because
the field list here *is* the documentation of what the solver needs.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# (latitude, longitude), in that order — the human order. Note this is the
# opposite of what PostGIS wants; see geospatial/queries.py.
Coord = tuple[float, float]


@dataclass
class OrderNode:
    """One delivery, as the solver sees it.

    Note what is absent: customer name, address, phone. The solver needs
    geometry, weight, timing and priority — nothing else — so nothing else is
    passed in. `order_id` and `order_number` are carried through untouched
    purely so the caller can match the answer back to its rows.
    """

    order_id: int
    order_number: str
    coord: Coord
    demand_kg: float
    service_time_min: int = 10

    # Minutes from the planning horizon start; None = no constraint.
    #
    # Relative integers, NOT timestamps. The solver does exact integer
    # arithmetic on time, so converting once at the boundary (in
    # optimization_service, against a horizon anchored on the earliest window)
    # keeps datetime and timezone handling entirely out of the hot loop.
    # None means unbounded, which the solver widens to the full horizon.
    tw_start_min: int | None = None
    tw_end_min: int | None = None

    # From OrderPriority.weight — 1/2/4/8 for LOW/NORMAL/HIGH/URGENT. Multiplies
    # the solver's drop penalty, so a higher weight makes this order more
    # expensive to leave unassigned. Defaults to NORMAL's value.
    priority_weight: int = 2


@dataclass
class VehicleInput:
    """One vehicle, as the solver sees it — its two hard limits.

    capacity_kg feeds the Capacity dimension, max_route_distance_km the
    Distance dimension. The default of 200 km mirrors the model's column
    default, so a vehicle with no stated limit still gets a finite one rather
    than an unbounded route.
    """

    vehicle_id: int
    registration_number: str
    capacity_kg: float
    max_route_distance_km: float = 200.0


@dataclass
class SolvedStop:
    """One call on a solved route."""

    order_id: int
    order_number: str
    # 1-based position in the visiting order. This is the actual answer the
    # solver was asked for.
    stop_sequence: int
    coord: Coord
    # Length of the leg that ended here. Per-leg rather than cumulative, so a
    # route's total is the sum of its stops and re-sequencing part of a route
    # only rewrites the legs that changed.
    distance_from_previous_km: float
    load_kg: float
    # Arrival time, still relative to the planning horizon. Read straight off
    # the solved Time dimension — the constraint solver computed the schedule,
    # so no second pass is needed. accept_plan turns it back into a timestamp.
    eta_minutes_from_start: float


@dataclass
class SolvedRoute:
    """One vehicle's work: an ordered list of stops, plus its totals."""

    vehicle_id: int
    registration_number: str
    total_distance_km: float
    estimated_duration_minutes: float
    total_load_kg: float
    # The vehicle's limit, echoed back alongside the load it was given. Carried
    # so a caller (or a test) can check utilisation without looking the vehicle
    # up again.
    capacity_kg: float
    # default_factory, not `= []`: a mutable default would be shared by every
    # instance of the dataclass — the classic Python footgun.
    stops: list[SolvedStop] = field(default_factory=list)


@dataclass
class UnassignedOrder:
    """An order that could not be served, and the best guess at why.

    Reporting these is a deliberate feature, not an error path. An infeasible
    problem (300 orders, two vans) should yield the best partial plan plus an
    honest list of what was left out — not an exception and no plan at all.

    `reason` holds an UnassignedReason value, deduced by solver._infer_reason:
    OR-Tools does not report why it dropped a node, so the cause is inferred by
    checking the order against the fleet.
    """

    order_id: int
    order_number: str
    reason: str


@dataclass
class SolveResult:
    """Everything one solve produced.

    `routes` and `unassigned` together account for exactly the input orders —
    each order appears in precisely one of them, which is a property
    tests/test_optimization.py asserts directly.
    """

    routes: list[SolvedRoute]
    unassigned: list[UnassignedOrder]
    # The solver's internal cost for this solution. Comparable only between
    # runs of the same objective on the same problem — diagnostic, not a KPI.
    # The greedy baseline reports 0.0, since it has no objective function.
    objective_value: float
    # "osrm" | "haversine" | "none" — which distance source was actually used.
    # Surfaced because it materially affects how much the numbers can be
    # trusted: real road distances versus a 1.25x approximation.
    matrix_source: str
