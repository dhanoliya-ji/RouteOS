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
    max_id = (await db.execute(select(func.coalesce(func.max(Order.id), 0)))).scalar_one()
    return f"ORD-{int(max_id) + 1:05d}"


async def get_order(db: AsyncSession, order_id: int) -> Order:
    order = (await db.execute(select(Order).where(Order.id == order_id))).scalar_one_or_none()
    if order is None:
        raise not_found("order", order_id)
    return order


async def create_order(db: AsyncSession, data: OrderCreate) -> Order:
    order = Order(
        order_number=await _next_order_number(db),
        location=make_point(data.latitude, data.longitude),
        **data.model_dump(),
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    return order


async def update_order(db: AsyncSession, order_id: int, data: OrderUpdate) -> Order:
    order = await get_order(db, order_id)
    if order.status in (OrderStatus.DELIVERED, OrderStatus.CANCELLED):
        raise APIError(
            "ORDER_IMMUTABLE",
            f"Order in status {order.status.value} cannot be modified",
            status_code=409,
        )
    payload = data.model_dump(exclude_unset=True)
    for key, value in payload.items():
        setattr(order, key, value)
    if "latitude" in payload or "longitude" in payload:
        order.location = make_point(order.latitude, order.longitude)
    await db.commit()
    await db.refresh(order)
    return order


async def cancel_order(db: AsyncSession, order_id: int) -> Order:
    order = await get_order(db, order_id)
    if order.status in (OrderStatus.OUT_FOR_DELIVERY, OrderStatus.DELIVERED):
        raise APIError("ORDER_IN_PROGRESS", "Cannot cancel an order already out for delivery", 409)
    order.status = OrderStatus.CANCELLED
    await db.commit()
    await db.refresh(order)
    return order


async def delete_order(db: AsyncSession, order_id: int) -> None:
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
    stmt = select(Order)
    count_stmt = select(func.count(Order.id))

    conditions = []
    if status is not None:
        conditions.append(Order.status == status)
    if priority is not None:
        conditions.append(Order.priority == priority)
    if depot_id is not None:
        conditions.append(Order.depot_id == depot_id)
    if on_date is not None:
        start = datetime.combine(on_date, time.min, tzinfo=timezone.utc)
        end = datetime.combine(on_date, time.max, tzinfo=timezone.utc)
        conditions.append(Order.created_at.between(start, end))
    if assigned is True:
        conditions.append(Order.status != OrderStatus.PENDING)
    elif assigned is False:
        conditions.append(Order.status == OrderStatus.PENDING)
    if search:
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

    total = (await db.execute(count_stmt)).scalar_one()
    stmt = stmt.order_by(Order.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    items = list((await db.execute(stmt)).scalars().all())
    return items, int(total)


async def orders_nearby(
    db: AsyncSession, latitude: float, longitude: float, radius_km: float, limit: int = 50
) -> list[tuple[Order, float]]:
    dist = distance_meters(Order.location, latitude, longitude)
    stmt = (
        select(Order, dist.label("dist_m"))
        .where(within(Order.location, latitude, longitude, radius_km))
        .order_by(dist)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [(row[0], row[1] / 1000.0) for row in rows]
