"""Optimization benchmark harness.

Generates synthetic order sets of increasing size and measures OR-Tools solve
time, distance improvement vs the naive baseline, and assignment rate. No DB
needed — this exercises the solver directly.

Usage:
    python -m scripts.benchmark
    python -m scripts.benchmark --sizes 50 100 250 500 --vehicles 12

The "no DB needed" part is the point: because the optimization package takes
plain dataclasses rather than ORM rows, the timings below measure the algorithm
alone, with no query or network noise mixed in.

The solve time is governed by SOLVER_TIME_LIMIT_SECONDS, so set it low for a
quick comparison:
    SOLVER_TIME_LIMIT_SECONDS=3 python -m scripts.benchmark
"""
from __future__ import annotations

import argparse
import random
import time

from app.models.enums import OptimizationObjective
from app.optimization.baseline import nearest_neighbour
from app.optimization.solver import solve_vrp
from app.optimization.types import OrderNode, VehicleInput

# The same Okhla hub the demo data uses, so benchmark geometry matches the app's.
DEPOT = (28.5478, 77.2733)


def _make_orders(n: int, rng: random.Random) -> list[OrderNode]:
    """Build `n` synthetic orders in a box around the depot.

    Uniform over a bounding box, unlike demo_geo's district clustering — a
    benchmark wants an even, comparable spread rather than realism, so that
    doubling `n` doubles the density instead of changing the shape of the
    problem.

    No time windows: this measures the capacity/distance model, and windows
    would make larger sizes infeasible and so incomparable.
    """
    orders = []
    for i in range(1, n + 1):
        orders.append(
            OrderNode(
                order_id=i,
                order_number=f"ORD-{i:05d}",
                # ~0.25 x 0.45 degrees, roughly 28 x 44 km — a metro-sized area.
                coord=(28.45 + rng.uniform(0, 0.25), 77.05 + rng.uniform(0, 0.45)),
                demand_kg=rng.uniform(1, 40),
                service_time_min=rng.choice([5, 10, 15]),
                priority_weight=rng.choice([1, 2, 4, 8]),
            )
        )
    return orders


def run(sizes: list[int], num_vehicles: int) -> None:
    """Solve each problem size twice — real solver and greedy — and tabulate."""
    # Fixed seed, so two runs of this script compare the SAME problems. Without
    # it, a change in solve time could just be a different random instance.
    rng = random.Random(123)

    # A uniform fleet here, unlike seed_data's mixed one: identical vehicles
    # keep the measurement about the routing, not about assignment to differing
    # capacities.
    vehicles = [
        VehicleInput(vehicle_id=v, registration_number=f"V{v}", capacity_kg=600, max_route_distance_km=250)
        for v in range(1, num_vehicles + 1)
    ]

    header = f"{'orders':>7} | {'solve(ms)':>10} | {'opt km':>9} | {'base km':>9} | {'gain %':>7} | {'assigned':>9}"
    print(header)
    print("-" * len(header))

    for n in sizes:
        # NOTE: orders are drawn from the shared `rng`, so each size gets a
        # different instance rather than the smaller set being a prefix of the
        # larger. Sizes are therefore comparable run-to-run, but not nested.
        orders = _make_orders(n, rng)

        # Time ONLY the solver. The baseline is measured outside the timer
        # because it runs in milliseconds and is the comparison point, not the
        # subject.
        t0 = time.perf_counter()
        opt = solve_vrp(DEPOT, orders, vehicles, OptimizationObjective.BALANCED)
        solve_ms = (time.perf_counter() - t0) * 1000

        base = nearest_neighbour(DEPOT, orders, vehicles)

        opt_km = sum(r.total_distance_km for r in opt.routes)
        base_km = sum(r.total_distance_km for r in base.routes)
        # Guard a zero baseline (nothing assignable), which would divide by zero.
        gain = (base_km - opt_km) / base_km * 100 if base_km else 0
        assigned = sum(len(r.stops) for r in opt.routes)

        # Read `assigned` against n: a high gain with a low assignment rate is
        # not a win — the solver may simply be delivering less.
        print(f"{n:>7} | {solve_ms:>10.0f} | {opt_km:>9.1f} | {base_km:>9.1f} | {gain:>7.1f} | {assigned:>4}/{n}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    # nargs="+" so several sizes are given in one go: --sizes 50 100 250
    parser.add_argument("--sizes", type=int, nargs="+", default=[50, 100, 250, 500])
    parser.add_argument("--vehicles", type=int, default=12)
    args = parser.parse_args()
    # No asyncio.run: nothing here touches the database, so the whole script is
    # synchronous — the clearest sign that this measures the solver in isolation.
    run(args.sizes, args.vehicles)
