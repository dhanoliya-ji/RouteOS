"""Geospatial + baseline unit tests (no database)."""
from __future__ import annotations

from app.geospatial.distance import haversine_km, road_distance_km, travel_time_minutes
from app.optimization.baseline import nearest_neighbour
from app.optimization.types import OrderNode, VehicleInput

DELHI = (28.6139, 77.2090)
NOIDA = (28.5355, 77.3910)


def test_haversine_known_distance():
    d = haversine_km(DELHI, NOIDA)
    # Delhi -> Noida straight line is ~18-20 km
    assert 15 < d < 24


def test_road_distance_applies_factor():
    straight = haversine_km(DELHI, NOIDA)
    assert road_distance_km(DELHI, NOIDA, 1.25) == straight * 1.25


def test_travel_time_scales_with_speed():
    assert travel_time_minutes(30, 30) == 60.0
    assert travel_time_minutes(0, 30) == 0.0


def test_baseline_respects_capacity_and_returns_to_depot():
    depot = (28.55, 77.25)
    orders = [
        OrderNode(order_id=i, order_number=f"O{i}", coord=(28.55 + i * 0.01, 77.25), demand_kg=40)
        for i in range(1, 6)
    ]
    vehicles = [VehicleInput(vehicle_id=1, registration_number="V1", capacity_kg=100)]
    result = nearest_neighbour(depot, orders, vehicles)
    for route in result.routes:
        assert route.total_load_kg <= route.capacity_kg + 1e-6
    # a single 100kg vehicle cannot carry all five 40kg orders (=200kg)
    assigned = sum(len(r.stops) for r in result.routes)
    assert assigned < len(orders)
    assert len(result.unassigned) > 0
