"""Simulation control — the demo/operations console.

These endpoints drive the movement clock in app/simulation/engine.py: start it,
change its speed, disrupt a route, or re-plan one mid-flight.

Note several handlers take no `db` at all. The engine holds its live state in
memory and opens its own sessions when it needs to write, so a pure control
command (start, stop, speed) touches no database from the request's side.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import require_roles
from app.db.session import get_db
from app.models.enums import UserRole
from app.services import simulation_service

router = APIRouter(prefix="/simulation", tags=["simulation"])
# All dispatcher-level: these commands move the fleet.
_dispatch = require_roles(UserRole.DISPATCHER)


# Request bodies declared locally rather than in app/schemas/.
#
# These are control-plane *commands*, not persisted resources — nothing else
# needs them, so keeping them beside their only use is clearer than a file in
# schemas/ that would be imported once.
class SpeedBody(BaseModel):
    speed_multiplier: float = 1.0


class TrafficBody(BaseModel):
    route_id: int
    # NOTE: a bare str, validated only later by the service (which raises
    # INVALID_SEVERITY for an unknown value). As
    # Literal["clear","moderate","severe","breakdown"] it would be rejected at
    # the schema boundary with a 422 naming the field, and /docs would list the
    # valid options instead of leaving them to this comment.
    severity: str  # clear | moderate | severe | breakdown


@router.get("/status")
async def status(_=Depends(_dispatch)):
    """Who is moving, where, and how fast.

    Reads the engine's in-memory state directly — no database, so this is cheap
    enough to poll. The same payload is pushed as the SNAPSHOT event when a
    WebSocket client connects.
    """
    return simulation_service.simulation_status()


@router.post("/start")
async def start(body: SpeedBody, _=Depends(_dispatch)):
    """Start the clock over every PLANNED or ACTIVE route.

    Returns `{"started": false, "reason": ...}` rather than an error when there
    is nothing to simulate — "no routes yet" is an expected state, not a
    failure, and the UI shows it as guidance.
    """
    return await simulation_service.start_simulation(body.speed_multiplier)


@router.post("/stop")
async def stop(_=Depends(_dispatch)):
    """Stop the clock. Vehicles freeze where they are; delivered orders stay
    delivered, because those were committed to the database as they happened."""
    return await simulation_service.stop_simulation()


@router.post("/speed")
async def speed(body: SpeedBody, _=Depends(_dispatch)):
    """Change the time multiplier (1x-60x) while running.

    This is how fast the *clock* runs — distinct from the traffic factor below,
    which is how fast a *vehicle* goes. Not async: it only assigns a float on
    the in-memory engine.
    """
    return simulation_service.set_speed(body.speed_multiplier)


@router.post("/traffic")
async def traffic(body: TrafficBody, db: AsyncSession = Depends(get_db), _=Depends(_dispatch)):
    """Slow a route down, or stop it dead.

    Takes `db` because it does more than set a factor: the service estimates the
    resulting delay and works out which deliveries will now miss their windows,
    returning them as `late_orders` (also broadcast as ROUTE_DELAYED). A
    severity of "breakdown" sets the factor to zero.

    Requires the route to be ACTIVE and known to the engine, else 409.
    """
    return await simulation_service.apply_traffic(db, body.route_id, body.severity)


@router.post("/routes/{route_id}/reoptimize")
async def reoptimize(route_id: int, db: AsyncSession = Depends(get_db), _=Depends(_dispatch)):
    """Re-sequence the stops a route has not yet delivered.

    Solves a fresh single-vehicle VRP over the PENDING stops only, starting
    from the vehicle's **current position** rather than the depot — a plan that
    assumed otherwise would be fiction. Completed stops keep their sequence
    numbers and recorded arrivals, and the engine picks the new order up on its
    next tick.

    Returns `{"reoptimized": false, "reason": ...}` when there is nothing worth
    doing (fewer than two stops remain, or no feasible re-route exists).
    """
    return await simulation_service.reoptimize_route(db, route_id)
