"""Shared domain enumerations (stored as strings in the DB)."""
from __future__ import annotations

import enum


class UserRole(str, enum.Enum):
    ADMIN = "ADMIN"
    DISPATCHER = "DISPATCHER"
    VIEWER = "VIEWER"


class VehicleStatus(str, enum.Enum):
    AVAILABLE = "AVAILABLE"
    ASSIGNED = "ASSIGNED"
    IN_TRANSIT = "IN_TRANSIT"
    MAINTENANCE = "MAINTENANCE"
    OFFLINE = "OFFLINE"


class OrderPriority(str, enum.Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"

    @property
    def weight(self) -> int:
        return {"LOW": 1, "NORMAL": 2, "HIGH": 4, "URGENT": 8}[self.value]


class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RouteStatus(str, enum.Enum):
    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class RouteStopStatus(str, enum.Enum):
    PENDING = "PENDING"
    ARRIVED = "ARRIVED"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"


class OptimizationStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class OptimizationObjective(str, enum.Enum):
    MIN_DISTANCE = "MIN_DISTANCE"
    MIN_TIME = "MIN_TIME"
    BALANCED = "BALANCED"


class UnassignedReason(str, enum.Enum):
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    NO_AVAILABLE_VEHICLE = "NO_AVAILABLE_VEHICLE"
    TIME_WINDOW_INFEASIBLE = "TIME_WINDOW_INFEASIBLE"
    ROUTE_DURATION_EXCEEDED = "ROUTE_DURATION_EXCEEDED"
    UNKNOWN = "UNKNOWN"
