"""Dashboard summary tests against a real database (Tier 2).

Skips cleanly when there is no test database — see tests/conftest.py.

Written around `on_time_delivery_rate`, which until recently counted every
DELIVERY_COMPLETED event and divided by delivered orders — so it reported ~100%
whatever the timings were, and a clamp hid the rest. These are the tests that
would have caught it: several deliberately arrange a rate that is NOT 100%, and
the old implementation could not have produced any of them.

The metric now answers "of the deliveries that carried a promised window, what
fraction met it".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.enums import OrderStatus, RouteStatus, RouteStopStatus, VehicleStatus
from tests.conftest import requires_db

pytestmark = requires_db

API = "/api/v1"

# A fixed instant, so "on time" and "late" are unambiguous rather than relative
# to when the suite happens to run.
NOON = datetime(2026, 1, 14, 12, 0, tzinfo=timezone.utc)


async def _delivery(db, depot, *, window_end, arrived_at, n):
    """One delivered stop, with a promised deadline and a recorded arrival.

    Builds the whole chain the metric joins across — order, vehicle, route,
    stop — because the rate is meaningless without all four.

    `window_end=None` models an order that was never promised anything;
    `arrived_at=None` models a stop not yet delivered.
    """
    from app.geospatial.queries import make_point
    from app.models.order import Order
    from app.models.route import Route, RouteStop
    from app.models.vehicle import Vehicle

    order = Order(
        order_number=f"ORD-W{n:04d}",
        customer_name=f"Customer {n}",
        delivery_address=f"{n} Test Street",
        latitude=depot.latitude + 0.01,
        longitude=depot.longitude + 0.01,
        location=make_point(depot.latitude + 0.01, depot.longitude + 0.01),
        weight_kg=5.0,
        status=OrderStatus.DELIVERED if arrived_at else OrderStatus.OUT_FOR_DELIVERY,
        delivery_window_start=NOON - timedelta(hours=3) if window_end else None,
        delivery_window_end=window_end,
        depot_id=depot.id,
    )
    vehicle = Vehicle(
        registration_number=f"DLW{n:04d}",
        driver_name=f"Driver {n}",
        capacity_kg=500.0,
        status=VehicleStatus.IN_TRANSIT,
        home_depot_id=depot.id,
    )
    db.add_all([order, vehicle])
    await db.flush()

    route = Route(
        route_code=f"RT-W{n:04d}",
        vehicle_id=vehicle.id,
        depot_id=depot.id,
        status=RouteStatus.ACTIVE,
    )
    db.add(route)
    await db.flush()

    db.add(
        RouteStop(
            route_id=route.id,
            order_id=order.id,
            stop_sequence=1,
            latitude=order.latitude,
            longitude=order.longitude,
            estimated_arrival=window_end,
            actual_arrival=arrived_at,
            status=RouteStopStatus.COMPLETED if arrived_at else RouteStopStatus.PENDING,
        )
    )
    await db.commit()


async def _rate(client) -> float:
    """The summary's on-time figure, bypassing the Redis cache.

    `use_cache` is not exposed over HTTP, and the endpoint caches for 15s — so
    without this the second test in a run would read the first one's answer.
    """
    res = await client.get(f"{API}/dashboard/summary")
    assert res.status_code == 200, res.text
    return res.json()["on_time_delivery_rate"]


@pytest.fixture(autouse=True)
def no_cache(monkeypatch):
    """Disable the Redis cache for these tests.

    Redis is not running in the test environment, so cache_get_json already
    returns None — but stubbing it makes the independence explicit rather than
    accidental, and keeps these tests correct on a machine where Redis IS up.
    """
    from app.services import dashboard_service

    async def _miss(_key):
        return None

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(dashboard_service, "cache_get_json", _miss)
    monkeypatch.setattr(dashboard_service, "cache_set_json", _noop)


class TestOnTimeRate:
    async def test_reports_100_when_nothing_has_been_promised(self, as_dispatcher, depot):
        """An empty system has broken no promises.

        Also the guard against dividing by zero.
        """
        assert await _rate(as_dispatcher) == 100.0

    async def test_a_delivery_inside_its_window_is_on_time(self, as_dispatcher, depot, db):
        await _delivery(db, depot, window_end=NOON, arrived_at=NOON - timedelta(minutes=20), n=1)
        assert await _rate(as_dispatcher) == 100.0

    async def test_a_delivery_after_its_window_is_late(self, as_dispatcher, depot, db):
        """The case the old implementation could not produce.

        One delivery, arriving an hour past its deadline. Counting completion
        events would have reported 100%; the correct answer is 0.
        """
        await _delivery(db, depot, window_end=NOON, arrived_at=NOON + timedelta(hours=1), n=1)
        assert await _rate(as_dispatcher) == 0.0

    async def test_mixed_deliveries_give_a_real_fraction(self, as_dispatcher, depot, db):
        # Three on time, one late.
        for i in range(3):
            await _delivery(
                db, depot, window_end=NOON, arrived_at=NOON - timedelta(minutes=10), n=i
            )
        await _delivery(db, depot, window_end=NOON, arrived_at=NOON + timedelta(minutes=30), n=9)

        assert await _rate(as_dispatcher) == 75.0

    async def test_arriving_exactly_on_the_deadline_counts_as_on_time(
        self, as_dispatcher, depot, db
    ):
        # <= rather than <. A delivery at the last promised second met the
        # promise; treating it as late would be a mean reading of the contract.
        await _delivery(db, depot, window_end=NOON, arrived_at=NOON, n=1)
        assert await _rate(as_dispatcher) == 100.0

    async def test_orders_with_no_window_are_excluded_entirely(
        self, as_dispatcher, depot, db
    ):
        """An order nobody promised anything about cannot be late.

        Counting it as on time would inflate the rate purely by adding
        unconstrained work — which is close to the failure the old
        implementation had, and worth guarding against directly.
        """
        # One late delivery WITH a window...
        await _delivery(db, depot, window_end=NOON, arrived_at=NOON + timedelta(hours=2), n=1)
        # ...and five delivered with no window at all.
        for i in range(2, 7):
            await _delivery(db, depot, window_end=None, arrived_at=NOON, n=i)

        # Still 0%: the only measurable delivery was late. Were the windowless
        # ones counted, this would read 83.3%.
        assert await _rate(as_dispatcher) == 0.0

    async def test_undelivered_stops_are_excluded(self, as_dispatcher, depot, db):
        """A stop still in flight is neither on time nor late.

        Counting a pending stop as late would make the rate sag as a simulation
        ran and recover as it finished — a metric that moves for the wrong
        reason.
        """
        await _delivery(db, depot, window_end=NOON, arrived_at=NOON - timedelta(minutes=5), n=1)
        # Two more promised but not yet arrived.
        for i in (2, 3):
            await _delivery(db, depot, window_end=NOON, arrived_at=None, n=i)

        assert await _rate(as_dispatcher) == 100.0

    async def test_the_rate_never_exceeds_100(self, as_dispatcher, depot, db):
        """There is no clamp any more, so this must hold by construction.

        The old code needed min(rate, 100.0) because an order re-delivered after
        a failure writes two events. Counting stops against the same joined set
        removes the possibility rather than hiding it.
        """
        for i in range(5):
            await _delivery(
                db, depot, window_end=NOON, arrived_at=NOON - timedelta(minutes=1), n=i
            )
        assert await _rate(as_dispatcher) == 100.0


class TestSummaryShape:
    async def test_returns_every_tile_the_dashboard_renders(self, as_dispatcher, depot):
        res = await as_dispatcher.get(f"{API}/dashboard/summary")
        body = res.json()
        for key in (
            "total_orders", "pending", "out_for_delivery", "delivered_today",
            "active_vehicles", "available_vehicles", "total_distance_today_km",
            "active_routes", "on_time_delivery_rate",
        ):
            assert key in body, f"the summary is missing {key}"

    async def test_counts_reflect_the_data(self, as_dispatcher, depot, db):
        await _delivery(db, depot, window_end=NOON, arrived_at=NOON, n=1)
        body = (await as_dispatcher.get(f"{API}/dashboard/summary")).json()
        assert body["total_orders"] == 1
        # The helper marks a vehicle IN_TRANSIT, which is what "active" means —
        # ASSIGNED vans are committed but stationary and count as neither.
        assert body["active_vehicles"] == 1
        assert body["available_vehicles"] == 0
        assert body["active_routes"] == 1

    async def test_activity_feed_is_reachable(self, as_dispatcher):
        res = await as_dispatcher.get(f"{API}/dashboard/activity")
        assert res.status_code == 200
        assert isinstance(res.json(), list)
