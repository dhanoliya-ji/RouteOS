"""Order API tests against a real PostGIS database (Tier 2).

Skips cleanly when there is no test database — see tests/conftest.py.

These cover the layer nothing else did: the **business rules**. A schema test
proves a negative weight is rejected; only this proves that a DELIVERED order
cannot be edited, that cancelling refuses once a delivery is under way, and that
the PostGIS point really is written on create.

Every test runs inside a transaction that is rolled back afterwards, so they
share one schema and still cannot see each other's data.
"""
from __future__ import annotations

import pytest

from app.models.enums import OrderStatus
from tests.conftest import requires_db

pytestmark = requires_db

API = "/api/v1"


def _payload(depot_id: int, **overrides) -> dict:
    """A valid create body. Overrides let a test vary one field at a time."""
    body = {
        "customer_name": "Aarav Sharma",
        "customer_phone": "+919812345678",
        "delivery_address": "12 MG Road, South Delhi",
        "latitude": 28.5245,
        "longitude": 77.2066,
        "weight_kg": 12.5,
        "priority": "NORMAL",
        "service_time_minutes": 10,
        "depot_id": depot_id,
    }
    body.update(overrides)
    return body


class TestCreate:
    async def test_creates_an_order_and_assigns_a_number(self, as_dispatcher, depot):
        res = await as_dispatcher.post(f"{API}/orders", json=_payload(depot.id))
        assert res.status_code == 201, res.text
        body = res.json()

        # Server-assigned: order_number is absent from OrderCreate, so a client
        # cannot set it. This proves the server does.
        assert body["order_number"].startswith("ORD-")
        # Status is not settable on create either — every order starts PENDING,
        # which is the only status the optimizer will plan for.
        assert body["status"] == OrderStatus.PENDING.value
        assert body["customer_name"] == "Aarav Sharma"

    async def test_writes_the_postgis_point(self, as_dispatcher, depot, db):
        """The geography column is populated, not just the float pair.

        Worth its own test because nothing in the API response reveals it —
        `location` is deliberately absent from OrderOut — yet /orders/nearby is
        useless without it.
        """
        from sqlalchemy import func, select

        from app.models.order import Order

        res = await as_dispatcher.post(f"{API}/orders", json=_payload(depot.id))
        order_id = res.json()["id"]

        # Read the point back as text, so the assertion does not depend on the
        # binary representation.
        location = (
            await db.execute(
                select(func.ST_AsText(Order.location)).where(Order.id == order_id)
            )
        ).scalar_one()
        assert location is not None
        assert "POINT" in location
        # And it is lon/lat order, as PostGIS requires — the longitude 77.2 must
        # appear first. Getting this backwards is the classic silent bug that
        # make_point() exists to prevent.
        assert location.startswith("POINT(77.2")

    async def test_order_numbers_increment(self, as_dispatcher, depot):
        first = (await as_dispatcher.post(f"{API}/orders", json=_payload(depot.id))).json()
        second = (await as_dispatcher.post(f"{API}/orders", json=_payload(depot.id))).json()
        assert first["order_number"] != second["order_number"]

    async def test_rejects_an_out_of_range_latitude(self, as_dispatcher, depot):
        """Schema validation, reached through HTTP rather than in isolation."""
        res = await as_dispatcher.post(f"{API}/orders", json=_payload(depot.id, latitude=280))
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_rejects_a_zero_weight(self, as_dispatcher, depot):
        # gt=0, not ge=0: a weightless order could be stacked without limit.
        res = await as_dispatcher.post(f"{API}/orders", json=_payload(depot.id, weight_kg=0))
        assert res.status_code == 422

    async def test_rejects_a_backwards_delivery_window(self, as_dispatcher, depot):
        """The cross-field validator, which no single Field constraint can do."""
        res = await as_dispatcher.post(
            f"{API}/orders",
            json=_payload(
                depot.id,
                delivery_window_start="2026-01-14T12:00:00Z",
                delivery_window_end="2026-01-14T09:00:00Z",
            ),
        )
        assert res.status_code == 422

    async def test_a_missing_depot_raises_rather_than_answering(self, as_dispatcher, depot, db):
        """Documents a real gap rather than asserting a wish.

        Creating an order for a depot that does not exist violates the foreign
        key, and **nothing handles IntegrityError** — no exception handler in
        core/errors.py covers it, and order_service does not check the depot
        exists first. So the exception escapes the handler entirely: over real
        HTTP that is a 500 with a traceback, and in this test it propagates out
        of the client.

        The same path is reachable in production by a concurrent create, since
        _next_order_number derives from MAX(id) and two simultaneous requests
        can generate the same order_number — the UNIQUE constraint then raises
        here too.

        A clean fix is a handler mapping IntegrityError to a 409, and/or a
        depot pre-check for a 404. Both are design decisions affecting every
        endpoint, so this test pins the behaviour as it stands instead of
        pretending otherwise.
        """
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            await as_dispatcher.post(f"{API}/orders", json=_payload(999_999))

        # The failed statement aborted the transaction, so roll it back before
        # the fixture teardown tries to use the connection.
        await db.rollback()


class TestStatusRules:
    """The rules that only a service can enforce, because they depend on state."""

    async def _create(self, client, depot_id) -> dict:
        return (await client.post(f"{API}/orders", json=_payload(depot_id))).json()

    async def test_a_pending_order_can_be_edited(self, as_dispatcher, depot):
        order = await self._create(as_dispatcher, depot.id)
        res = await as_dispatcher.patch(
            f"{API}/orders/{order['id']}", json={"customer_name": "Renamed"}
        )
        assert res.status_code == 200
        assert res.json()["customer_name"] == "Renamed"

    async def test_patch_leaves_unmentioned_fields_alone(self, as_dispatcher, depot):
        """PATCH semantics — the point of `exclude_unset` in the service.

        Without it, every field the client omitted would be overwritten with a
        default, so renaming a customer would silently reset their weight.
        """
        order = await self._create(as_dispatcher, depot.id)
        res = await as_dispatcher.patch(
            f"{API}/orders/{order['id']}", json={"customer_name": "Only This"}
        )
        body = res.json()
        assert body["customer_name"] == "Only This"
        assert body["weight_kg"] == 12.5           # untouched
        assert body["delivery_address"] == "12 MG Road, South Delhi"

    async def test_a_delivered_order_cannot_be_edited(self, as_dispatcher, depot, db):
        """ORDER_IMMUTABLE — a finished order is history, not working data."""
        from sqlalchemy import select

        from app.models.order import Order

        order = await self._create(as_dispatcher, depot.id)
        # Move it to DELIVERED the way the simulation would.
        row = (await db.execute(select(Order).where(Order.id == order["id"]))).scalar_one()
        row.status = OrderStatus.DELIVERED
        await db.commit()

        res = await as_dispatcher.patch(
            f"{API}/orders/{order['id']}", json={"customer_name": "Rewriting history"}
        )
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "ORDER_IMMUTABLE"

    async def test_cancelling_a_pending_order_keeps_the_record(self, as_dispatcher, depot):
        """Cancel is a state transition, not a delete."""
        order = await self._create(as_dispatcher, depot.id)
        res = await as_dispatcher.post(f"{API}/orders/{order['id']}/cancel")
        assert res.status_code == 200
        assert res.json()["status"] == OrderStatus.CANCELLED.value
        # Still retrievable — the history survives.
        assert (await as_dispatcher.get(f"{API}/orders/{order['id']}")).status_code == 200

    async def test_cannot_cancel_an_order_out_for_delivery(self, as_dispatcher, depot, db):
        """Cancelling would not stop the van, so recording it would be a fiction."""
        from sqlalchemy import select

        from app.models.order import Order

        order = await self._create(as_dispatcher, depot.id)
        row = (await db.execute(select(Order).where(Order.id == order["id"]))).scalar_one()
        row.status = OrderStatus.OUT_FOR_DELIVERY
        await db.commit()

        res = await as_dispatcher.post(f"{API}/orders/{order['id']}/cancel")
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "ORDER_IN_PROGRESS"

    async def test_a_pending_order_can_be_deleted(self, as_dispatcher, depot):
        order = await self._create(as_dispatcher, depot.id)
        assert (await as_dispatcher.delete(f"{API}/orders/{order['id']}")).status_code == 200
        assert (await as_dispatcher.get(f"{API}/orders/{order['id']}")).status_code == 404

    async def test_an_assigned_order_cannot_be_deleted(self, as_dispatcher, depot, db):
        """Delete is narrower than cancel: it would cascade away route_stops
        belonging to a route that really happened."""
        from sqlalchemy import select

        from app.models.order import Order

        order = await self._create(as_dispatcher, depot.id)
        row = (await db.execute(select(Order).where(Order.id == order["id"]))).scalar_one()
        row.status = OrderStatus.ASSIGNED
        await db.commit()

        res = await as_dispatcher.delete(f"{API}/orders/{order['id']}")
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "ORDER_DELETE_FORBIDDEN"

    async def test_missing_order_is_a_clean_404(self, as_dispatcher):
        res = await as_dispatcher.get(f"{API}/orders/999999")
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "ORDER_NOT_FOUND"
        # The id is echoed as data, so a client need not parse the message.
        assert res.json()["error"]["details"]["id"] == 999999


class TestListing:
    @pytest.fixture(autouse=True)
    async def _orders(self, as_dispatcher, depot):
        """Six orders with a known spread of priorities.

        All are PENDING: the API cannot create an order in any other status,
        which is itself the guarantee test_filters_by_status relies on.
        """
        for i, priority in enumerate(["LOW", "NORMAL", "HIGH", "URGENT", "NORMAL", "HIGH"]):
            await as_dispatcher.post(
                f"{API}/orders",
                json=_payload(depot.id, priority=priority, customer_name=f"Customer {i}"),
            )

    async def test_returns_a_page_envelope(self, as_dispatcher):
        body = (await as_dispatcher.get(f"{API}/orders")).json()
        for key in ("items", "total", "page", "page_size", "pages"):
            assert key in body
        assert body["total"] == 6
        assert body["page"] == 1

    async def test_page_size_is_respected_and_pages_computed(self, as_dispatcher):
        body = (await as_dispatcher.get(f"{API}/orders?page_size=4")).json()
        assert len(body["items"]) == 4
        assert body["total"] == 6
        # ceil(6/4) — the server states this so the client need not recompute
        # it and risk disagreeing.
        assert body["pages"] == 2

    async def test_second_page_returns_the_remainder(self, as_dispatcher):
        body = (await as_dispatcher.get(f"{API}/orders?page_size=4&page=2")).json()
        assert len(body["items"]) == 2

    async def test_filters_by_priority(self, as_dispatcher):
        body = (await as_dispatcher.get(f"{API}/orders?priority=HIGH")).json()
        assert body["total"] == 2
        assert all(o["priority"] == "HIGH" for o in body["items"])

    async def test_filters_by_status(self, as_dispatcher):
        body = (await as_dispatcher.get(f"{API}/orders?status=PENDING")).json()
        assert body["total"] == 6  # every order created via the API is PENDING
        assert all(o["status"] == "PENDING" for o in body["items"])

    async def test_search_matches_the_customer_name(self, as_dispatcher):
        body = (await as_dispatcher.get(f"{API}/orders?search=Customer 3")).json()
        assert body["total"] == 1
        assert body["items"][0]["customer_name"] == "Customer 3"

    async def test_search_is_case_insensitive(self, as_dispatcher):
        """ilike, not like — pinned because the difference is invisible until
        someone searches in the wrong case."""
        body = (await as_dispatcher.get(f"{API}/orders?search=customer 3")).json()
        assert body["total"] == 1

    async def test_unknown_filter_value_is_rejected(self, as_dispatcher):
        """Typed as an enum, so a bad value is a 422 naming the field rather
        than a filter that silently matches nothing."""
        res = await as_dispatcher.get(f"{API}/orders?status=NONSENSE")
        assert res.status_code == 422

    async def test_page_zero_is_rejected(self, as_dispatcher):
        # ge=1, so the service never computes a negative OFFSET.
        assert (await as_dispatcher.get(f"{API}/orders?page=0")).status_code == 422

    async def test_page_size_is_capped(self, as_dispatcher):
        # le=200 — one request cannot ask for the whole table.
        assert (await as_dispatcher.get(f"{API}/orders?page_size=5000")).status_code == 422


class TestNearby:
    """The PostGIS radius search — the reason these tests need a real database."""

    async def test_finds_orders_within_the_radius_nearest_first(self, as_dispatcher, depot):
        # Three orders at increasing distance from the search point.
        near = _payload(depot.id, latitude=28.5478, longitude=77.2733, customer_name="Near")
        mid = _payload(depot.id, latitude=28.5600, longitude=77.2900, customer_name="Mid")
        far = _payload(depot.id, latitude=28.7000, longitude=77.5000, customer_name="Far")
        for body in (far, near, mid):  # inserted out of order on purpose
            await as_dispatcher.post(f"{API}/orders", json=body)

        res = await as_dispatcher.get(
            f"{API}/orders/nearby?latitude=28.5478&longitude=77.2733&radius_km=5"
        )
        assert res.status_code == 200
        items = res.json()
        names = [o["customer_name"] for o in items]

        assert "Near" in names
        assert "Far" not in names, "an order ~30km away matched a 5km radius"
        # Sorted by true distance, ascending — ST_Distance in the ORDER BY.
        distances = [o["distance_km"] for o in items]
        assert distances == sorted(distances)
        assert items[0]["customer_name"] == "Near"
        # The nearest is at the search point itself, so ~0 km.
        assert items[0]["distance_km"] < 0.1

    async def test_rejects_an_impossible_radius(self, as_dispatcher):
        # gt=0: a zero radius finds nothing, so it is a mistake not a query.
        assert (
            await as_dispatcher.get(f"{API}/orders/nearby?latitude=28&longitude=77&radius_km=0")
        ).status_code == 422

    async def test_nearby_is_matched_before_the_id_route(self, as_dispatcher):
        """`/orders/nearby` must not be captured by `/orders/{order_id}`.

        Route declaration order is load-bearing: reversed, this path would bind
        order_id="nearby", fail to coerce it to int, and return a confusing 422
        about a parameter the caller never sent. A 200 here proves the literal
        route still wins.
        """
        res = await as_dispatcher.get(
            f"{API}/orders/nearby?latitude=28.5&longitude=77.2&radius_km=1"
        )
        assert res.status_code == 200
        assert isinstance(res.json(), list)
