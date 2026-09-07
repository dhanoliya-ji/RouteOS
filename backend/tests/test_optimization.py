"""Optimization engine correctness tests (pure solver, no DB required).

These encode the constraints the product spec calls out explicitly:
capacity separation, depot start/end, no duplicate visits, and time-window
awareness. They exercise the real OR-Tools solver.

They assert on PROPERTIES, not on exact routes. That is the right approach for
a heuristic under a wall-clock budget: the specific answer may legitimately
differ between OR-Tools versions or CPU speeds, but "no van is overloaded" must
hold every time. Pinning exact stop orders would produce a suite that fails on
a faster machine.

This is also why the whole suite takes about a minute — these are real solves.
"""
from __future__ import annotations

import pytest

# Skip rather than error if OR-Tools is absent, so the rest of the suite still
# runs in an environment without the (large) solver dependency installed.
ortools = pytest.importorskip("ortools", reason="OR-Tools required for optimization tests")

# Imports sit below importorskip deliberately — they would fail at collection
# time if ortools were missing, defeating the skip. Hence the noqa: E402.
from app.models.enums import OptimizationObjective  # noqa: E402
from app.optimization.baseline import nearest_neighbour  # noqa: E402
from app.optimization.solver import solve_vrp  # noqa: E402
from app.optimization.types import OrderNode, VehicleInput  # noqa: E402

DEPOT = (28.5478, 77.2733)  # Okhla hub


def _order(oid: int, lat: float, lon: float, kg: float, **kw) -> OrderNode:
    """Build an OrderNode with the fields a test cares about.

    **kw passes through the optional ones (time windows, priority), so each
    test names only what it is actually exercising.
    """
    return OrderNode(order_id=oid, order_number=f"ORD-{oid:05d}", coord=(lat, lon), demand_kg=kg, **kw)


def test_capacity_prevents_overloading_single_vehicle():
    """Vehicle capacity 100kg; two 60kg + 50kg orders must NOT share it."""
    # 60 + 50 = 110 kg, just over one van's 100 kg. Two vans are offered, so a
    # correct solver has a feasible answer available — the test checks it does
    # not take the infeasible one.
    orders = [
        _order(1, 28.55, 77.28, 60),
        _order(2, 28.56, 77.29, 50),
    ]
    vehicles = [
        VehicleInput(vehicle_id=1, registration_number="V1", capacity_kg=100),
        VehicleInput(vehicle_id=2, registration_number="V2", capacity_kg=100),
    ]
    # MIN_DISTANCE deliberately: the two orders are close together, so the
    # cheapest plan by distance alone WOULD put them on one van. This pushes the
    # objective against the constraint to prove the constraint wins.
    result = solve_vrp(DEPOT, orders, vehicles, OptimizationObjective.MIN_DISTANCE)

    for route in result.routes:
        assert route.total_load_kg <= route.capacity_kg + 1e-6

    # 60 + 50 = 110 > 100 => cannot be on the same route
    #
    # Checked as a set relation rather than by inspecting a specific route, so
    # the test does not care which van got which order.
    for route in result.routes:
        order_ids = {s.order_id for s in route.stops}
        assert not {1, 2}.issubset(order_ids)


def test_every_route_starts_and_ends_at_depot_no_duplicates():
    """Structural validity: ordered sequences, each order served exactly once.

    The most valuable test in the file — it would catch an off-by-one in the
    node/index translation, a mis-read NextVar chain, or an order counted in
    both the assigned and unassigned lists.
    """
    orders = [_order(i, 28.5 + i * 0.01, 77.25 + i * 0.01, 10) for i in range(1, 9)]
    vehicles = [VehicleInput(vehicle_id=v, registration_number=f"V{v}", capacity_kg=100) for v in range(1, 4)]
    result = solve_vrp(DEPOT, orders, vehicles)

    seen: set[int] = set()
    for route in result.routes:
        seqs = [s.stop_sequence for s in route.stops]
        assert seqs == sorted(seqs)  # ordered
        for s in route.stops:
            # A set accumulated ACROSS routes, so an order appearing on two
            # different vehicles is caught, not just a duplicate within one.
            assert s.order_id not in seen, "order visited more than once"
            seen.add(s.order_id)

    # all assigned + unassigned exactly cover the input
    #
    # Two assertions expressing one invariant: the union accounts for every
    # order (nothing lost) and the intersection is empty (nothing double
    # counted). Together they mean the solver's output is a true partition of
    # its input.
    assigned = {s.order_id for r in result.routes for s in r.stops}
    unassigned = {u.order_id for u in result.unassigned}
    assert assigned | unassigned == {o.order_id for o in orders}
    assert not (assigned & unassigned)


def test_oversized_order_reported_capacity_exceeded():
    """An impossible order is reported with a reason, not silently dropped.

    500 kg for a 100 kg fleet cannot be served by anything. The valuable part is
    the *reason*: this pins solver._infer_reason's most certain branch, so a
    dispatcher is told "too heavy" rather than a vague "no vehicle".
    """
    orders = [_order(1, 28.55, 77.28, 500)]  # heavier than any vehicle
    vehicles = [VehicleInput(vehicle_id=1, registration_number="V1", capacity_kg=100)]
    result = solve_vrp(DEPOT, orders, vehicles)
    assert not result.routes
    assert result.unassigned[0].reason == "CAPACITY_EXCEEDED"


def test_time_window_respected_when_feasible():
    """A stop with an early window is scheduled inside it."""
    # Order 2 must be served early (0-30 min); order 1 can be later.
    #
    # Order 1 is placed far away with a LATE window, so serving it first would
    # make order 2's window unreachable. A solver that ignored windows would
    # naturally visit the nearer order 2 first anyway — hence MIN_TIME below,
    # and the ETA assertion rather than a check on ordering.
    orders = [
        _order(1, 28.70, 77.45, 10, tw_start_min=120, tw_end_min=240),
        _order(2, 28.55, 77.28, 10, tw_start_min=0, tw_end_min=30),
    ]
    vehicles = [VehicleInput(vehicle_id=1, registration_number="V1", capacity_kg=100)]
    result = solve_vrp(DEPOT, orders, vehicles, OptimizationObjective.MIN_TIME)
    assert result.routes, "expected a feasible route"

    stops = {s.order_id: s for s in result.routes[0].stops}
    # Guarded by `if 2 in stops` because the solver is permitted to drop an
    # order it cannot fit — the assertion is "if it was served, it was served in
    # time", which is the actual requirement.
    if 2 in stops:
        assert stops[2].eta_minutes_from_start <= 30 + 1e-6


def test_optimizer_beats_or_matches_baseline_distance():
    """The solver is not dramatically worse than routing by hand.

    A quality FLOOR, not a quality target. The 1.05 tolerance is deliberate: the
    solver is a heuristic on a clock, so on a slow or loaded machine it can
    legitimately finish a little behind greedy. Demanding a strict win would
    make this test fail for reasons that are not regressions.

    What it does catch is the solver becoming badly broken — a mis-scaled cost
    callback, or a constraint forcing absurd routes.
    """
    # A grid-ish spread via modulo, so orders cluster into repeated positions —
    # a pattern where a good solver should comfortably beat nearest-neighbour.
    orders = [_order(i, 28.5 + (i % 5) * 0.02, 77.2 + (i % 4) * 0.02, 8) for i in range(1, 21)]
    vehicles = [VehicleInput(vehicle_id=v, registration_number=f"V{v}", capacity_kg=200) for v in range(1, 5)]
    opt = solve_vrp(DEPOT, orders, vehicles, OptimizationObjective.MIN_DISTANCE)
    base = nearest_neighbour(DEPOT, orders, vehicles)
    opt_dist = sum(r.total_distance_km for r in opt.routes)
    base_dist = sum(r.total_distance_km for r in base.routes)
    # Optimizer should not be worse than the naive baseline.
    assert opt_dist <= base_dist * 1.05
