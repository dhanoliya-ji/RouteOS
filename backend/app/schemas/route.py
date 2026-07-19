from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import RouteStatus, RouteStopStatus


class RouteStopOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    stop_sequence: int
    latitude: float
    longitude: float
    estimated_arrival: datetime | None
    actual_arrival: datetime | None
    distance_from_previous_km: float
    status: RouteStopStatus


class RouteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    route_code: str
    vehicle_id: int | None
    depot_id: int
    status: RouteStatus
    total_distance_km: float
    estimated_duration_minutes: float
    actual_duration_minutes: float | None
    optimization_score: float | None
    total_load_kg: float
    progress_stop_index: int
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    stops: list[RouteStopOut] = []
