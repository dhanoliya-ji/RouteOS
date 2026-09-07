"""Optimization request/response shapes.

These describe a *proposal*, not committed state. The nested models
(PlannedRoute / PlannedStop) mirror what the solver produced and what is stored
in `optimization_runs.result_payload` — they deliberately look like RouteOut and
RouteStopOut without being them, because a planned route has no database id, no
actual arrival times and no progress.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import OptimizationObjective, OptimizationStatus


class OptimizationRequest(BaseModel):
    """What to optimize.

    Only `depot_id` is required. The two id lists are *narrowing filters*, not
    the definition of the problem:

        both empty  -> every PENDING order and AVAILABLE vehicle at the depot
        order_ids   -> restrict to those orders (still only the PENDING ones)
        vehicle_ids -> restrict to those vehicles (still only AVAILABLE ones)

    So passing a list can never widen the problem to include an order already
    on a van or a vehicle already out — the service applies the status filters
    regardless. See optimization_service._load_inputs.
    """

    depot_id: int
    # default_factory rather than `= []`: a shared mutable default would be
    # the same list object on every instance.
    order_ids: list[int] = Field(default_factory=list)
    vehicle_ids: list[int] = Field(default_factory=list)
    objective: OptimizationObjective = OptimizationObjective.BALANCED


class UnassignedOrderInfo(BaseModel):
    """An order the plan could not serve, and the inferred reason.

    Part of the normal response, not an error. `reason` is an UnassignedReason
    value, deduced by the solver rather than reported by it.
    """

    order_id: int
    order_number: str
    reason: str


class PlannedStop(BaseModel):
    """A proposed stop.

    Compare RouteStopOut: no `id` (nothing is stored yet) and no
    `actual_arrival` (nothing has happened yet). Instead of a timestamp it
    carries `eta_minutes_from_start` — still relative to the planning horizon,
    exactly as the solver produced it. accept_plan converts those to absolute
    timestamps using the horizon saved alongside the plan.
    """

    order_id: int
    order_number: str
    stop_sequence: int
    latitude: float
    longitude: float
    distance_from_previous_km: float
    load_kg: float
    eta_minutes_from_start: float


class PlannedRoute(BaseModel):
    """A proposed route for one vehicle.

    Carries `capacity_kg` next to `total_load_kg` so a reviewer can see how
    full each van would be without looking the vehicle up.
    """

    vehicle_id: int
    registration_number: str
    total_distance_km: float
    estimated_duration_minutes: float
    total_load_kg: float
    capacity_kg: float
    stops: list[PlannedStop]


class OptimizationMetrics(BaseModel):
    """The headline numbers for one plan.

    Used twice inside BaselineComparison — once for the greedy baseline and once
    for the plan being dispatched — so the two are guaranteed to be measured the
    same way.
    """

    total_distance_km: float
    estimated_duration_minutes: float
    vehicles_used: int
    assigned_orders: int
    unassigned_orders: int


class BaselineComparison(BaseModel):
    """Optimized versus greedy, side by side.

    This is what makes the reported saving a measurement rather than a claim:
    both plans are computed on every run from the same orders, so the
    percentages below are derived, not asserted.

    A negative reduction is possible and meaningful — it says the dispatched
    plan is worse than greedy on that axis, which happens when the solver runs
    out of time. (The service normally dispatches the winner, so this mostly
    shows up as a near-zero figure.)
    """

    baseline: OptimizationMetrics
    optimized: OptimizationMetrics
    distance_reduction_pct: float
    time_reduction_pct: float
    vehicles_reduction_pct: float


class OptimizationResult(BaseModel):
    """A fully-expanded, typed view of a completed run.

    NOTE: not currently returned by any endpoint — the optimization routes all
    respond with OptimizationRunOut, whose `result_payload` is an untyped dict.
    This model documents that payload's real shape and is what those endpoints
    would use to become fully typed in OpenAPI.
    """

    optimization_run_id: int
    status: OptimizationStatus
    objective: OptimizationObjective
    routes: list[PlannedRoute]
    unassigned: list[UnassignedOrderInfo]
    comparison: BaselineComparison
    execution_time_ms: int
    objective_value: float


class OptimizationRunOut(BaseModel):
    """A row from `optimization_runs` — what the endpoints actually return.

    Doubles as both the progress view and the result view: a client polls this
    while `status` is PROCESSING, then reads `result_payload` once it is
    COMPLETED.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: OptimizationStatus
    algorithm: str
    objective: OptimizationObjective
    depot_id: int | None

    # Inputs: the size of the problem.
    orders_count: int
    vehicles_count: int
    # Outcome counts.
    assigned_count: int
    unassigned_count: int

    # "before" is the greedy baseline, "after" the dispatched plan. Nullable
    # because a PROCESSING or FAILED run has not produced them.
    total_distance_before: float | None
    total_distance_after: float | None
    improvement_percentage: float | None

    execution_time_ms: int | None
    objective_value: float | None
    error_message: str | None
    created_at: datetime

    # The whole plan, as stored. Typed as a bare dict rather than
    # OptimizationResult above, which keeps the endpoint flexible but means
    # OpenAPI cannot describe the contents — so a client has to know the shape
    # from documentation instead of from the schema.
    #
    # Defaulted to None so the model is still valid for a run that has no plan
    # yet (PROCESSING) or never produced one (FAILED).
    result_payload: dict[str, Any] | None = None
