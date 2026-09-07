"""Order request/response shapes — the fullest example of the Create/Update/Out
triad. Read this file first to learn the pattern the other resources follow.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import OrderPriority, OrderStatus


class OrderBase(BaseModel):
    """Fields a client may supply, shared by create (and inherited nowhere else).

    Note what is absent: `order_number` (server-assigned), `status` (starts
    PENDING and changes only through the workflow) and `location` (derived from
    lat/lng by the service). A client cannot set any of them, because they are
    not in the schema — which is a stronger guarantee than checking for them.
    """

    customer_name: str = Field(min_length=1, max_length=120)
    customer_phone: str | None = None
    delivery_address: str = Field(min_length=1, max_length=255)

    # Bounded to real coordinates. Without these a typo'd latitude of 280 would
    # be accepted, stored, and then quietly wreck every distance calculation
    # involving this order.
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)

    # gt=0, not ge=0: a zero-weight delivery is not a thing, and allowing it
    # would let unlimited orders stack into one vehicle for free.
    weight_kg: float = Field(gt=0, default=1.0)
    volume: float | None = None  # recorded only; the solver models kg

    priority: OrderPriority = OrderPriority.NORMAL

    # Both optional: an order with no window can be served at any time, and the
    # solver treats it as unconstrained.
    delivery_window_start: datetime | None = None
    delivery_window_end: datetime | None = None

    # ge=0 allows zero — a drop-off that takes no measurable time is plausible.
    service_time_minutes: int = Field(ge=0, default=10)

    depot_id: int

    @model_validator(mode="after")
    def _check_window(self) -> "OrderBase":
        """Reject a window that ends before it starts.

        This has to be a model validator rather than a Field constraint: no
        single field can see another, and the rule relates the two.

        mode="after" runs once every field has been parsed and coerced, so both
        values are real datetimes here rather than raw strings.

        `<=` rather than `<` so a zero-length window is rejected too — it would
        be satisfiable only by arriving at one exact instant.
        """
        s, e = self.delivery_window_start, self.delivery_window_end
        if s and e and e <= s:
            raise ValueError("delivery_window_end must be after delivery_window_start")
        return self


class OrderCreate(OrderBase):
    """POST body. Identical to OrderBase today.

    Kept as a separate name rather than using OrderBase directly at the
    endpoint, so create-only fields can be added later without disturbing
    anything else that inherits the base.
    """


class OrderUpdate(BaseModel):
    """PATCH body: every field optional.

    That is what makes PATCH semantics work. Paired with
    `model_dump(exclude_unset=True)` in the service, an omitted field means
    "leave it alone" while an explicit `null` means "clear it" — a distinction
    that would be impossible if the fields had defaults applied on absence.

    Declared standalone rather than inheriting OrderBase, because inheriting
    would carry the required-ness of those fields along with them.

    Note `status` IS settable here, unlike on create — it is how a dispatcher
    corrects a delivery outcome. The service still guards which transitions are
    legal; the schema only says the field may be sent.
    """

    customer_name: str | None = None
    customer_phone: str | None = None
    delivery_address: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    weight_kg: float | None = Field(default=None, gt=0)
    volume: float | None = None
    priority: OrderPriority | None = None
    status: OrderStatus | None = None
    delivery_window_start: datetime | None = None
    delivery_window_end: datetime | None = None
    service_time_minutes: int | None = Field(default=None, ge=0)
    depot_id: int | None = None


class OrderOut(BaseModel):
    """An order as the API describes it.

    Written out field by field rather than inheriting OrderBase, so the response
    contract is explicit and independent: adding an internal column to the ORM
    model — a cost, a supplier note, the PostGIS blob — cannot make it appear in
    an API response by accident.

    It carries the two server-assigned fields the input shapes omit:
    `order_number` and `status`.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    order_number: str
    customer_name: str
    customer_phone: str | None
    delivery_address: str
    latitude: float
    longitude: float
    weight_kg: float
    volume: float | None
    priority: OrderPriority
    status: OrderStatus
    delivery_window_start: datetime | None
    delivery_window_end: datetime | None
    service_time_minutes: int
    depot_id: int
    created_at: datetime


class NearbyOrder(OrderOut):
    """An order plus how far away it is — the /orders/nearby response.

    Subclassing OrderOut rather than redeclaring its fields means the two can
    never drift apart. `distance_km` is not a stored column: it is computed by
    PostGIS per query, relative to whatever point was searched from.
    """

    distance_km: float
