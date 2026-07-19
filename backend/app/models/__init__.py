"""SQLAlchemy models. Importing this package registers all tables on Base.metadata."""
from app.models.depot import Depot
from app.models.enums import (
    OptimizationObjective,
    OptimizationStatus,
    OrderPriority,
    OrderStatus,
    RouteStatus,
    RouteStopStatus,
    UnassignedReason,
    UserRole,
    VehicleStatus,
)
from app.models.optimization import OptimizationRun
from app.models.order import Order
from app.models.route import Route, RouteStop
from app.models.telemetry import DeliveryEvent, VehicleLocationHistory
from app.models.user import User
from app.models.vehicle import Vehicle

__all__ = [
    "Depot",
    "DeliveryEvent",
    "OptimizationRun",
    "OptimizationObjective",
    "OptimizationStatus",
    "Order",
    "OrderPriority",
    "OrderStatus",
    "Route",
    "RouteStop",
    "RouteStatus",
    "RouteStopStatus",
    "UnassignedReason",
    "User",
    "UserRole",
    "Vehicle",
    "VehicleStatus",
    "VehicleLocationHistory",
]
