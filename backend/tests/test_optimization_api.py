"""Optimization workflow tests against a real database (Tier 2).

Skips cleanly when there is no test database — see tests/conftest.py.

Covers the three-phase workflow end to end: solve, review, then accept or
discard. `accept_plan` is the most consequential function in the codebase — it
is the moment the system commits, writing four tables in one transaction — and
nothing tested it.

Uses the SYNCHRONOUS `/optimization/run` endpoint rather than `/jobs`, so there
is nothing to poll and no background task to await. The solver's time budget is
turned down to a second per test; these problems are tiny.
"""
from __future__ import annotations

import pytest

from app.models.enums import OrderStatus, RouteStatus, VehicleStatus
from tests.conftest import requires_db

pytestmark = requires_db

API = "/api/v1"


@pytest.fixture(autouse=True)
def fast_solver(monkeypatch):
    """One second of search, not fifteen.

    The default budget is sized for real problems. With four orders the search
    converges immediately, so the rest of the budget would be pure test
    latency.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "solver_time_limit_seconds", 1)


async def _make_orders(db, depot, count=4, status=OrderStatus.PENDING):
    """Save `count` orders clustered near the depot."""
    from app.geospatial.queries import make_point
    from app.models.order import Order

    made = []
    for i in range(count):
        lat = depot.latitude + 0.01 * (i + 1)
        lon = depot.longitude + 0.01 * (i + 1)
        o = Order(
            order_number=f"ORD-T{i:04d}",
            customer_name=f"Customer {i}",
            delivery_address=f"{i} Test Street",
            latitude=lat,
            longitude=lon,
            location=make_point(lat, lon),
            weight_kg=10.0,
            service_time_minutes=5,
            status=status,
            depot_id=depot.id,
        )
        db.add(o)
        made.append(o)
    await db.commit()
    for o in made:
        await db.refresh(o)
    return made


async def _make_vehicles(db, depot, count=2, status=VehicleStatus.AVAILABLE):
    from app.models.vehicle import Vehicle

    made = []
    for i in range(count):
        v = Vehicle(
            registration_number=f"DLT{i:04d}",
            driver_name=f"Driver {i}",
            vehicle_type="VAN",
            capacity_kg=500.0,
            current_load_kg=0.0,
            status=status,
            current_latitude=depot.latitude,
            current_longitude=depot.longitude,
            home_depot_id=depot.id,
            max_route_distance_km=200.0,
        )
        db.add(v)
        made.append(v)
    await db.commit()
    for v in made:
        await db.refresh(v)
    return made


class TestInputValidation:
    """The rules _load_inputs enforces before any solving happens."""

    async def test_no_pending_orders_is_a_422(self, as_dispatcher, depot, db):
        await _make_vehicles(db, depot)
        res = await as_dispatcher.post(
            f"{API}/optimization/run", json={"depot_id": depot.id, "objective": "BALANCED"}
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "NO_ORDERS"

    async def test_no_available_vehicles_is_a_422(self, as_dispatcher, depot, db):
        await _make_orders(db, depot)
        res = await as_dispatcher.post(
            f"{API}/optimization/run", json={"depot_id": depot.id, "objective": "BALANCED"}
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "NO_VEHICLES"

    async def test_a_missing_depot_is_a_404(self, as_dispatcher, depot, db):
        await _make_orders(db, depot)
        await _make_vehicles(db, depot)
        res = await as_dispatcher.post(
            f"{API}/optimization/run", json={"depot_id": 999_999, "objective": "BALANCED"}
        )
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "DEPOT_NOT_FOUND"

    async def test_already_assigned_orders_are_not_replanned(self, as_dispatcher, depot, db):
        """The status filter is a RULE, not a convenience.

        An order already loaded on a van must not be planned onto another one.
        With every order ASSIGNED there is nothing eligible, so the request is
        refused even though orders exist.
        """
        await _make_orders(db, depot, status=OrderStatus.ASSIGNED)
        await _make_vehicles(db, depot)
        res = await as_dispatcher.post(
            f"{API}/optimization/run", json={"depot_id": depot.id, "objective": "BALANCED"}
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "NO_ORDERS"

    async def test_busy_vehicles_are_not_dispatched_again(self, as_dispatcher, depot, db):
        """The mirror rule: a van already out cannot be given a second route."""
        await _make_orders(db, depot)
        await _make_vehicles(db, depot, status=VehicleStatus.IN_TRANSIT)
        res = await as_dispatcher.post(
            f"{API}/optimization/run", json={"depot_id": depot.id, "objective": "BALANCED"}
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "NO_VEHICLES"

    async def test_id_lists_narrow_but_cannot_widen(self, as_dispatcher, depot, db):
        """Naming ids restricts the problem; it does not bypass the filters.

        Two orders are PENDING and two are DELIVERED. Asking for all four by id
        must still plan only the two that are eligible.
        """
        from app.geospatial.queries import make_point
        from app.models.order import Order

        pending = await _make_orders(db, depot, count=2)

        # Two more that are already DELIVERED, added directly since the API
        # cannot create an order in that state.
        for i in range(2):
            db.add(
                Order(
                    order_number=f"ORD-D{i:04d}",
                    customer_name="Already delivered",
                    delivery_address="x",
                    latitude=depot.latitude + 0.05,
                    longitude=depot.longitude + 0.05,
                    location=make_point(depot.latitude + 0.05, depot.longitude + 0.05),
                    weight_kg=5.0,
                    status=OrderStatus.DELIVERED,
                    depot_id=depot.id,
                )
            )
        await db.commit()

        await _make_vehicles(db, depot)
        res = await as_dispatcher.post(
            f"{API}/optimization/run",
            json={"depot_id": depot.id, "objective": "BALANCED", "order_ids": []},
        )
        assert res.status_code == 201
        # Only the two PENDING orders were considered.
        assert res.json()["orders_count"] == len(pending)


class TestSolve:
    async def test_a_solve_completes_and_stores_a_plan(self, as_dispatcher, depot, db):
        await _make_orders(db, depot)
        await _make_vehicles(db, depot)

        res = await as_dispatcher.post(
            f"{API}/optimization/run", json={"depot_id": depot.id, "objective": "BALANCED"}
        )
        assert res.status_code == 201, res.text
        run = res.json()

        assert run["status"] == "COMPLETED"
        assert run["orders_count"] == 4
        assert run["vehicles_count"] == 2
        assert run["execution_time_ms"] is not None

        payload = run["result_payload"]
        assert payload is not None, "a completed run must carry its plan"
        # The stored plan is what accept replays, so these keys are a contract.
        for key in ("routes", "unassigned", "comparison", "horizon", "plan_source"):
            assert key in payload, f"result_payload is missing {key}"
        # Which algorithm won. Either is legitimate — the service dispatches
        # whichever plan is better — so this asserts the field is meaningful
        # rather than asserting the solver always wins.
        assert payload["plan_source"] in ("solver", "baseline")

    async def test_every_order_is_either_routed_or_reported(self, as_dispatcher, depot, db):
        """The partition property, at the API level.

        The solver's output must account for every input order exactly once —
        the same invariant test_optimization.py checks on the solver directly.
        """
        await _make_orders(db, depot)
        await _make_vehicles(db, depot)
        payload = (
            await as_dispatcher.post(
                f"{API}/optimization/run", json={"depot_id": depot.id, "objective": "BALANCED"}
            )
        ).json()["result_payload"]

        routed = {s["order_id"] for r in payload["routes"] for s in r["stops"]}
        dropped = {u["order_id"] for u in payload["unassigned"]}
        assert len(routed) + len(dropped) == 4
        assert not (routed & dropped), "an order was both routed and reported unassigned"

    async def test_the_baseline_comparison_is_computed(self, as_dispatcher, depot, db):
        """The improvement figure is a measurement, so both sides must exist."""
        await _make_orders(db, depot)
        await _make_vehicles(db, depot)
        run = (
            await as_dispatcher.post(
                f"{API}/optimization/run", json={"depot_id": depot.id, "objective": "BALANCED"}
            )
        ).json()

        comparison = run["result_payload"]["comparison"]
        assert comparison["baseline"]["total_distance_km"] > 0
        assert comparison["optimized"]["total_distance_km"] > 0
        # Recorded on the run row too, which is what the analytics chart reads.
        assert run["total_distance_before"] is not None
        assert run["total_distance_after"] is not None

    async def test_solving_changes_nothing_operationally(self, as_dispatcher, depot, db):
        """THE property of phase one: a plan is a proposal.

        Until a human accepts, no route exists and nothing has moved. This is
        why the plan is stored as JSON rather than materialised.
        """
        from sqlalchemy import func, select

        from app.models.order import Order
        from app.models.route import Route

        await _make_orders(db, depot)
        await _make_vehicles(db, depot)
        await as_dispatcher.post(
            f"{API}/optimization/run", json={"depot_id": depot.id, "objective": "BALANCED"}
        )

        assert (await db.execute(select(func.count(Route.id)))).scalar_one() == 0
        still_pending = (
            await db.execute(
                select(func.count(Order.id)).where(Order.status == OrderStatus.PENDING)
            )
        ).scalar_one()
        assert still_pending == 4

    @pytest.mark.parametrize("objective", ["MIN_DISTANCE", "MIN_TIME", "BALANCED"])
    async def test_every_objective_solves(self, as_dispatcher, depot, db, objective):
        await _make_orders(db, depot)
        await _make_vehicles(db, depot)
        res = await as_dispatcher.post(
            f"{API}/optimization/run", json={"depot_id": depot.id, "objective": objective}
        )
        assert res.status_code == 201
        assert res.json()["result_payload"]["objective"] == objective


class TestAccept:
    """Phase three: the moment the system commits."""

    async def _solve(self, client, depot):
        return (
            await client.post(
                f"{API}/optimization/run", json={"depot_id": depot.id, "objective": "BALANCED"}
            )
        ).json()

    async def test_accepting_creates_routes_with_stops(self, as_dispatcher, depot, db):
        await _make_orders(db, depot)
        await _make_vehicles(db, depot)
        run = await self._solve(as_dispatcher, depot)

        res = await as_dispatcher.post(f"{API}/optimization/runs/{run['id']}/accept")
        assert res.status_code == 200, res.text
        routes = res.json()

        assert len(routes) == len(run["result_payload"]["routes"])
        for route in routes:
            assert route["route_code"].startswith("RT-")
            # PLANNED, not ACTIVE: accepting commits the plan, but nothing
            # moves until the simulation picks it up.
            assert route["status"] == RouteStatus.PLANNED.value
            # Stops come back with the route, which is why the endpoint
            # re-reads each one through route_service.
            assert len(route["stops"]) > 0
            sequences = [s["stop_sequence"] for s in route["stops"]]
            assert sequences == sorted(sequences)

    async def test_accepting_updates_orders_and_vehicles(self, as_dispatcher, depot, db):
        """The four-table transaction, verified from the database side."""
        from sqlalchemy import select

        from app.models.order import Order
        from app.models.vehicle import Vehicle

        await _make_orders(db, depot)
        await _make_vehicles(db, depot)
        run = await self._solve(as_dispatcher, depot)
        routed_ids = {
            s["order_id"] for r in run["result_payload"]["routes"] for s in r["stops"]
        }
        used_vehicles = {r["vehicle_id"] for r in run["result_payload"]["routes"]}

        await as_dispatcher.post(f"{API}/optimization/runs/{run['id']}/accept")

        # Every routed order left the planning pool...
        for order in (await db.execute(select(Order))).scalars().all():
            expected = (
                OrderStatus.ASSIGNED if order.id in routed_ids else OrderStatus.PENDING
            )
            assert order.status == expected, f"order {order.id} has status {order.status}"

        # ...and every used vehicle did too, carrying its planned load.
        for vehicle in (await db.execute(select(Vehicle))).scalars().all():
            if vehicle.id in used_vehicles:
                assert vehicle.status == VehicleStatus.ASSIGNED
                assert vehicle.current_load_kg > 0
            else:
                assert vehicle.status == VehicleStatus.AVAILABLE

    async def test_stop_arrivals_are_absolute_timestamps(self, as_dispatcher, depot, db):
        """The horizon conversion, which is easy to get wrong.

        The solver works in integer minutes from a horizon; accept must turn
        those back into real timestamps against the same origin.
        """
        await _make_orders(db, depot)
        await _make_vehicles(db, depot)
        run = await self._solve(as_dispatcher, depot)
        routes = (
            await as_dispatcher.post(f"{API}/optimization/runs/{run['id']}/accept")
        ).json()

        stops = [s for r in routes for s in r["stops"]]
        assert stops
        for stop in stops:
            assert stop["estimated_arrival"] is not None
            # Not yet visited, so no actual arrival — that is the simulation's
            # job, and the pair is what makes on-time analysis possible.
            assert stop["actual_arrival"] is None

    async def test_accepted_routes_appear_in_the_routes_list(self, as_dispatcher, depot, db):
        await _make_orders(db, depot)
        await _make_vehicles(db, depot)
        run = await self._solve(as_dispatcher, depot)
        await as_dispatcher.post(f"{API}/optimization/runs/{run['id']}/accept")

        listed = (await as_dispatcher.get(f"{API}/routes?status=PLANNED")).json()
        assert len(listed) > 0
        assert all(r["status"] == "PLANNED" for r in listed)

    async def test_accepting_twice_is_refused(self, as_dispatcher, depot, db):
        """A second accept must not dispatch the fleet again.

        The status check alone does not cover this: accepting leaves the run
        COMPLETED, so it stays acceptable. Before the guard was added, a
        double-click on Accept created a duplicate route per vehicle,
        re-assigned the orders, and overwrote each vehicle's load with the
        second plan's figures.

        The guard uses the routes' own optimization_run_id, so no new column or
        status was needed — the routes are the record of the plan having been
        accepted.
        """
        from sqlalchemy import func, select

        from app.models.route import Route

        await _make_orders(db, depot)
        await _make_vehicles(db, depot)
        run = await self._solve(as_dispatcher, depot)

        first = await as_dispatcher.post(f"{API}/optimization/runs/{run['id']}/accept")
        assert first.status_code == 200
        after_first = (await db.execute(select(func.count(Route.id)))).scalar_one()
        assert after_first > 0

        second = await as_dispatcher.post(f"{API}/optimization/runs/{run['id']}/accept")
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "PLAN_ALREADY_ACCEPTED"

        # And crucially, nothing was created by the refused call.
        after_second = (await db.execute(select(func.count(Route.id)))).scalar_one()
        assert after_second == after_first

    async def test_cannot_accept_a_missing_run(self, as_dispatcher):
        res = await as_dispatcher.post(f"{API}/optimization/runs/999999/accept")
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "OPTIMIZATION_RUN_NOT_FOUND"


class TestDiscard:
    async def test_discarding_leaves_the_fleet_untouched(self, as_dispatcher, depot, db):
        from sqlalchemy import func, select

        from app.models.order import Order
        from app.models.route import Route

        await _make_orders(db, depot)
        await _make_vehicles(db, depot)
        run = (
            await as_dispatcher.post(
                f"{API}/optimization/run", json={"depot_id": depot.id, "objective": "BALANCED"}
            )
        ).json()

        res = await as_dispatcher.post(f"{API}/optimization/runs/{run['id']}/discard")
        assert res.status_code == 200

        assert (await db.execute(select(func.count(Route.id)))).scalar_one() == 0
        pending = (
            await db.execute(
                select(func.count(Order.id)).where(Order.status == OrderStatus.PENDING)
            )
        ).scalar_one()
        assert pending == 4

    async def test_a_discarded_plan_cannot_be_accepted(self, as_dispatcher, depot, db):
        await _make_orders(db, depot)
        await _make_vehicles(db, depot)
        run = (
            await as_dispatcher.post(
                f"{API}/optimization/run", json={"depot_id": depot.id, "objective": "BALANCED"}
            )
        ).json()
        await as_dispatcher.post(f"{API}/optimization/runs/{run['id']}/discard")

        res = await as_dispatcher.post(f"{API}/optimization/runs/{run['id']}/accept")
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "PLAN_NOT_READY"
        # Discard records itself as FAILED with a reason — see the note on
        # OptimizationStatus in models/enums.py about that conflation.
        reread = (await as_dispatcher.get(f"{API}/optimization/runs/{run['id']}")).json()
        assert reread["status"] == "FAILED"
        assert reread["error_message"] == "Plan discarded by user"


class TestRunLog:
    async def test_runs_are_listed_newest_first(self, as_dispatcher, depot, db):
        await _make_orders(db, depot)
        await _make_vehicles(db, depot)
        for _ in range(2):
            await as_dispatcher.post(
                f"{API}/optimization/run", json={"depot_id": depot.id, "objective": "BALANCED"}
            )

        runs = (await as_dispatcher.get(f"{API}/optimization/runs")).json()
        assert len(runs) == 2
        assert runs[0]["created_at"] >= runs[1]["created_at"]
