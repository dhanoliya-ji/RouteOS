"""Plan-portfolio selection tests (no database).

The VRP solver is a heuristic under a wall-clock budget. On a large instance
with little CPU it can finish while still worse than the greedy baseline, so the
service dispatches whichever plan actually wins. These tests pin that rule.
"""
from __future__ import annotations

from app.optimization.types import SolvedRoute, SolvedStop, SolveResult, UnassignedOrder
from app.services.optimization_service import _better_plan, _plan_cost


def _stop(i: int) -> SolvedStop:
    return SolvedStop(
        order_id=i, order_number=f"O{i}", stop_sequence=i, coord=(28.5, 77.2),
        distance_from_previous_km=1.0, load_kg=1.0, eta_minutes_from_start=10.0,
    )


def _plan(distance_per_route: list[float], stops_per_route: list[int]) -> SolveResult:
    routes = []
    n = 0
    for idx, (km, count) in enumerate(zip(distance_per_route, stops_per_route), start=1):
        stops = []
        for _ in range(count):
            n += 1
            stops.append(_stop(n))
        routes.append(
            SolvedRoute(
                vehicle_id=idx, registration_number=f"V{idx}", total_distance_km=km,
                estimated_duration_minutes=km * 2, total_load_kg=count, capacity_kg=1000.0,
                stops=stops,
            )
        )
    return SolveResult(routes=routes, unassigned=[], objective_value=0.0, matrix_source="test")


class TestBetterPlan:
    def test_solver_wins_when_it_beats_baseline_on_distance_and_vehicles(self):
        # The healthy case: 672 km on 6 vehicles vs 723 km on 7.
        baseline = _plan([103.3] * 7, [10] * 7)
        solver = _plan([112.0] * 6, [11, 12, 12, 12, 12, 11])
        chosen, source = _better_plan(baseline, solver, 70)
        assert source == "solver"
        assert chosen is solver

    def test_baseline_wins_when_the_solver_ran_out_of_time(self):
        # Observed on a CPU-starved instance: solver returns a longer plan on the
        # same vehicle count. Dispatching it would be worse than routing by hand.
        baseline = _plan([103.3] * 7, [10] * 7)   # 723.1 km, 7 vehicles
        solver = _plan([104.2] * 7, [10] * 7)     # 729.4 km, 7 vehicles
        chosen, source = _better_plan(baseline, solver, 70)
        assert source == "baseline"
        assert chosen is baseline

    def test_serving_more_orders_beats_a_cheaper_plan(self):
        # A shorter plan that strands deliveries is not better.
        baseline = _plan([50.0], [5])              # cheap, serves 5
        solver = _plan([200.0] * 3, [10, 10, 10])  # pricier, serves 30
        chosen, source = _better_plan(baseline, solver, 30)
        assert source == "solver"

        # ...and the same rule protects the baseline when the solver drops orders.
        baseline2 = _plan([200.0] * 3, [10, 10, 10])
        solver2 = _plan([50.0], [5])
        chosen2, source2 = _better_plan(baseline2, solver2, 30)
        assert source2 == "baseline"
        assert chosen2 is baseline2

    def test_solver_preferred_on_an_exact_tie(self):
        baseline = _plan([100.0] * 3, [5, 5, 5])
        solver = _plan([100.0] * 3, [5, 5, 5])
        _, source = _better_plan(baseline, solver, 15)
        assert source == "solver"

    def test_fewer_vehicles_can_justify_extra_distance(self):
        # One fewer vehicle is worth 300 km under the model's own trade-off, so a
        # plan 100 km longer on one fewer vehicle should still win.
        baseline = _plan([100.0] * 4, [5] * 4)   # 400 km, 4 vehicles -> 1600
        solver = _plan([166.6] * 3, [7, 7, 6])   # 499.8 km, 3 vehicles -> 1399.8
        _, source = _better_plan(baseline, solver, 20)
        assert source == "solver"

    def test_plan_cost_prices_a_vehicle_at_300km(self):
        one_vehicle = _plan([10.0], [1])
        two_vehicles = _plan([5.0, 5.0], [1, 1])
        assert _plan_cost(one_vehicle) == 310.0
        assert _plan_cost(two_vehicles) == 610.0
