"""Naive nearest-neighbour baseline used to quantify optimizer improvement.

Greedy strategy: for each vehicle (in turn), repeatedly hop to the nearest
unserved order that still fits remaining capacity and distance budget, until the
vehicle is full or nothing fits, then return to depot. This mimics a dispatcher
routing by hand and provides an honest, computed comparison point — no numbers
are hard-coded.
"""
from __future__ import annotations

from app.core.config import settings
from app.geospatial.distance import road_distance_km, travel_time_minutes
from app.optimization.types import (
    Coord,
    OrderNode,
    SolvedRoute,
    SolvedStop,
    SolveResult,
    UnassignedOrder,
    VehicleInput,
)


def nearest_neighbour(
    depot: Coord, orders: list[OrderNode], vehicles: list[VehicleInput]
) -> SolveResult:
    factor = settings.road_distance_factor
    speed = settings.average_speed_kmh
    remaining = list(orders)
    routes: list[SolvedRoute] = []

    for veh in vehicles:
        if not remaining:
            break
        current = depot
        load = 0.0
        dist = 0.0
        stops: list[SolvedStop] = []
        elapsed = 0.0
        seq = 0
        while True:
            candidates = [o for o in remaining if load + o.demand_kg <= veh.capacity_kg]
            if not candidates:
                break
            nxt = min(candidates, key=lambda o: road_distance_km(current, o.coord, factor))
            leg = road_distance_km(current, nxt.coord, factor)
            if (dist + leg + road_distance_km(nxt.coord, depot, factor)) > veh.max_route_distance_km:
                break
            dist += leg
            elapsed += travel_time_minutes(leg, speed) + nxt.service_time_min
            load += nxt.demand_kg
            seq += 1
            stops.append(
                SolvedStop(
                    order_id=nxt.order_id,
                    order_number=nxt.order_number,
                    stop_sequence=seq,
                    coord=nxt.coord,
                    distance_from_previous_km=round(leg, 3),
                    load_kg=nxt.demand_kg,
                    eta_minutes_from_start=round(elapsed, 1),
                )
            )
            remaining.remove(nxt)
            current = nxt.coord
        if stops:
            dist += road_distance_km(current, depot, factor)
            elapsed += travel_time_minutes(road_distance_km(current, depot, factor), speed)
            routes.append(
                SolvedRoute(
                    vehicle_id=veh.vehicle_id,
                    registration_number=veh.registration_number,
                    total_distance_km=round(dist, 3),
                    estimated_duration_minutes=round(elapsed, 1),
                    total_load_kg=round(load, 2),
                    capacity_kg=veh.capacity_kg,
                    stops=stops,
                )
            )

    unassigned = [
        UnassignedOrder(o.order_id, o.order_number, "NO_AVAILABLE_VEHICLE") for o in remaining
    ]
    return SolveResult(routes=routes, unassigned=unassigned, objective_value=0.0, matrix_source="haversine")
