"""Vehicle request/response shapes. Follows the same triad as order.py."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import VehicleStatus


class VehicleBase(BaseModel):
    """Fields a client supplies when adding a vehicle."""

    registration_number: str = Field(min_length=1, max_length=32)
    driver_name: str = Field(min_length=1, max_length=120)
    # Free text, mirroring the model column: descriptive only, nothing branches
    # on it, so new types need neither a migration nor a schema change.
    vehicle_type: str = "VAN"
    # gt=0: a vehicle that can carry nothing is not a vehicle, and zero would
    # make the solver's capacity dimension trivially infeasible for every order.
    capacity_kg: float = Field(gt=0)
    capacity_volume: float | None = None  # recorded only; the solver models kg
    home_depot_id: int
    # Optional, but bounded if given. Matches the model's 200 km default so the
    # solver always has a finite range limit to enforce — an unbounded value
    # would let a route wander arbitrarily far.
    max_route_distance_km: float | None = Field(default=200.0, gt=0)


class VehicleCreate(VehicleBase):
    """POST body: the base fields, plus an optional starting position.

    Position is create-only rather than part of the base because it is a *live*
    value the simulation subsequently owns and rewrites. Seeding it lets a new
    vehicle appear on the map before it has ever moved.

    Note `status` is absent: a new vehicle is AVAILABLE by the model's default,
    so a client cannot register a van as already IN_TRANSIT.
    """

    current_latitude: float | None = None
    current_longitude: float | None = None


class VehicleUpdate(BaseModel):
    """PATCH body: every field optional, so omission means "leave alone".

    `status` is settable here — that is how a vehicle is taken out of service
    (MAINTENANCE / OFFLINE), which is the mechanism for excluding it from
    planning, since the optimizer only considers AVAILABLE vehicles.

    Note the coordinates here are NOT range-bounded, unlike the order schemas.
    That is an inconsistency rather than a decision: an out-of-range latitude
    would be accepted and then corrupt the vehicles-nearby distance results.
    Adding `ge=-90, le=90` and `ge=-180, le=180` would match order.py.
    """

    registration_number: str | None = Field(default=None, min_length=1, max_length=32)
    driver_name: str | None = None
    vehicle_type: str | None = None
    capacity_kg: float | None = Field(default=None, gt=0)
    capacity_volume: float | None = None
    status: VehicleStatus | None = None
    current_latitude: float | None = None
    current_longitude: float | None = None
    home_depot_id: int | None = None
    max_route_distance_km: float | None = Field(default=None, gt=0)


class VehicleOut(BaseModel):
    """A vehicle as the API describes it.

    Adds the two fields the system owns rather than the client: `status` and
    `current_load_kg`.

    Note there is no `created_at`, unlike OrderOut and UserOut — the column
    exists on the model via TimestampMixin but is simply not exposed here.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    registration_number: str
    driver_name: str
    vehicle_type: str
    capacity_kg: float
    capacity_volume: float | None
    current_load_kg: float
    status: VehicleStatus
    current_latitude: float | None
    current_longitude: float | None
    home_depot_id: int
    max_route_distance_km: float | None


class NearbyVehicle(VehicleOut):
    """A vehicle plus its distance from the searched point.

    Same composition trick as NearbyOrder: subclassing means the two shapes
    cannot drift. `distance_km` is computed per query, not stored — and for
    vehicles it comes from a point built inside the SQL, since this table has
    no geography column (see app/geospatial/README.md).
    """

    distance_km: float
