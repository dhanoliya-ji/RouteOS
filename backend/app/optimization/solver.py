"""Capacity- and time-window-aware Vehicle Routing Problem solver (OR-Tools).

Model summary
-------------
* Node 0 is the depot; nodes 1..n are the orders.
* Objective arc cost is distance (MIN_DISTANCE), travel time (MIN_TIME), or a
  blend (BALANCED). A per-vehicle fixed cost discourages using more vehicles
  than necessary.
* Capacity dimension enforces per-vehicle ``capacity_kg``.
* Distance dimension enforces per-vehicle ``max_route_distance_km``.
* Time dimension carries travel + service time and enforces delivery windows
  (with waiting allowed). Node arrival times feed each stop's ETA.
* Orders may be dropped via disjunctions with a priority-scaled penalty, so the
  solver serves everything it can and reports the rest as unassigned.

This is a heuristic solver (guided local search under a wall-clock limit). It is
NOT guaranteed mathematically optimal; results are high-quality feasible routes.

How to read `solve_vrp`
-----------------------
You do not write the search here — you *describe the rules* and OR-Tools
searches. The function is therefore a sequence of declarations, marked with
banner comments:

    STEP 1  index manager      map nodes <-> per-vehicle path positions
    STEP 2  arc cost           what we are minimising
    STEP 3  capacity dimension how much a van can carry
    STEP 4  distance dimension how far a van may drive
    STEP 5  time dimension     travel + service, and delivery windows
    STEP 6  disjunctions       permission to drop an order, at a price
    STEP 7  search parameters  how to look, and for how long
    STEP 8  solve and read back

A note on why the callbacks are inline
--------------------------------------
Each dimension needs a Python callback registered with the model. Those stay
nested inside `solve_vrp` rather than being extracted into helper functions:
OR-Tools holds them across the C++/SWIG boundary, so their lifetime is easiest
to reason about when an obvious local reference keeps them alive for the whole
solve. The banner comments carry the structure instead. The two pieces with no
callbacks — building search parameters and reading the solution back — *are*
extracted below.
"""
from __future__ import annotations

import math

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from app.core.config import settings
from app.models.enums import OptimizationObjective, UnassignedReason
from app.optimization.matrix import DistanceMatrix, build_matrix
from app.optimization.types import (
    Coord,
    OrderNode,
    SolvedRoute,
    SolvedStop,
    SolveResult,
    UnassignedOrder,
    VehicleInput,
)

# Penalty (in objective units) for leaving a NORMAL-priority order unassigned.
# Must dominate typical arc costs so the solver prefers serving orders.
#
# The magnitude is the whole trick. Arc costs are metres, so a long urban leg is
# maybe 30,000. At five million, dropping an order is never worth a detour — the
# solver will only do it when the order is genuinely impossible to serve.
BASE_DROP_PENALTY = 5_000_000
# Fixed cost per used vehicle, nudging the solver to consolidate.
#
# 300,000 metre-equivalents = 300 km. Without it, the cheapest plan measured in
# distance alone is often one van per order. This says "a vehicle-day costs us
# about what 300 km of driving costs us", so a second van must save more than
# 300 km to be worth dispatching. It is a policy dial, not a measurement, and
# services/optimization_service._plan_cost mirrors it so both plans are judged
# by the same exchange rate.
VEHICLE_FIXED_COST = 300_000
TIME_HORIZON_MIN = 24 * 60  # planning horizon (minutes)


def _scaled_cost(matrix: DistanceMatrix, objective: OptimizationObjective, i: int, j: int) -> int:
    """Arc cost in integer units for the chosen objective.

    Integers, not floats: the constraint solver does exact integer arithmetic,
    so kilometres become metres and minutes become seconds. That also fixes the
    granularity — one metre and one second are the smallest differences the
    model can perceive.
    """
    dist_m = int(matrix.distances_km[i][j] * 1000)
    dur_s = int(matrix.durations_min[i][j] * 60)
    if objective == OptimizationObjective.MIN_DISTANCE:
        return dist_m
    if objective == OptimizationObjective.MIN_TIME:
        return dur_s
    # BALANCED: normalise time to metre-equivalents (avg speed) and average.
    #
    # The two quantities are in different units, so they cannot simply be added.
    # `dur_s * 1000 // 60` converts seconds into "metres you would cover in that
    # time at 60 km/h", putting both on one scale before averaging.
    return (dist_m + dur_s * 1000 // 60) // 2  # rough metre-equivalent blend


def _infer_reason(order: OrderNode, vehicles: list[VehicleInput]) -> str:
    """Best guess at why an order went unserved.

    OR-Tools does not say why it dropped a node — it just drops it — so the
    cause is deduced by re-checking the order against the fleet. Ordered
    most-certain first:

      1. heavier than every vehicle  -> definitely CAPACITY_EXCEEDED
      2. it had a time window        -> probably the window
      3. otherwise                   -> the fleet simply ran out of room

    Only the first is certain; the rest are informed guesses, which is why the
    label is a hint for a dispatcher rather than a diagnosis.
    """
    max_cap = max((v.capacity_kg for v in vehicles), default=0)
    if order.demand_kg > max_cap:
        return UnassignedReason.CAPACITY_EXCEEDED.value
    if order.tw_start_min is not None or order.tw_end_min is not None:
        return UnassignedReason.TIME_WINDOW_INFEASIBLE.value
    return UnassignedReason.NO_AVAILABLE_VEHICLE.value


def _all_unassigned(orders: list[OrderNode], reason_fn) -> SolveResult:
    """An empty plan in which every order is reported as unserved.

    Used for the give-up paths (no vehicles, no solution found). Returning a
    valid SolveResult rather than raising is what lets the caller treat "could
    not serve these" as data — the API still answers, with an honest list.
    """
    return SolveResult(
        routes=[],
        unassigned=[UnassignedOrder(o.order_id, o.order_number, reason_fn(o)) for o in orders],
        objective_value=0.0,
        matrix_source="none",
    )


def _search_parameters(time_limit_seconds: int | None):
    """Build the search configuration: how to look for a solution, and how long.

    Two phases, and the split explains most of the tuning:

        PHASE 1  first_solution_strategy — construct one feasible answer fast.
                 PATH_CHEAPEST_ARC repeatedly takes the cheapest arc to an
                 unvisited node, which is essentially our greedy baseline. This
                 is a starting point, not an answer.

        PHASE 2  local_search_metaheuristic — improve it until time runs out.
                 GUIDED_LOCAL_SEARCH tries small mutations (move a stop to
                 another route, reverse a segment, swap two stops) and keeps
                 improvements. When stuck, it penalises the *features* of the
                 current solution to push the search somewhere new, rather than
                 restarting blindly.

    Phase 2 is where quality comes from, which is why the time budget dominates
    the result — and why the phase-1 heuristic is configurable: on a starved CPU
    phase 2 gets few iterations, so the answer is mostly whatever phase 1 built.
    """
    params = pywrapcp.DefaultRoutingSearchParameters()
    # getattr with a default so an invalid name in configuration degrades to the
    # sensible strategy instead of raising at solve time.
    params.first_solution_strategy = getattr(
        routing_enums_pb2.FirstSolutionStrategy,
        settings.solver_first_solution_strategy,
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC,
    )
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    budget = time_limit_seconds if time_limit_seconds is not None else settings.solver_time_limit_seconds
    # max(1, ...) because a zero or negative limit would leave the search no
    # time at all and yield nothing.
    params.time_limit.FromSeconds(max(1, int(budget)))
    return params


def _extract_routes(
    routing,
    manager,
    solution,
    time_dim,
    matrix: DistanceMatrix,
    orders: list[OrderNode],
    vehicles: list[VehicleInput],
) -> tuple[list[SolvedRoute], set[int]]:
    """Read the solved model back into our own dataclasses.

    Returns the routes plus the set of order ids that were served, so the caller
    can work out what was dropped by difference.

    OR-Tools represents a solution as a **successor pointer per node**: NextVar
    tells you what comes after this position. So reading a route means walking
    that linked list from the vehicle's start until IsEnd.
    """
    routes: list[SolvedRoute] = []
    served: set[int] = set()

    for v in range(len(vehicles)):
        index = routing.Start(v)
        # A vehicle whose start points straight at its end was never used. Skip
        # it, which is how "vehicles used" falls out of the answer without being
        # counted separately.
        if routing.IsEnd(solution.Value(routing.NextVar(index))):
            continue

        veh = vehicles[v]
        stops: list[SolvedStop] = []
        seq = 0
        total_dist = 0.0
        total_load = 0.0
        prev_node = 0  # every route begins at the depot

        # Step past the start marker onto the first real stop.
        index = solution.Value(routing.NextVar(index))
        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            # Node numbering: 0 is the depot, so order k is node k+1 — hence
            # the -1 to get back to our list.
            order = orders[node - 1]

            leg_km = matrix.distances_km[prev_node][node]
            total_dist += leg_km
            total_load += order.demand_kg
            # The arrival time the constraint solver already computed. No second
            # scheduling pass is needed — this IS the schedule.
            eta = solution.Value(time_dim.CumulVar(index))

            seq += 1
            stops.append(
                SolvedStop(
                    order_id=order.order_id,
                    order_number=order.order_number,
                    stop_sequence=seq,
                    coord=order.coord,
                    distance_from_previous_km=round(leg_km, 3),
                    load_kg=order.demand_kg,
                    eta_minutes_from_start=float(eta),
                )
            )
            served.add(order.order_id)
            prev_node = node
            index = solution.Value(routing.NextVar(index))

        # return leg to depot
        # Not a stop, but part of the route's cost — so it is added to the total
        # only. matrix[...][0] because node 0 is the depot.
        total_dist += matrix.distances_km[prev_node][0]
        # The End node's cumulative Time is when the vehicle gets home, which is
        # the route's true duration including any waiting for time windows.
        end_time = solution.Value(time_dim.CumulVar(routing.End(v)))

        routes.append(
            SolvedRoute(
                vehicle_id=veh.vehicle_id,
                registration_number=veh.registration_number,
                total_distance_km=round(total_dist, 3),
                estimated_duration_minutes=float(end_time),
                total_load_kg=round(total_load, 2),
                capacity_kg=veh.capacity_kg,
                stops=stops,
            )
        )

    return routes, served


def solve_vrp(
    depot: Coord,
    orders: list[OrderNode],
    vehicles: list[VehicleInput],
    objective: OptimizationObjective = OptimizationObjective.BALANCED,
    time_limit_seconds: int | None = None,
) -> SolveResult:
    """Solve the VRP. ``time_limit_seconds`` overrides the configured budget —
    a run detached from an HTTP request can afford far more search than one a
    client is waiting on."""
    # --- Guard clauses: two problems not worth building a model for ---------
    if not orders:
        return SolveResult(routes=[], unassigned=[], objective_value=0.0, matrix_source="none")
    if not vehicles:
        # Nothing to route with. Every order is unserved for the same reason, so
        # skip _infer_reason's guessing.
        return _all_unassigned(orders, lambda _o: UnassignedReason.NO_AVAILABLE_VEHICLE.value)

    # Node 0 = depot, nodes 1..n = orders, in the order given. This convention
    # is assumed by every index calculation below.
    coords: list[Coord] = [depot] + [o.coord for o in orders]
    matrix = build_matrix(coords)
    n = len(coords)
    num_vehicles = len(vehicles)

    # === STEP 1: index manager ==============================================
    # OR-Tools separates *nodes* (places) from *indices* (positions in a
    # vehicle's path), because each vehicle has its own start and end position.
    # The manager translates between them — hence IndexToNode everywhere.
    manager = pywrapcp.RoutingIndexManager(n, num_vehicles, 0)
    routing = pywrapcp.RoutingModel(manager)

    # === STEP 2: arc cost — what are we minimising? =========================
    def cost_cb(from_index: int, to_index: int) -> int:
        i = manager.IndexToNode(from_index)
        j = manager.IndexToNode(to_index)
        return _scaled_cost(matrix, objective, i, j)

    cost_idx = routing.RegisterTransitCallback(cost_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(cost_idx)
    # Charge for putting each van on the road at all, so the solver consolidates
    # instead of spreading orders across the whole fleet.
    for v in range(num_vehicles):
        routing.SetFixedCostOfVehicle(VEHICLE_FIXED_COST, v)

    # === STEP 3: capacity dimension =========================================
    # A "dimension" is a quantity that accumulates along a route and can be
    # bounded. Here: kilograms on board.
    #
    # ceil() because the model is integer-only, and rounding UP is the safe
    # direction — a 10.2 kg parcel counted as 10 could let the solver overload a
    # van by a fraction. max(0, ...) defends against a negative weight.
    demands = [0] + [max(0, math.ceil(o.demand_kg)) for o in orders]

    def demand_cb(from_index: int) -> int:
        return demands[manager.IndexToNode(from_index)]

    # Unary (one argument): the load added depends only on the node visited,
    # not on where you came from — unlike distance, which needs both.
    demand_idx = routing.RegisterUnaryTransitCallback(demand_cb)
    capacities = [int(v.capacity_kg) for v in vehicles]
    routing.AddDimensionWithVehicleCapacity(
        demand_idx,
        0,           # no slack: load cannot be "wasted" or skipped
        capacities,  # a separate limit per vehicle
        True,        # start cumulative at zero — every van leaves empty
        "Capacity",
    )

    # === STEP 4: distance dimension (per-vehicle max) =======================
    def dist_cb(from_index: int, to_index: int) -> int:
        i = manager.IndexToNode(from_index)
        j = manager.IndexToNode(to_index)
        return int(matrix.distances_km[i][j] * 1000)

    dist_idx = routing.RegisterTransitCallback(dist_cb)
    # AddDimension takes ONE global cap, so pass the largest vehicle's limit
    # here and then tighten each vehicle individually below.
    max_dist_global = max(int(v.max_route_distance_km * 1000) for v in vehicles)
    routing.AddDimension(dist_idx, 0, max_dist_global, True, "Distance")
    dist_dim = routing.GetDimensionOrDie("Distance")
    for v in range(num_vehicles):
        # Bound the cumulative distance at the END node, so the limit covers the
        # whole round trip including the leg home — not just the outward journey.
        dist_dim.CumulVar(routing.End(v)).SetMax(int(vehicles[v].max_route_distance_km * 1000))

    # === STEP 5: time dimension (travel + service, enforces windows) ========
    service = [0] + [int(o.service_time_min) for o in orders]

    def time_cb(from_index: int, to_index: int) -> int:
        i = manager.IndexToNode(from_index)
        j = manager.IndexToNode(to_index)
        # Travel time to j, PLUS the service time spent at i. Charging service
        # on departure is what makes a stop's own handling time delay everything
        # after it without delaying its own arrival.
        return int(matrix.durations_min[i][j]) + service[i]

    time_idx = routing.RegisterTransitCallback(time_cb)
    routing.AddDimension(
        time_idx,
        TIME_HORIZON_MIN,  # slack: this is what makes WAITING legal — arriving
                           # early for a window is allowed, the van just idles
        TIME_HORIZON_MIN,  # a route cannot exceed the horizon
        False,             # do NOT force start at zero, so a vehicle may set off
                           # later to hit a window rather than wait at a stop
        "Time",
    )
    time_dim = routing.GetDimensionOrDie("Time")
    for node in range(1, n):  # from 1: the depot has no delivery window
        order = orders[node - 1]
        index = manager.NodeToIndex(node)
        # A missing bound means "no constraint", widened to the full horizon.
        start = order.tw_start_min if order.tw_start_min is not None else 0
        end = order.tw_end_min if order.tw_end_min is not None else TIME_HORIZON_MIN
        time_dim.CumulVar(index).SetRange(int(start), int(end))
    for v in range(num_vehicles):
        # Departure may be anywhere in the horizon (see the False above).
        time_dim.CumulVar(routing.Start(v)).SetRange(0, TIME_HORIZON_MIN)

    # === STEP 6: optional visits (priority-scaled drop penalty) =============
    # Without this the model is all-or-nothing: one unservable order makes the
    # whole problem infeasible and the solver returns nothing. A disjunction
    # says "visiting this node is optional, but skipping it costs you".
    for node in range(1, n):
        order = orders[node - 1]
        # max(1, ...) so a zero or missing weight still carries the base penalty
        # rather than making the order free to abandon.
        penalty = BASE_DROP_PENALTY * max(1, order.priority_weight)
        routing.AddDisjunction([manager.NodeToIndex(node)], penalty)

    # === STEP 7: search parameters ==========================================
    # The first-solution heuristic matters more than it looks: on small/slow
    # instances the guided local search completes few improvement iterations, so
    # the quality of the starting solution largely determines the final answer.
    params = _search_parameters(time_limit_seconds)

    # === STEP 8: solve, then read the answer back ===========================
    solution = routing.SolveWithParameters(params)

    if solution is None:
        # No feasible solution within the budget. Report every order as unserved
        # with an inferred reason, rather than raising — the caller still gets a
        # usable, honest answer.
        result = _all_unassigned(orders, lambda o: _infer_reason(o, vehicles))
        # _all_unassigned cannot know which matrix was used, so correct it here.
        result.matrix_source = matrix.source
        return result

    routes, served = _extract_routes(
        routing, manager, solution, time_dim, matrix, orders, vehicles
    )

    # Anything the solver chose to drop (paid the disjunction penalty for).
    unassigned = [
        UnassignedOrder(o.order_id, o.order_number, _infer_reason(o, vehicles))
        for o in orders
        if o.order_id not in served
    ]

    return SolveResult(
        routes=routes,
        unassigned=unassigned,
        objective_value=float(solution.ObjectiveValue()),
        matrix_source=matrix.source,
    )
