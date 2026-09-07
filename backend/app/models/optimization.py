"""The `optimization_runs` table — the audit log of every solve.

One row per optimization attempt, whether it succeeded, failed or was
discarded. Two jobs:

  1. **History.** A permanent record of what was solved, how long it took and
     how much it saved. This is what the analytics savings chart reads.
  2. **The review workflow.** `result_payload` holds the entire proposed plan,
     so accepting it later is a pure state transition — no re-solve. That
     matters because the search is time-bounded: re-running would legitimately
     give a different answer, so a dispatcher could approve one plan and get
     another.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import OptimizationObjective, OptimizationStatus


class OptimizationRun(Base, TimestampMixin):
    __tablename__ = "optimization_runs"

    id: Mapped[int] = mapped_column(primary_key=True)

    # PROCESSING is written before the solve begins, so a client polling this
    # row (or watching the OPTIMIZATION_* events) sees the job exists.
    # COMPLETED means a plan is available — NOT that it has been applied.
    status: Mapped[OptimizationStatus] = mapped_column(
        SAEnum(OptimizationStatus, name="optimization_status"),
        default=OptimizationStatus.PENDING,
    )
    # Free text, not an enum: it is a label for humans reading the run log, and
    # swapping the algorithm should not require a migration.
    algorithm: Mapped[str] = mapped_column(String(64), default="OR-Tools VRP (guided local search)")
    objective: Mapped[OptimizationObjective] = mapped_column(
        SAEnum(OptimizationObjective, name="optimization_objective"),
        default=OptimizationObjective.BALANCED,
    )

    # NOTE: a plain Integer, not a ForeignKey — unlike every other depot
    # reference in the schema. Nothing stops a depot being deleted while runs
    # still point at it. The intent (an audit record should outlive the depot it
    # ran for) is right, but the correct expression is a real FK with
    # ondelete="SET NULL", matching how optimization_run_id is handled on
    # routes. Fixing it needs a migration; see models/README.md.
    depot_id: Mapped[int | None] = mapped_column(Integer)

    # --- Inputs: the size of the problem we were given ----------------------
    orders_count: Mapped[int] = mapped_column(Integer, default=0)
    vehicles_count: Mapped[int] = mapped_column(Integer, default=0)

    # --- Outputs: what the chosen plan achieved -----------------------------
    assigned_count: Mapped[int] = mapped_column(Integer, default=0)
    unassigned_count: Mapped[int] = mapped_column(Integer, default=0)

    # "before" is the greedy baseline, "after" is the dispatched plan. Storing
    # both is what makes the reported improvement a measurement rather than a
    # claim — the comparison point is computed on every run, not assumed.
    total_distance_before: Mapped[float | None] = mapped_column(Float)
    total_distance_after: Mapped[float | None] = mapped_column(Float)
    improvement_percentage: Mapped[float | None] = mapped_column(Float)

    execution_time_ms: Mapped[int | None] = mapped_column(Integer)
    # The solver's internal objective value. Comparable only between runs of
    # the same objective and problem, so it is diagnostic rather than a KPI.
    objective_value: Mapped[float | None] = mapped_column(Float)

    # Set when status is FAILED. Truncated to 490 chars by the caller to fit,
    # since a traceback string can be arbitrarily long.
    error_message: Mapped[str | None] = mapped_column(String(500))

    # Full solver result (routes, metrics, baseline) held for the accept/discard workflow
    #
    # JSONB, not JSON: Postgres stores it in a binary form that is queryable and
    # indexable rather than as a text blob it must re-parse.
    #
    # A JSON column rather than proper tables because this is an immutable
    # snapshot of a *proposal*. Materialising it into routes/route_stops before
    # anyone approved it would put unapproved plans in the operational tables —
    # exactly what the review step exists to prevent.
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
