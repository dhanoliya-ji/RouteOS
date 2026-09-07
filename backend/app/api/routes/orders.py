"""Order endpoints — the fullest CRUD example in the API.

Read this module to learn the pattern the other resources follow: declare,
validate, authorize, delegate. Every handler is a few lines because the guards
are declarations and the logic lives in order_service.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user, require_roles
from app.db.session import get_db
from app.models.enums import OrderPriority, OrderStatus, UserRole
from app.schemas.common import Message, Page
from app.schemas.order import NearbyOrder, OrderCreate, OrderOut, OrderUpdate
from app.services import order_service

router = APIRouter(prefix="/orders", tags=["orders"])
# Bound once for the whole module: reading is open to any signed-in user,
# writing needs a dispatcher. One place to change who may modify orders.
_manage = require_roles(UserRole.DISPATCHER)


@router.get("", response_model=Page[OrderOut])
async def list_orders(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
    # ge=1 so page 0 or a negative page is rejected before the service computes
    # a negative OFFSET.
    page: int = Query(1, ge=1),
    # le=200 caps the damage one request can do: without it a caller could ask
    # for a million rows in a single query.
    page_size: int = Query(25, ge=1, le=200),
    # Each of these is optional; the service ANDs together whichever were sent.
    # Typing them as enums means an invalid value is a 422 naming the field,
    # rather than a filter that silently matches nothing.
    status: OrderStatus | None = None,
    priority: OrderPriority | None = None,
    depot_id: int | None = None,
    on_date: date | None = None,
    # Tri-state on purpose: True = anything not PENDING, False = only PENDING,
    # None = no filter. A plain bool could not express the third case.
    assigned: bool | None = None,
    search: str | None = None,
):
    """Paginated, filterable order list."""
    items, total = await order_service.list_orders(
        db,
        page=page,
        page_size=page_size,
        status=status,
        priority=priority,
        depot_id=depot_id,
        on_date=on_date,
        assigned=assigned,
        search=search,
    )
    # Ceiling division without floats: (a + b - 1) // b. Computed here rather
    # than in the service because it is presentation — the service's job was to
    # return rows and a count.
    pages = (total + page_size - 1) // page_size
    return Page(items=items, total=total, page=page, page_size=page_size, pages=pages)


# NOTE: /nearby MUST stay declared before /{order_id}.
#
# FastAPI matches in declaration order and /{order_id} matches any single
# segment, so if these were swapped, GET /orders/nearby would bind
# order_id="nearby", fail to coerce it to int, and return a confusing 422 about
# a path parameter the caller never sent. Rule: literal paths before
# parameterised ones.
@router.get("/nearby", response_model=list[NearbyOrder])
async def orders_nearby(
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    # gt=0 because a zero radius finds nothing; le=500 stops a caller asking
    # for a radius that matches the whole table and defeats the index.
    radius_km: float = Query(5.0, gt=0, le=500),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    """Orders within a radius, nearest first (PostGIS)."""
    rows = await order_service.orders_nearby(db, latitude, longitude, radius_km)
    # The service returns (Order, distance) pairs, so the two are merged into
    # one flat object here: validate the row into OrderOut, dump it to a dict,
    # and splat it into NearbyOrder alongside the distance. Because NearbyOrder
    # subclasses OrderOut, the field lists cannot drift apart.
    return [NearbyOrder(**OrderOut.model_validate(o).model_dump(), distance_km=round(d, 3)) for o, d in rows]


@router.get("/{order_id}", response_model=OrderOut)
async def get_order(order_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    """One order. 404 via the service's not_found() if it does not exist."""
    return await order_service.get_order(db, order_id)


@router.post("", response_model=OrderOut, status_code=201)
async def create_order(data: OrderCreate, db: AsyncSession = Depends(get_db), _=Depends(_manage)):
    """Create an order. 201, per HTTP semantics for a newly created resource."""
    return await order_service.create_order(db, data)


@router.patch("/{order_id}", response_model=OrderOut)
async def update_order(
    order_id: int, data: OrderUpdate, db: AsyncSession = Depends(get_db), _=Depends(_manage)
):
    """Partial update. PATCH, not PUT: only the fields sent are changed.

    The service rejects edits to a DELIVERED or CANCELLED order with a 409.
    """
    return await order_service.update_order(db, order_id, data)


@router.post("/{order_id}/cancel", response_model=OrderOut)
async def cancel_order(order_id: int, db: AsyncSession = Depends(get_db), _=Depends(_manage)):
    """Cancel an order — a state transition, not a deletion.

    POST to a named sub-resource rather than PATCH with status=CANCELLED,
    because cancelling has its own rules (the service refuses once a delivery
    is under way) and modelling it as an action makes those rules discoverable.
    The record and its history are kept.
    """
    return await order_service.cancel_order(db, order_id)


@router.delete("/{order_id}", response_model=Message)
async def delete_order(order_id: int, db: AsyncSession = Depends(get_db), _=Depends(_manage)):
    """Delete an order for good.

    Dispatcher-level, unlike vehicles and depots which are admin-only — an
    order is one row of day-to-day data, whereas deleting a depot cascades to
    its whole fleet. The service still refuses unless the order is
    PENDING/CANCELLED/FAILED, so anything in flight cannot be erased.
    """
    await order_service.delete_order(db, order_id)
    # A JSON body rather than a bare 204, so every endpoint in the API returns
    # JSON and a client needs only one response path.
    return Message(message="Order deleted")
