"""Route response shapes.

**Read-only.** There is no RouteCreate and no RouteUpdate, and that absence is
the design: a route cannot be POSTed. Routes exist only by accepting an
optimization plan, so having no input schema is what makes them unforgeable
through the API.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import RouteStatus, RouteStopStatus


class RouteStopOut(BaseModel):
    """One call on a route.

    The pair worth noticing is `estimated_arrival` / `actual_arrival`: the
    first came from the solver at plan time, the second is written by the
    simulation when the vehicle got there. Returning both lets a client show
    "12 minutes late" without needing a separate lookup.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    # The visiting position — what makes the stops a sequence rather than a set.
    stop_sequence: int
    # Copied from the order at accept time, so this is where the vehicle was
    # actually sent even if the order's address was edited later.
    latitude: float
    longitude: float
    estimated_arrival: datetime | None
    actual_arrival: datetime | None   # None until delivered
    # Length of the leg ending at this stop, not a running total.
    distance_from_previous_km: float
    status: RouteStopStatus

    # Note the order itself is NOT nested here — only `order_id`. A client that
    # wants customer details fetches the order separately. That keeps a route
    # response bounded in size (a 30-stop route would otherwise embed 30 full
    # orders) at the cost of an extra request when the details are needed.


class RouteOut(BaseModel):
    """A route and its stops.

    `stops` is populated only when the query eager-loaded them — which
    route_service always does, via selectinload. Without that eager load,
    serialising this model would trigger a lazy load and raise MissingGreenlet
    under async.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    route_code: str
    # Optional because the FK is ON DELETE SET NULL: the route outlives the
    # vehicle that drove it.
    vehicle_id: int | None
    depot_id: int
    status: RouteStatus

    # Planned figures, frozen at accept time.
    total_distance_km: float
    estimated_duration_minutes: float
    # Filled in by the simulation when the vehicle returns; None until then.
    # Alongside the estimate above, this is what makes plan-versus-actual
    # analysis possible.
    actual_duration_minutes: float | None

    # The improvement percentage of the optimization run that produced this
    # route, denormalised onto it so a routes list needs no join.
    optimization_score: float | None
    total_load_kg: float

    # Index of the last completed stop; -1 means "still at the depot". Lets a
    # client draw progress along the route without scanning every stop's status.
    progress_stop_index: int

    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    # Defaults to empty rather than being required, so a response is still
    # valid for a route whose stops were not loaded.
    stops: list[RouteStopOut] = []
