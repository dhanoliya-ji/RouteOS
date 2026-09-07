"""SQLAlchemy models. Importing this package registers all tables on Base.metadata.

That first line is the load-bearing part. A table exists on `Base.metadata` only
as a side effect of its class being *defined*, which happens when its module is
imported. So this package's import list is what makes the schema visible to:

  * **Alembic** — autogenerate diffs `Base.metadata` against the live database.
    If nothing here were imported, metadata would be empty and it would emit a
    migration dropping every table.
  * **`create_all`** — the initial migration builds the schema straight from
    metadata.

Which is why `db/base.py:import_models()` does nothing but `from app import
models`: importing this module has the effect, and the imports below are
therefore *not* dead code, however much they look it.

The re-exports also give callers one obvious place to import from —
`from app.models import Order, Route` rather than three separate module paths.
"""
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

# Declaring __all__ marks these names as the package's intended public surface,
# so a linter does not report the imports above as unused — and so
# `from app.models import *` brings exactly these.
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
