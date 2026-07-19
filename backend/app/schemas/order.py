from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import OrderPriority, OrderStatus


class OrderBase(BaseModel):
    customer_name: str = Field(min_length=1, max_length=120)
    customer_phone: str | None = None
    delivery_address: str = Field(min_length=1, max_length=255)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    weight_kg: float = Field(gt=0, default=1.0)
    volume: float | None = None
    priority: OrderPriority = OrderPriority.NORMAL
    delivery_window_start: datetime | None = None
    delivery_window_end: datetime | None = None
    service_time_minutes: int = Field(ge=0, default=10)
    depot_id: int

    @model_validator(mode="after")
    def _check_window(self) -> "OrderBase":
        s, e = self.delivery_window_start, self.delivery_window_end
        if s and e and e <= s:
            raise ValueError("delivery_window_end must be after delivery_window_start")
        return self


class OrderCreate(OrderBase):
    pass


class OrderUpdate(BaseModel):
    customer_name: str | None = None
    customer_phone: str | None = None
    delivery_address: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    weight_kg: float | None = Field(default=None, gt=0)
    volume: float | None = None
    priority: OrderPriority | None = None
    status: OrderStatus | None = None
    delivery_window_start: datetime | None = None
    delivery_window_end: datetime | None = None
    service_time_minutes: int | None = Field(default=None, ge=0)
    depot_id: int | None = None


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_number: str
    customer_name: str
    customer_phone: str | None
    delivery_address: str
    latitude: float
    longitude: float
    weight_kg: float
    volume: float | None
    priority: OrderPriority
    status: OrderStatus
    delivery_window_start: datetime | None
    delivery_window_end: datetime | None
    service_time_minutes: int
    depot_id: int
    created_at: datetime


class NearbyOrder(OrderOut):
    distance_km: float
