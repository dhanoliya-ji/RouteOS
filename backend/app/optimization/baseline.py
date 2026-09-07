"""Naive nearest-neighbour baseline used to quantify optimizer improvement.

Greedy strategy: for each vehicle (in turn), repeatedly hop to the nearest
unserved order that still fits remaining capacity and distance budget, until the
vehicle is full or nothing fits, then return to depot. This mimics a dispatcher
routing by hand and provides an honest, computed comparison point — no numbers
are hard-coded.

Why a deliberately dumb algorithm is worth keeping
--------------------------------------------------
Two reasons, and both matter:

1. **It makes "we saved 23%" a measurement.** Every optimization run computes
   this baseline over the same orders, so the reported improvement is against a
   real alternative rather than an invented figure.

2. **It is sometimes the better plan.** The OR-Tools solver is a heuristic under
   a wall-clock budget; starved of CPU on a large problem it can time out while
   still worse than greedy. The service runs both and dispatches the winner —
   see services/optimization_service._better_plan.

Greedy costs O(n²) per vehicle and finishes in milliseconds, which is what makes
running it on *every* optimization affordable.

The weakness, which is the point
--------------------------------
Greedy takes the best immediate step and never reconsiders. So it hoovers up a
cheap cluster near the depot and strands whatever is far away:

    depot ──► o1 ──► o2 ──► o3 ─────────────────────────────► o4
                                    one huge leg              │
          ◄─────────────────────────────────────────────────  ┘

A real solver sees the whole problem at once and folds the distant order into a
sensible loop instead. That gap is the improvement the optimizer is claiming.
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


def _nearest_that_fits(
    current: Coord,
    remaining: list[OrderNode],
    load_kg: float,
    capacity_kg: float,
    factor: float,
) -> OrderNode | None:
    """The closest unserved order that still fits in the vehicle. None if none do.

    This is the "greedy" in nearest-neighbour: closest *right now*, with no
    regard for what it leaves behind.

    Two steps, in this order:
      1. filter to what the vehicle can still carry (a capacity check is
         cheaper than a distance calculation, so it goes first)
      2. of those, take the nearest

    Ties go to whichever order appears earlier in `remaining`, because `min`
    returns the first minimal element. Callers therefore get a deterministic
    answer for a given input ordering, which is what makes a benchmark
    reproducible.
    """
    affordable = [o for o in remaining if load_kg + o.demand_kg <= capacity_kg]
    if not affordable:
        return None
    return min(affordable, key=lambda o: road_distance_km(current, o.coord, factor))


def _build_one_route(
    depot: Coord,
    vehicle: VehicleInput,
    remaining: list[OrderNode],
    factor: float,
    speed: float,
) -> SolvedRoute | None:
    """Fill one vehicle greedily from `remaining`, returning its route.

    **Mutates `remaining`**: every order this vehicle takes is removed from the
    list, so the next vehicle only sees what is left. That shared shrinking list
    is what stops two vehicles being given the same order.

    Returns None if the vehicle picked nothing up — an empty route is not a
    route, and counting one would inflate the "vehicles used" figure the plan
    comparison depends on.
    """
    # The running state of one vehicle's day.
    position = depot          # where the van is now
    load_kg = 0.0             # what it is carrying
    distance_km = 0.0         # how far it has driven so far
    elapsed_min = 0.0         # driving + service time so far
    stops: list[SolvedStop] = []

    while True:
        # --- pick the next stop, or stop picking -----------------------------
        nxt = _nearest_that_fits(position, remaining, load_kg, vehicle.capacity_kg, factor)
        if nxt is None:
            break  # nothing left that fits — the van is effectively full

        leg_km = road_distance_km(position, nxt.coord, factor)

        # --- can it still get home afterwards? -------------------------------
        # Budget the return leg BEFORE committing to this stop, or the vehicle
        # could drive to its limit and be unable to come back.
        #
        # Note this gives up entirely rather than trying the next-nearest
        # candidate that might still fit the budget. That is greedy behaving
        # like greedy, and it is left as-is on purpose: this function's job is
        # to be a naive baseline, not a second optimizer.
        trip_home_km = road_distance_km(nxt.coord, depot, factor)
        if distance_km + leg_km + trip_home_km > vehicle.max_route_distance_km:
            break

        # --- commit to the stop ----------------------------------------------
        distance_km += leg_km
        elapsed_min += travel_time_minutes(leg_km, speed) + nxt.service_time_min
        load_kg += nxt.demand_kg
        stops.append(
            SolvedStop(
                order_id=nxt.order_id,
                order_number=nxt.order_number,
                # 1-based, assigned as we go, so it is the true visiting order.
                stop_sequence=len(stops) + 1,
                coord=nxt.coord,
                distance_from_previous_km=round(leg_km, 3),
                load_kg=nxt.demand_kg,
                eta_minutes_from_start=round(elapsed_min, 1),
            )
        )
        # Claim the order so no later vehicle can take it, and move the van.
        remaining.remove(nxt)
        position = nxt.coord

    if not stops:
        return None

    # --- drive home --------------------------------------------------------
    # The return leg is part of the route's cost but is not a stop, so it is
    # added to the totals only.
    return_km = road_distance_km(position, depot, factor)
    distance_km += return_km
    elapsed_min += travel_time_minutes(return_km, speed)

    return SolvedRoute(
        vehicle_id=vehicle.vehicle_id,
        registration_number=vehicle.registration_number,
        total_distance_km=round(distance_km, 3),
        estimated_duration_minutes=round(elapsed_min, 1),
        total_load_kg=round(load_kg, 2),
        capacity_kg=vehicle.capacity_kg,
        stops=stops,
    )


def nearest_neighbour(
    depot: Coord, orders: list[OrderNode], vehicles: list[VehicleInput]
) -> SolveResult:
    """Greedily assign orders to vehicles, one vehicle at a time.

        for each vehicle:
            fill it from what is left
        whatever is still unserved -> unassigned

    Vehicles are filled **sequentially, not in parallel**: the first vehicle
    takes the best of everything, the second gets the leftovers, and so on. That
    is precisely why the result is beatable — a solver balances across vehicles
    instead.
    """
    factor = settings.road_distance_factor
    speed = settings.average_speed_kmh

    # A copy: this gets consumed as orders are claimed, and the caller's list
    # must not be mutated (optimization_service passes the same list to the
    # real solver, and a shortened one would silently change that problem).
    remaining = list(orders)
    routes: list[SolvedRoute] = []

    for vehicle in vehicles:
        if not remaining:
            break  # everything is served; leave the rest of the fleet idle
        route = _build_one_route(depot, vehicle, remaining, factor, speed)
        if route is not None:
            routes.append(route)

    # Anything still in `remaining` had no vehicle with room for it. Greedy
    # cannot distinguish "too heavy for any van" from "the fleet filled up", so
    # it reports the general reason; the real solver's _infer_reason is more
    # specific.
    unassigned = [
        UnassignedOrder(o.order_id, o.order_number, "NO_AVAILABLE_VEHICLE") for o in remaining
    ]

    # objective_value is 0.0 because greedy has no objective function — it never
    # scores a solution, it just builds one. Comparison against the solver is
    # done on distance and vehicle count instead (see _plan_cost).
    return SolveResult(routes=routes, unassigned=unassigned, objective_value=0.0, matrix_source="haversine")
