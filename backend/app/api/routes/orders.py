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
_manage = require_roles(UserRole.DISPATCHER)


@router.get("", response_model=Page[OrderOut])
async def list_orders(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    status: OrderStatus | None = None,
    priority: OrderPriority | None = None,
    depot_id: int | None = None,
    on_date: date | None = None,
    assigned: bool | None = None,
    search: str | None = None,
):
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
    pages = (total + page_size - 1) // page_size
    return Page(items=items, total=total, page=page, page_size=page_size, pages=pages)


@router.get("/nearby", response_model=list[NearbyOrder])
async def orders_nearby(
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    radius_km: float = Query(5.0, gt=0, le=500),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    rows = await order_service.orders_nearby(db, latitude, longitude, radius_km)
    return [NearbyOrder(**OrderOut.model_validate(o).model_dump(), distance_km=round(d, 3)) for o, d in rows]


@router.get("/{order_id}", response_model=OrderOut)
async def get_order(order_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    return await order_service.get_order(db, order_id)


@router.post("", response_model=OrderOut, status_code=201)
async def create_order(data: OrderCreate, db: AsyncSession = Depends(get_db), _=Depends(_manage)):
    return await order_service.create_order(db, data)


@router.patch("/{order_id}", response_model=OrderOut)
async def update_order(
    order_id: int, data: OrderUpdate, db: AsyncSession = Depends(get_db), _=Depends(_manage)
):
    return await order_service.update_order(db, order_id, data)


@router.post("/{order_id}/cancel", response_model=OrderOut)
async def cancel_order(order_id: int, db: AsyncSession = Depends(get_db), _=Depends(_manage)):
    return await order_service.cancel_order(db, order_id)


@router.delete("/{order_id}", response_model=Message)
async def delete_order(order_id: int, db: AsyncSession = Depends(get_db), _=Depends(_manage)):
    await order_service.delete_order(db, order_id)
    return Message(message="Order deleted")
