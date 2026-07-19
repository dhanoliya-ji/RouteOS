from __future__ import annotations

from typing import Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import OptimizationObjective, OptimizationStatus


class OptimizationRun(Base, TimestampMixin):
    __tablename__ = "optimization_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[OptimizationStatus] = mapped_column(
        SAEnum(OptimizationStatus, name="optimization_status"),
        default=OptimizationStatus.PENDING,
    )
    algorithm: Mapped[str] = mapped_column(String(64), default="OR-Tools VRP (guided local search)")
    objective: Mapped[OptimizationObjective] = mapped_column(
        SAEnum(OptimizationObjective, name="optimization_objective"),
        default=OptimizationObjective.BALANCED,
    )
    depot_id: Mapped[int | None] = mapped_column(Integer)
    orders_count: Mapped[int] = mapped_column(Integer, default=0)
    vehicles_count: Mapped[int] = mapped_column(Integer, default=0)
    assigned_count: Mapped[int] = mapped_column(Integer, default=0)
    unassigned_count: Mapped[int] = mapped_column(Integer, default=0)
    total_distance_before: Mapped[float | None] = mapped_column(Float)
    total_distance_after: Mapped[float | None] = mapped_column(Float)
    improvement_percentage: Mapped[float | None] = mapped_column(Float)
    execution_time_ms: Mapped[int | None] = mapped_column(Integer)
    objective_value: Mapped[float | None] = mapped_column(Float)
    error_message: Mapped[str | None] = mapped_column(String(500))
    # Full solver result (routes, metrics, baseline) held for the accept/discard workflow
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
