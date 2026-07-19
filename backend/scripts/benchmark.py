"""Optimization benchmark harness.

Generates synthetic order sets of increasing size and measures OR-Tools solve
time, distance improvement vs the naive baseline, and assignment rate. No DB
needed — this exercises the solver directly.

Usage:
    python -m scripts.benchmark
    python -m scripts.benchmark --sizes 50 100 250 500 --vehicles 12
"""
from __future__ import annotations

import argparse
import random
import time

from app.models.enums import OptimizationObjective
from app.optimization.baseline import nearest_neighbour
from app.optimization.solver import solve_vrp
from app.optimization.types import OrderNode, VehicleInput

DEPOT = (28.5478, 77.2733)


def _make_orders(n: int, rng: random.Random) -> list[OrderNode]:
    orders = []
    for i in range(1, n + 1):
        orders.append(
            OrderNode(
                order_id=i,
                order_number=f"ORD-{i:05d}",
                coord=(28.45 + rng.uniform(0, 0.25), 77.05 + rng.uniform(0, 0.45)),
                demand_kg=rng.uniform(1, 40),
                service_time_min=rng.choice([5, 10, 15]),
                priority_weight=rng.choice([1, 2, 4, 8]),
            )
        )
    return orders


def run(sizes: list[int], num_vehicles: int) -> None:
    rng = random.Random(123)
    vehicles = [
        VehicleInput(vehicle_id=v, registration_number=f"V{v}", capacity_kg=600, max_route_distance_km=250)
        for v in range(1, num_vehicles + 1)
    ]
    header = f"{'orders':>7} | {'solve(ms)':>10} | {'opt km':>9} | {'base km':>9} | {'gain %':>7} | {'assigned':>9}"
    print(header)
    print("-" * len(header))
    for n in sizes:
        orders = _make_orders(n, rng)
        t0 = time.perf_counter()
        opt = solve_vrp(DEPOT, orders, vehicles, OptimizationObjective.BALANCED)
        solve_ms = (time.perf_counter() - t0) * 1000
        base = nearest_neighbour(DEPOT, orders, vehicles)
        opt_km = sum(r.total_distance_km for r in opt.routes)
        base_km = sum(r.total_distance_km for r in base.routes)
        gain = (base_km - opt_km) / base_km * 100 if base_km else 0
        assigned = sum(len(r.stops) for r in opt.routes)
        print(f"{n:>7} | {solve_ms:>10.0f} | {opt_km:>9.1f} | {base_km:>9.1f} | {gain:>7.1f} | {assigned:>4}/{n}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", type=int, nargs="+", default=[50, 100, 250, 500])
    parser.add_argument("--vehicles", type=int, default=12)
    args = parser.parse_args()
    run(args.sizes, args.vehicles)
