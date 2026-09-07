"""Order business logic: the rules about what may be done to an order, and when.

The status checks here are the point of this module. A schema can reject a
malformed order; only a service can know that *this* order is too far along to
be edited.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import APIError, not_found
from app.geospatial.queries import distance_meters, make_point, within
from app.models.enums import OrderPriority, OrderStatus
from app.models.order import Order
from app.schemas.order import OrderCreate, OrderUpdate


async def _next_order_number(db: AsyncSession) -> str:
    """Generate the next human-facing reference, e.g. "ORD-00042".

    Derived from MAX(id) rather than a counter table or sequence.

    Be aware this is **not concurrency-safe**: two simultaneous creates can read
    the same MAX and generate the same number, and the UNIQUE constraint on
    order_number would then reject the second with an IntegrityError rather than
    a clean error. Fine at demo scale; a Postgres SEQUENCE (or deriving the
    label from the row's own id after flush) is the real fix.
    """
    # coalesce so an empty table yields 0 rather than None.
    max_id = (await db.execute(select(func.coalesce(func.max(Order.id), 0)))).scalar_one()
    return f"ORD-{int(max_id) + 1:05d}"


async def get_order(db: AsyncSession, order_id: int) -> Order:
    """Fetch one order, or raise a 404.

    Every other function here starts by calling this, so the not-found case is
    handled once instead of at each call site.
    """
    order = (await db.execute(select(Order).where(Order.id == order_id))).scalar_one_or_none()
    if order is None:
        # scalar_one_or_none (not scalar_one) so a missing row is None rather
        # than an exception we would have to catch and translate.
        raise not_found("order", order_id)
    return order


async def create_order(db: AsyncSession, data: OrderCreate) -> Order:
    """Create an order, assigning its reference and its PostGIS point."""
    order = Order(
        # Server-assigned: not present on OrderCreate, so a client cannot set it.
        order_number=await _next_order_number(db),
        # Keep the geography column in step with the plain lat/lng. Both are
        # stored (see models/order.py) and this is the only place they are set
        # on create, so they cannot diverge here.
        location=make_point(data.latitude, data.longitude),
        # The schema has already validated every field, so spreading it is safe
        # — and it means adding a field to OrderCreate needs no change here.
        **data.model_dump(),
    )
    db.add(order)
    await db.commit()
    # Populate id, created_at and the DB-side defaults for the response.
    await db.refresh(order)
    return order


async def update_order(db: AsyncSession, order_id: int, data: OrderUpdate) -> Order:
    """Partially update an order, if its state still allows changes."""
    order = await get_order(db, order_id)

    # THE RULE: a finished order is history, not working data. Editing a
    # delivered order's address or weight would silently rewrite what actually
    # happened. 409 Conflict — the request is well-formed, but conflicts with
    # the resource's current state.
    if order.status in (OrderStatus.DELIVERED, OrderStatus.CANCELLED):
        raise APIError(
            "ORDER_IMMUTABLE",
            f"Order in status {order.status.value} cannot be modified",
            status_code=409,
        )

    # exclude_unset is what makes this PATCH rather than PUT: only keys the
    # client actually sent appear, so an omitted field is left alone while an
    # explicit null clears it. Without it, every unmentioned field would be
    # overwritten with its default.
    payload = data.model_dump(exclude_unset=True)
    for key, value in payload.items():
        setattr(order, key, value)

    # Rebuild the geography point only if a coordinate actually changed.
    # Checking the payload keys (not the values) is what distinguishes "the
    # client sent a new latitude" from "latitude was not mentioned".
    #
    # Note it reads order.latitude/longitude, not payload's — so sending only
    # one coordinate still combines it with the existing other one.
    if "latitude" in payload or "longitude" in payload:
        order.location = make_point(order.latitude, order.longitude)

    await db.commit()
    await db.refresh(order)
    return order


async def cancel_order(db: AsyncSession, order_id: int) -> Order:
    """Cancel an order — a state change that keeps the record."""
    order = await get_order(db, order_id)

    # Once a vehicle is carrying it, cancelling in the system would not stop the
    # delivery — the route is already planned and the parcel is on board. So the
    # cancellation is refused rather than recorded as a fiction.
    if order.status in (OrderStatus.OUT_FOR_DELIVERY, OrderStatus.DELIVERED):
        raise APIError("ORDER_IN_PROGRESS", "Cannot cancel an order already out for delivery", 409)

    order.status = OrderStatus.CANCELLED
    await db.commit()
    await db.refresh(order)
    return order


async def delete_order(db: AsyncSession, order_id: int) -> None:
    """Delete an order permanently, if it never got underway.

    Deliberately narrower than cancel: an order that was delivered or is out for
    delivery is part of the operational record, and deleting it would also
    cascade away its route_stops — removing a stop from a route that really
    happened. Only orders that never entered the flow may be erased.
    """
    order = await get_order(db, order_id)
    if order.status not in (OrderStatus.PENDING, OrderStatus.CANCELLED, OrderStatus.FAILED):
        raise APIError("ORDER_DELETE_FORBIDDEN", "Only pending/cancelled/failed orders can be deleted", 409)
    await db.delete(order)
    await db.commit()


async def list_orders(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 25,
    status: OrderStatus | None = None,
    priority: OrderPriority | None = None,
    depot_id: int | None = None,
    on_date: date | None = None,
    assigned: bool | None = None,
    search: str | None = None,
) -> tuple[list[Order], int]:
    """Filtered, paginated order list. Returns (rows, total matching count).

    Keyword-only (note the bare `*`), so a call site cannot silently pass
    `page_size` where `page` was meant — with eight similar parameters that is a
    real hazard.

    The total is returned alongside the page because the client needs it to know
    how many pages exist, and it must be counted with the *same* filters.
    """
    # Two statements built in parallel: one to fetch the page, one to count the
    # whole filtered set.
    stmt = select(Order)
    count_stmt = select(func.count(Order.id))

    # Collect the filters first rather than chaining .where() inline, so each
    # condition can be applied to BOTH statements below. Building them twice
    # would risk the count drifting out of step with the rows — the classic
    # pagination bug where the pager disagrees with the list.
    conditions = []
    if status is not None:
        conditions.append(Order.status == status)
    if priority is not None:
        conditions.append(Order.priority == priority)
    if depot_id is not None:
        conditions.append(Order.depot_id == depot_id)
    if on_date is not None:
        # A whole calendar day in UTC: time.min to time.max. `between` is
        # inclusive on both ends, and time.max is 23:59:59.999999, so this
        # covers the day without spilling into the next.
        start = datetime.combine(on_date, time.min, tzinfo=timezone.utc)
        end = datetime.combine(on_date, time.max, tzinfo=timezone.utc)
        conditions.append(Order.created_at.between(start, end))
    if assigned is True:
        # "assigned" means anything that has left PENDING — including delivered
        # and cancelled. A tri-state, so `is True` / `is False` rather than a
        # truthiness test: None must mean "no filter", and `if assigned:` would
        # collapse None and False together.
        conditions.append(Order.status != OrderStatus.PENDING)
    elif assigned is False:
        conditions.append(Order.status == OrderStatus.PENDING)
    if search:
        # One free-text box across three columns. ilike for case-insensitive
        # matching; or_ so a term matching any one column is a hit.
        #
        # A leading wildcard means no index can serve this, so it is a scan.
        # Acceptable here; a large table would want a trigram index or
        # Postgres full-text search.
        like = f"%{search}%"
        conditions.append(
            or_(
                Order.order_number.ilike(like),
                Order.customer_name.ilike(like),
                Order.delivery_address.ilike(like),
            )
        )

    for c in conditions:
        stmt = stmt.where(c)
        count_stmt = count_stmt.where(c)

    # Count before paginating — the total is of all matches, not of this page.
    total = (await db.execute(count_stmt)).scalar_one()

    # Newest first, then OFFSET/LIMIT. The ORDER BY is not cosmetic: without a
    # deterministic order, two requests for the same page can return different
    # rows, so paging could skip or repeat records.
    stmt = stmt.order_by(Order.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    items = list((await db.execute(stmt)).scalars().all())
    return items, int(total)


async def orders_nearby(
    db: AsyncSession, latitude: float, longitude: float, radius_km: float, limit: int = 50
) -> list[tuple[Order, float]]:
    """Orders within a radius, nearest first. Returns (order, distance_km) pairs.

    One query does all of it: PostGIS filters by the index, sorts by true
    distance, and returns the distance as a column so it need not be
    recomputed in Python.
    """
    dist = distance_meters(Order.location, latitude, longitude)
    stmt = (
        # Select the distance alongside the row, so the caller gets it for free.
        select(Order, dist.label("dist_m"))
        # Filter with the GiST-indexable predicate FIRST, so Postgres discards
        # almost every row without measuring it. Sorting by distance alone would
        # compute one for every order in the table.
        .where(within(Order.location, latitude, longitude, radius_km))
        .order_by(dist)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    # .all() (not .scalars()) because each row is a (Order, distance) pair.
    # Convert metres to km here so the API speaks one unit throughout.
    return [(row[0], row[1] / 1000.0) for row in rows]
