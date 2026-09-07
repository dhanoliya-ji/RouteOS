"""Shared domain enumerations (stored as strings in the DB).

Read this file first — nine enums naming every state a thing in RouteOS can be
in. The rest of `models/` refers to them constantly.

Why every enum subclasses `str`
-------------------------------
    class OrderStatus(str, enum.Enum)

A member is then *also* a Python string, which buys three things:

    OrderStatus.PENDING == "PENDING"     -> True   (compares to plain strings)
    json.dumps(OrderStatus.PENDING)      -> works  (serialises with no encoder)
    stored in Postgres as 'PENDING'      -> readable in a raw SELECT

The alternative — integer codes — means a raw `SELECT status FROM orders`
returns `2`, and you need a lookup table (or the source) to know what that is.
It also means inserting a new member in the middle renumbers everything after
it. Strings avoid both problems, at the cost of a few bytes per row.

The columns that use these are declared as `SAEnum(..., name="order_status")`,
which creates a real named Postgres enum type — so the database itself rejects
a value that isn't in the list.
"""
from __future__ import annotations

import enum


class UserRole(str, enum.Enum):
    """Who someone is allowed to be. A strict ladder, not a set of flags.

    ADMIN is checked first in `require_roles` and bypasses every other guard,
    so a permission only ever has to name the *lowest* role that may pass.
    """

    ADMIN = "ADMIN"           # everything, including deleting depots/vehicles
    DISPATCHER = "DISPATCHER"  # may change operational data
    VIEWER = "VIEWER"         # read-only


class VehicleStatus(str, enum.Enum):
    """Where a vehicle is in its working day.

    The first three are driven by the system; the last two are set by hand and
    are how you take a van out of the planning pool.
    """

    AVAILABLE = "AVAILABLE"      # idle at a depot — the only status the optimizer will plan for
    ASSIGNED = "ASSIGNED"        # has an accepted plan, not yet moving
    IN_TRANSIT = "IN_TRANSIT"    # out on a route, moved by the simulation engine
    MAINTENANCE = "MAINTENANCE"  # manual: in the workshop
    OFFLINE = "OFFLINE"          # manual: not in service


class OrderPriority(str, enum.Enum):
    """How badly an order needs to be served today."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"

    @property
    def weight(self) -> int:
        """How expensive it is to leave this order unassigned.

        The solver multiplies this by its base drop penalty, so the number is
        the *relative* cost of stranding one order versus another.

        Deliberately doubling, not incrementing:

            LOW 1   NORMAL 2   HIGH 4   URGENT 8

        Linear weights (1,2,3,4) would let the solver trade one URGENT order
        against two NORMALs. Powers of two mean an URGENT order outranks any
        number of lower-priority ones that could fit in the same slot, so
        priority behaves like a ranking rather than something averaged away.

        This lives on the enum, not in the solver, because the priority ladder
        is domain knowledge — it belongs next to the priorities themselves.
        """
        return {"LOW": 1, "NORMAL": 2, "HIGH": 4, "URGENT": 8}[self.value]


class OrderStatus(str, enum.Enum):
    """An order's lifecycle.

        PENDING ──optimization accepted──> ASSIGNED
                                              │
                                        OUT_FOR_DELIVERY
                                              │
                                  ┌───────────┴──────────┐
                              DELIVERED               FAILED

        CANCELLED can be reached from PENDING or ASSIGNED (see
        order_service.cancel_order, which refuses once a delivery is underway).

    PENDING is special: it is the only status the optimizer will plan for, which
    is what stops an order already on a van being re-planned onto another.
    """

    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RouteStatus(str, enum.Enum):
    """A route's lifecycle.

        PLANNED ──engine picks it up──> ACTIVE ──back at depot──> COMPLETED
                                                       │
                                                  CANCELLED (manual)

    A route is born PLANNED by accepting an optimization run; nothing else
    creates one.
    """

    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class RouteStopStatus(str, enum.Enum):
    """One stop on a route.

    PENDING vs COMPLETED is what makes mid-route re-optimization possible: the
    re-solve only touches PENDING stops, so completed deliveries keep their
    sequence numbers and their recorded arrival times.
    """

    PENDING = "PENDING"
    ARRIVED = "ARRIVED"      # at the location, delivery not yet confirmed
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"      # passed over — nobody home, access blocked


class OptimizationStatus(str, enum.Enum):
    """How a solver run is progressing.

    PROCESSING is written *before* the solve starts and is what a client polls
    (or watches over WebSocket) while waiting. COMPLETED means a plan exists in
    `result_payload` — it does not mean the plan has been applied.

    Note `discard_plan()` reuses FAILED for a plan a human rejected, with the
    reason in `error_message`. That conflates "the solver broke" with "we
    didn't want it"; a distinct DISCARDED member would be clearer, but adding
    one changes the Postgres enum type and so needs a migration.
    """

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class OptimizationObjective(str, enum.Enum):
    """What the solver should minimise. Chosen per run by the caller.

    Each maps to a different arc cost in solver._scaled_cost:

        MIN_DISTANCE  cheapest total kilometres — lowest fuel
        MIN_TIME      fastest total drive time — best for tight windows
        BALANCED      the average of the two, on a common scale
    """

    MIN_DISTANCE = "MIN_DISTANCE"
    MIN_TIME = "MIN_TIME"
    BALANCED = "BALANCED"


class UnassignedReason(str, enum.Enum):
    """Why an order did not make it onto any route.

    Reported per order so a dispatcher can act on it rather than just seeing
    that something was left out. The solver does not tell us *why* it dropped a
    node, so `solver._infer_reason` deduces the most likely cause by checking
    the order against the fleet — which is why UNKNOWN exists as a fallback.
    """

    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"              # heavier than any single vehicle
    NO_AVAILABLE_VEHICLE = "NO_AVAILABLE_VEHICLE"        # fleet full — add vehicles
    TIME_WINDOW_INFEASIBLE = "TIME_WINDOW_INFEASIBLE"    # window unreachable in time
    ROUTE_DURATION_EXCEEDED = "ROUTE_DURATION_EXCEEDED"  # would breach a distance/time limit
    UNKNOWN = "UNKNOWN"
