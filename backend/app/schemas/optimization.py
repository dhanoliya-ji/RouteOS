from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import OptimizationObjective, OptimizationStatus


class OptimizationRequest(BaseModel):
    depot_id: int
    order_ids: list[int] = Field(default_factory=list)
    vehicle_ids: list[int] = Field(default_factory=list)
    objective: OptimizationObjective = OptimizationObjective.BALANCED


class UnassignedOrderInfo(BaseModel):
    order_id: int
    order_number: str
    reason: str


class PlannedStop(BaseModel):
    order_id: int
    order_number: str
    stop_sequence: int
    latitude: float
    longitude: float
    distance_from_previous_km: float
    load_kg: float
    eta_minutes_from_start: float


class PlannedRoute(BaseModel):
    vehicle_id: int
    registration_number: str
    total_distance_km: float
    estimated_duration_minutes: float
    total_load_kg: float
    capacity_kg: float
    stops: list[PlannedStop]


class OptimizationMetrics(BaseModel):
    total_distance_km: float
    estimated_duration_minutes: float
    vehicles_used: int
    assigned_orders: int
    unassigned_orders: int


class BaselineComparison(BaseModel):
    baseline: OptimizationMetrics
    optimized: OptimizationMetrics
    distance_reduction_pct: float
    time_reduction_pct: float
    vehicles_reduction_pct: float


class OptimizationResult(BaseModel):
    optimization_run_id: int
    status: OptimizationStatus
    objective: OptimizationObjective
    routes: list[PlannedRoute]
    unassigned: list[UnassignedOrderInfo]
    comparison: BaselineComparison
    execution_time_ms: int
    objective_value: float


class OptimizationRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: OptimizationStatus
    algorithm: str
    objective: OptimizationObjective
    depot_id: int | None
    orders_count: int
    vehicles_count: int
    assigned_count: int
    unassigned_count: int
    total_distance_before: float | None
    total_distance_after: float | None
    improvement_percentage: float | None
    execution_time_ms: int | None
    objective_value: float | None
    error_message: str | None
    created_at: datetime
    result_payload: dict[str, Any] | None = None
