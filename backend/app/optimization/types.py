"""Plain data structures passed in/out of the optimization engine.

These are intentionally decoupled from SQLAlchemy models and Pydantic schemas so
the solver can be unit-tested in isolation with no DB.
"""
from __future__ import annotations

from dataclasses import dataclass, field

Coord = tuple[float, float]


@dataclass
class OrderNode:
    order_id: int
    order_number: str
    coord: Coord
    demand_kg: float
    service_time_min: int = 10
    # Minutes from the planning horizon start; None = no constraint.
    tw_start_min: int | None = None
    tw_end_min: int | None = None
    priority_weight: int = 2


@dataclass
class VehicleInput:
    vehicle_id: int
    registration_number: str
    capacity_kg: float
    max_route_distance_km: float = 200.0


@dataclass
class SolvedStop:
    order_id: int
    order_number: str
    stop_sequence: int
    coord: Coord
    distance_from_previous_km: float
    load_kg: float
    eta_minutes_from_start: float


@dataclass
class SolvedRoute:
    vehicle_id: int
    registration_number: str
    total_distance_km: float
    estimated_duration_minutes: float
    total_load_kg: float
    capacity_kg: float
    stops: list[SolvedStop] = field(default_factory=list)


@dataclass
class UnassignedOrder:
    order_id: int
    order_number: str
    reason: str


@dataclass
class SolveResult:
    routes: list[SolvedRoute]
    unassigned: list[UnassignedOrder]
    objective_value: float
    matrix_source: str
