"""Depot request/response shapes.

The smallest resource, and the one place the triad pattern differs — see
DepotOut.
"""
from __future__ import annotations

from datetime import time

from pydantic import BaseModel, ConfigDict, Field


class DepotBase(BaseModel):
    """Fields a client supplies for a depot."""

    name: str = Field(min_length=1, max_length=120)
    address: str | None = None

    # Bounded, as in order.py. A depot's coordinates matter more than any single
    # order's: node 0 of every optimization is this point, so an out-of-range
    # value would distort every route from this hub.
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)

    # Plain `time` values — no date, no timezone — because these describe a
    # recurring daily window rather than a specific instant.
    #
    # Recorded but NOT enforced: the solver anchors its planning horizon on the
    # earliest order window instead, so a route may currently be planned outside
    # these hours.
    operating_start: time = time(8, 0)
    operating_end: time = time(20, 0)

    # Note there is no validator checking operating_end > operating_start, in
    # contrast to OrderBase._check_window. A depot spanning midnight (22:00 to
    # 06:00) is legitimate, so the simple comparison would be wrong — which is
    # probably why it was omitted. Since the hours are unenforced today it costs
    # nothing, but a real check would need to handle the wrap.


class DepotCreate(DepotBase):
    """POST body. No extra fields — `location` is derived from lat/lng by the
    service, and `id` is assigned by the database."""


class DepotUpdate(BaseModel):
    """PATCH body: every field optional, so omission means "leave alone".

    Declared standalone rather than inheriting DepotBase, because inheriting
    would carry the base's required fields with it.
    """

    name: str | None = Field(default=None, min_length=1, max_length=120)
    address: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    operating_start: time | None = None
    operating_end: time | None = None


class DepotOut(DepotBase):
    """A depot as the API describes it.

    **This one INHERITS DepotBase**, unlike OrderOut and VehicleOut which
    redeclare every field. Two consequences worth knowing:

      * Any field added to DepotBase automatically appears in API responses.
        Convenient here — a depot has no secrets — but it is exactly the
        accidental exposure the redeclared models exist to prevent, which is
        why they do not do this.
      * It also inherits the base's validation, so DepotOut validates its own
        content on construction.

    Defensible because every depot field is public. Worth being deliberate
    about rather than copying to another resource by habit.
    """

    model_config = ConfigDict(from_attributes=True)
    id: int
