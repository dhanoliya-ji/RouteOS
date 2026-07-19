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
BASE_DROP_PENALTY = 5_000_000
# Fixed cost per used vehicle, nudging the solver to consolidate.
VEHICLE_FIXED_COST = 300_000
TIME_HORIZON_MIN = 24 * 60  # planning horizon (minutes)


def _scaled_cost(matrix: DistanceMatrix, objective: OptimizationObjective, i: int, j: int) -> int:
    """Arc cost in integer units for the chosen objective."""
    dist_m = int(matrix.distances_km[i][j] * 1000)
    dur_s = int(matrix.durations_min[i][j] * 60)
    if objective == OptimizationObjective.MIN_DISTANCE:
        return dist_m
    if objective == OptimizationObjective.MIN_TIME:
        return dur_s
    # BALANCED: normalise time to metre-equivalents (avg speed) and average.
    return (dist_m + dur_s * 1000 // 60) // 2  # rough metre-equivalent blend


def _infer_reason(order: OrderNode, vehicles: list[VehicleInput]) -> str:
    max_cap = max((v.capacity_kg for v in vehicles), default=0)
    if order.demand_kg > max_cap:
        return UnassignedReason.CAPACITY_EXCEEDED.value
    if order.tw_start_min is not None or order.tw_end_min is not None:
        return UnassignedReason.TIME_WINDOW_INFEASIBLE.value
    return UnassignedReason.NO_AVAILABLE_VEHICLE.value


def solve_vrp(
    depot: Coord,
    orders: list[OrderNode],
    vehicles: list[VehicleInput],
    objective: OptimizationObjective = OptimizationObjective.BALANCED,
) -> SolveResult:
    if not orders:
        return SolveResult(routes=[], unassigned=[], objective_value=0.0, matrix_source="none")
    if not vehicles:
        return SolveResult(
            routes=[],
            unassigned=[
                UnassignedOrder(o.order_id, o.order_number, UnassignedReason.NO_AVAILABLE_VEHICLE.value)
                for o in orders
            ],
            objective_value=0.0,
            matrix_source="none",
        )

    coords: list[Coord] = [depot] + [o.coord for o in orders]
    matrix = build_matrix(coords)
    n = len(coords)
    num_vehicles = len(vehicles)

    manager = pywrapcp.RoutingIndexManager(n, num_vehicles, 0)
    routing = pywrapcp.RoutingModel(manager)

    # --- Arc cost ---
    def cost_cb(from_index: int, to_index: int) -> int:
        i = manager.IndexToNode(from_index)
        j = manager.IndexToNode(to_index)
        return _scaled_cost(matrix, objective, i, j)

    cost_idx = routing.RegisterTransitCallback(cost_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(cost_idx)
    for v in range(num_vehicles):
        routing.SetFixedCostOfVehicle(VEHICLE_FIXED_COST, v)

    # --- Capacity dimension ---
    demands = [0] + [max(0, math.ceil(o.demand_kg)) for o in orders]

    def demand_cb(from_index: int) -> int:
        return demands[manager.IndexToNode(from_index)]

    demand_idx = routing.RegisterUnaryTransitCallback(demand_cb)
    capacities = [int(v.capacity_kg) for v in vehicles]
    routing.AddDimensionWithVehicleCapacity(
        demand_idx, 0, capacities, True, "Capacity"
    )

    # --- Distance dimension (per-vehicle max) ---
    def dist_cb(from_index: int, to_index: int) -> int:
        i = manager.IndexToNode(from_index)
        j = manager.IndexToNode(to_index)
        return int(matrix.distances_km[i][j] * 1000)

    dist_idx = routing.RegisterTransitCallback(dist_cb)
    max_dist_global = max(int(v.max_route_distance_km * 1000) for v in vehicles)
    routing.AddDimension(dist_idx, 0, max_dist_global, True, "Distance")
    dist_dim = routing.GetDimensionOrDie("Distance")
    for v in range(num_vehicles):
        dist_dim.CumulVar(routing.End(v)).SetMax(int(vehicles[v].max_route_distance_km * 1000))

    # --- Time dimension (travel + service, enforces windows) ---
    service = [0] + [int(o.service_time_min) for o in orders]

    def time_cb(from_index: int, to_index: int) -> int:
        i = manager.IndexToNode(from_index)
        j = manager.IndexToNode(to_index)
        return int(matrix.durations_min[i][j]) + service[i]

    time_idx = routing.RegisterTransitCallback(time_cb)
    routing.AddDimension(time_idx, TIME_HORIZON_MIN, TIME_HORIZON_MIN, False, "Time")
    time_dim = routing.GetDimensionOrDie("Time")
    for node in range(1, n):
        order = orders[node - 1]
        index = manager.NodeToIndex(node)
        start = order.tw_start_min if order.tw_start_min is not None else 0
        end = order.tw_end_min if order.tw_end_min is not None else TIME_HORIZON_MIN
        time_dim.CumulVar(index).SetRange(int(start), int(end))
    for v in range(num_vehicles):
        time_dim.CumulVar(routing.Start(v)).SetRange(0, TIME_HORIZON_MIN)

    # --- Optional visits (priority-scaled drop penalty) ---
    for node in range(1, n):
        order = orders[node - 1]
        penalty = BASE_DROP_PENALTY * max(1, order.priority_weight)
        routing.AddDisjunction([manager.NodeToIndex(node)], penalty)

    # --- Search parameters ---
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromSeconds(max(1, settings.solver_time_limit_seconds))

    solution = routing.SolveWithParameters(params)

    if solution is None:
        return SolveResult(
            routes=[],
            unassigned=[
                UnassignedOrder(o.order_id, o.order_number, _infer_reason(o, vehicles))
                for o in orders
            ],
            objective_value=0.0,
            matrix_source=matrix.source,
        )

    routes: list[SolvedRoute] = []
    served: set[int] = set()

    for v in range(num_vehicles):
        index = routing.Start(v)
        if routing.IsEnd(solution.Value(routing.NextVar(index))):
            continue  # unused vehicle
        veh = vehicles[v]
        stops: list[SolvedStop] = []
        seq = 0
        total_dist = 0.0
        total_load = 0.0
        prev_node = 0
        index = solution.Value(routing.NextVar(index))
        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            order = orders[node - 1]
            leg_km = matrix.distances_km[prev_node][node]
            total_dist += leg_km
            total_load += order.demand_kg
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
        total_dist += matrix.distances_km[prev_node][0]
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
