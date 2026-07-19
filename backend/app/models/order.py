from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from geoalchemy2 import Geography
from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import OrderPriority, OrderStatus

if TYPE_CHECKING:
    from app.models.depot import Depot
    from app.models.route import RouteStop


class Order(Base, TimestampMixin):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_status", "status"),
        Index("ix_orders_priority", "priority"),
        Index("ix_orders_depot_id", "depot_id"),
        Index("ix_orders_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    customer_name: Mapped[str] = mapped_column(String(120), nullable=False)
    customer_phone: Mapped[str | None] = mapped_column(String(32))
    delivery_address: Mapped[str] = mapped_column(String(255), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    location: Mapped[object] = mapped_column(
        Geography(geometry_type="POINT", srid=4326), nullable=True
    )
    weight_kg: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    volume: Mapped[float | None] = mapped_column(Float)
    priority: Mapped[OrderPriority] = mapped_column(
        SAEnum(OrderPriority, name="order_priority"), default=OrderPriority.NORMAL
    )
    status: Mapped[OrderStatus] = mapped_column(
        SAEnum(OrderStatus, name="order_status"), default=OrderStatus.PENDING
    )
    delivery_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    service_time_minutes: Mapped[int] = mapped_column(Integer, default=10)
    depot_id: Mapped[int] = mapped_column(ForeignKey("depots.id", ondelete="CASCADE"))

    depot: Mapped["Depot"] = relationship(back_populates="orders")
    route_stops: Mapped[list["RouteStop"]] = relationship(back_populates="order")
