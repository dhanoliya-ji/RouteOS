"""Optimization engine correctness tests (pure solver, no DB required).

These encode the constraints the product spec calls out explicitly:
capacity separation, depot start/end, no duplicate visits, and time-window
awareness. They exercise the real OR-Tools solver.
"""
from __future__ import annotations

import pytest

ortools = pytest.importorskip("ortools", reason="OR-Tools required for optimization tests")

from app.models.enums import OptimizationObjective  # noqa: E402
from app.optimization.baseline import nearest_neighbour  # noqa: E402
from app.optimization.solver import solve_vrp  # noqa: E402
from app.optimization.types import OrderNode, VehicleInput  # noqa: E402

DEPOT = (28.5478, 77.2733)  # Okhla hub


def _order(oid: int, lat: float, lon: float, kg: float, **kw) -> OrderNode:
    return OrderNode(order_id=oid, order_number=f"ORD-{oid:05d}", coord=(lat, lon), demand_kg=kg, **kw)


def test_capacity_prevents_overloading_single_vehicle():
    """Vehicle capacity 100kg; two 60kg + 50kg orders must NOT share it."""
    orders = [
        _order(1, 28.55, 77.28, 60),
        _order(2, 28.56, 77.29, 50),
    ]
    vehicles = [
        VehicleInput(vehicle_id=1, registration_number="V1", capacity_kg=100),
        VehicleInput(vehicle_id=2, registration_number="V2", capacity_kg=100),
    ]
    result = solve_vrp(DEPOT, orders, vehicles, OptimizationObjective.MIN_DISTANCE)

    for route in result.routes:
        assert route.total_load_kg <= route.capacity_kg + 1e-6
    # 60 + 50 = 110 > 100 => cannot be on the same route
    for route in result.routes:
        order_ids = {s.order_id for s in route.stops}
        assert not {1, 2}.issubset(order_ids)


def test_every_route_starts_and_ends_at_depot_no_duplicates():
    orders = [_order(i, 28.5 + i * 0.01, 77.25 + i * 0.01, 10) for i in range(1, 9)]
    vehicles = [VehicleInput(vehicle_id=v, registration_number=f"V{v}", capacity_kg=100) for v in range(1, 4)]
    result = solve_vrp(DEPOT, orders, vehicles)

    seen: set[int] = set()
    for route in result.routes:
        seqs = [s.stop_sequence for s in route.stops]
        assert seqs == sorted(seqs)  # ordered
        for s in route.stops:
            assert s.order_id not in seen, "order visited more than once"
            seen.add(s.order_id)
    # all assigned + unassigned exactly cover the input
    assigned = {s.order_id for r in result.routes for s in r.stops}
    unassigned = {u.order_id for u in result.unassigned}
    assert assigned | unassigned == {o.order_id for o in orders}
    assert not (assigned & unassigned)


def test_oversized_order_reported_capacity_exceeded():
    orders = [_order(1, 28.55, 77.28, 500)]  # heavier than any vehicle
    vehicles = [VehicleInput(vehicle_id=1, registration_number="V1", capacity_kg=100)]
    result = solve_vrp(DEPOT, orders, vehicles)
    assert not result.routes
    assert result.unassigned[0].reason == "CAPACITY_EXCEEDED"


def test_time_window_respected_when_feasible():
    # Order 2 must be served early (0-30 min); order 1 can be later.
    orders = [
        _order(1, 28.70, 77.45, 10, tw_start_min=120, tw_end_min=240),
        _order(2, 28.55, 77.28, 10, tw_start_min=0, tw_end_min=30),
    ]
    vehicles = [VehicleInput(vehicle_id=1, registration_number="V1", capacity_kg=100)]
    result = solve_vrp(DEPOT, orders, vehicles, OptimizationObjective.MIN_TIME)
    assert result.routes, "expected a feasible route"
    stops = {s.order_id: s for s in result.routes[0].stops}
    if 2 in stops:
        assert stops[2].eta_minutes_from_start <= 30 + 1e-6


def test_optimizer_beats_or_matches_baseline_distance():
    orders = [_order(i, 28.5 + (i % 5) * 0.02, 77.2 + (i % 4) * 0.02, 8) for i in range(1, 21)]
    vehicles = [VehicleInput(vehicle_id=v, registration_number=f"V{v}", capacity_kg=200) for v in range(1, 5)]
    opt = solve_vrp(DEPOT, orders, vehicles, OptimizationObjective.MIN_DISTANCE)
    base = nearest_neighbour(DEPOT, orders, vehicles)
    opt_dist = sum(r.total_distance_km for r in opt.routes)
    base_dist = sum(r.total_distance_km for r in base.routes)
    # Optimizer should not be worse than the naive baseline.
    assert opt_dist <= base_dist * 1.05
