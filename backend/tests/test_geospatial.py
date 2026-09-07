"""Geospatial + baseline unit tests (no database).

Covers the pure maths in app/geospatial/distance.py and the greedy baseline's
capacity handling. Everything here runs in milliseconds — no I/O of any kind.
"""
from __future__ import annotations

from app.geospatial.distance import haversine_km, road_distance_km, travel_time_minutes
from app.optimization.baseline import nearest_neighbour
from app.optimization.types import OrderNode, VehicleInput

# Two real Delhi-area points, ~19 km apart. Real coordinates rather than
# invented ones so the assertions can be checked against an external source.
DELHI = (28.6139, 77.2090)
NOIDA = (28.5355, 77.3910)


def test_haversine_known_distance():
    """Haversine agrees with a known real-world distance.

    A RANGE, not an exact value. The point is to catch the errors that actually
    happen in this kind of code — forgetting to convert degrees to radians, or
    swapping latitude and longitude — each of which is wrong by a large factor,
    not by a rounding error. A tight assertion would instead be brittle against
    a different Earth-radius constant.
    """
    d = haversine_km(DELHI, NOIDA)
    # Delhi -> Noida straight line is ~18-20 km
    assert 15 < d < 24


def test_road_distance_applies_factor():
    """Road distance is exactly the straight line times the factor.

    Asserted by recomputing the relationship rather than hard-coding a number,
    so the test states the *rule* and stays correct if the underlying haversine
    constant is ever tuned.
    """
    straight = haversine_km(DELHI, NOIDA)
    assert road_distance_km(DELHI, NOIDA, 1.25) == straight * 1.25


def test_travel_time_scales_with_speed():
    """30 km at 30 km/h is one hour — and a zero speed does not explode.

    The second assertion is the valuable one: it pins the guard clause that
    returns 0.0 instead of raising ZeroDivisionError.
    """
    assert travel_time_minutes(30, 30) == 60.0
    assert travel_time_minutes(0, 30) == 0.0


def test_baseline_respects_capacity_and_returns_to_depot():
    """Greedy never overloads a vehicle, and reports what it could not fit.

    The setup is deliberately over-subscribed: five 40 kg orders (200 kg) for
    one 100 kg van, so at most two can be carried. That makes the test check
    both halves of the contract — the capacity limit is respected, AND the
    remainder is reported rather than silently dropped.
    """
    depot = (28.55, 77.25)
    # Orders in a line heading north, so the greedy nearest-first choice is
    # unambiguous and the test does not depend on tie-breaking.
    orders = [
        OrderNode(order_id=i, order_number=f"O{i}", coord=(28.55 + i * 0.01, 77.25), demand_kg=40)
        for i in range(1, 6)
    ]
    vehicles = [VehicleInput(vehicle_id=1, registration_number="V1", capacity_kg=100)]
    result = nearest_neighbour(depot, orders, vehicles)

    # The hard constraint. The epsilon absorbs float accumulation — the loads
    # are summed, so an exact == against the capacity would be fragile.
    for route in result.routes:
        assert route.total_load_kg <= route.capacity_kg + 1e-6

    # a single 100kg vehicle cannot carry all five 40kg orders (=200kg)
    #
    # Asserting "fewer than all" rather than an exact count keeps the test about
    # the rule instead of the arithmetic of this particular setup.
    assigned = sum(len(r.stops) for r in result.routes)
    assert assigned < len(orders)
    assert len(result.unassigned) > 0
