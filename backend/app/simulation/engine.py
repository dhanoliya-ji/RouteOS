"""Backend-authoritative delivery simulation.

A single asyncio loop advances every ACTIVE vehicle along its route polyline,
mutating real database state (order/route/vehicle status, location history) and
broadcasting events over WebSockets. The frontend only renders what this engine
publishes — it never invents movement.

Traffic events apply a per-route speed factor; breakdown sets it to zero.
Re-optimization is handled by simulation_service, which rebuilds a route's
remaining waypoints and the engine picks them up on the next tick.

Why the backend owns movement
-----------------------------
The tempting alternative is to let the browser animate between waypoints. That
makes a demo a lie: two tabs would disagree, a refresh would jump, and the
database would not match the map. Doing it here means the map, the order
statuses and the analytics are all views of the same rows.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.geospatial.distance import haversine_km
from app.models.enums import (
    OrderStatus,
    RouteStatus,
    RouteStopStatus,
    VehicleStatus,
)
from app.models.order import Order
from app.models.route import Route, RouteStop
from app.models.telemetry import DeliveryEvent, VehicleLocationHistory
from app.models.vehicle import Vehicle
from app.websocket.manager import manager

logger = get_logger(__name__)

TICK_SECONDS = 1.0  # real seconds between ticks

# How fast a VEHICLE goes, as a fraction of normal. Distinct from the engine's
# speed_multiplier, which is how fast the CLOCK runs.
#
# A breakdown is 0.0 rather than removing the vehicle: it stays in the
# simulation with its position and its pending stops, so recovery is just a
# matter of restoring the factor.
SPEED_FACTORS = {"clear": 1.0, "moderate": 0.6, "severe": 0.35, "breakdown": 0.0}


@dataclass
class Waypoint:
    """One point on a route's path.

    `order_id is None` marks the DEPOT — that test is how the arrival handler
    tells a delivery from a homecoming.
    """

    latitude: float
    longitude: float
    order_id: int | None  # None => depot
    stop_id: int | None


@dataclass
class VehicleSim:
    """The live cursor for one vehicle's journey.

    Position is held as an index plus an offset rather than as coordinates
    alone, because the engine needs to know *where along which leg* the vehicle
    is in order to continue interpolating on the next tick.

    All of this is in-memory only: a restart loses it and vehicles resume from
    their depot. The durable facts — delivered orders, completed stops — are in
    Postgres and survive.
    """

    route_id: int
    vehicle_id: int
    route_code: str
    waypoints: list[Waypoint]
    seg_index: int = 0  # index of the segment start waypoint
    dist_into_seg: float = 0.0  # km travelled along the current segment
    position: tuple[float, float] = (0.0, 0.0)  # current interpolated lat/lng
    speed_factor: float = 1.0  # traffic; 0.0 = broken down
    finished: bool = False  # back at the depot, route complete
    history_accum_km: float = 0.0  # km since the last breadcrumb was written


class SimulationEngine:
    """The clock. One instance per process — see the singleton at the bottom."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        # Keyed by ROUTE id, not vehicle id: the simulation follows a journey,
        # and one vehicle may run several routes over a day. apply_traffic and
        # reload_route both address a route.
        self._vehicles: dict[int, VehicleSim] = {}  # keyed by route_id
        self.speed_multiplier: float = 1.0
        self.running: bool = False

    @property
    def active_count(self) -> int:
        """Vehicles still moving. Also the loop's own exit condition."""
        return sum(1 for v in self._vehicles.values() if not v.finished)

    def status(self) -> dict:
        """A full snapshot of the simulation.

        Serves three callers: GET /simulation/status, the WebSocket SNAPSHOT
        sent on connect, and the SIMULATION_STARTED broadcast. Finished vehicles
        are included so a client can still draw them parked at the depot.
        """
        return {
            "running": self.running,
            "speed_multiplier": self.speed_multiplier,
            "active_vehicles": self.active_count,
            "vehicles": [
                {
                    "route_id": v.route_id,
                    "vehicle_id": v.vehicle_id,
                    "route_code": v.route_code,
                    "latitude": v.position[0],
                    "longitude": v.position[1],
                    "finished": v.finished,
                    "speed_factor": v.speed_factor,
                }
                for v in self._vehicles.values()
            ],
        }

    async def start(self, speed_multiplier: float = 1.0) -> dict:
        """Load every routable route and start ticking.

        Returns `{"started": False, ...}` rather than raising when there is
        nothing to do — "no routes yet" is an expected state a UI shows as
        guidance, not a failure.
        """
        # Floor of 1.0: a multiplier below one would run slower than real time,
        # which no caller wants and zero would freeze the simulation entirely.
        self.speed_multiplier = max(1.0, float(speed_multiplier))
        await self._load_routes()
        if not self._vehicles:
            return {"started": False, "reason": "No planned/active routes to simulate"}
        self.running = True
        # Only spawn if there is no live task, so calling start() twice does not
        # create two loops racing over the same vehicles.
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        await manager.broadcast("SIMULATION_STARTED", self.status())
        return {"started": True, **self.status()}

    async def stop(self) -> None:
        """Halt the clock. Vehicles freeze where they are.

        Note the sims are NOT cleared, so status() still reports their last
        known positions. Delivered orders and completed stops are already
        committed, so nothing in-flight is lost — only the in-memory progress
        along the current leg.
        """
        self.running = False
        if self._task:
            self._task.cancel()
            self._task = None
        await manager.broadcast("SIMULATION_STOPPED", {"running": False})

    def set_speed(self, multiplier: float) -> None:
        """Change the clock rate while running. Same floor of 1.0 as start()."""
        self.speed_multiplier = max(1.0, float(multiplier))

    def apply_traffic(self, route_id: int, severity: str) -> dict | None:
        """Set one route's speed factor. `None` if the engine does not know it.

        Returning None rather than raising lets the caller distinguish "no such
        simulating route" from a validation error — simulation_service turns it
        into a 409.
        """
        sim = self._vehicles.get(route_id)
        if sim is None:
            return None
        # Unknown severity falls back to 1.0 (clear). The service validates
        # against SPEED_FACTORS first, so this is belt-and-braces.
        sim.speed_factor = SPEED_FACTORS.get(severity, 1.0)
        return {"route_id": route_id, "severity": severity, "speed_factor": sim.speed_factor}

    def has_route(self, route_id: int) -> bool:
        """Whether this route is currently being simulated.

        Needed because a route can be ACTIVE in the database while absent here
        — for instance after a process restart, which loses all in-memory sims.
        """
        return route_id in self._vehicles

    async def reload_route(self, route_id: int) -> None:
        """Rebuild a single route's remaining waypoints (after re-optimization)."""
        # Its own session: this is called from a service whose session may
        # already have committed and moved on.
        async with AsyncSessionLocal() as db:
            sim = await self._build_sim(db, route_id, only_remaining=True)
        if sim is not None:
            # preserve current speed factor if the route was under traffic
            #
            # Carrying position across is what makes the swap invisible: the
            # vehicle continues from where it actually is rather than teleporting
            # back to the depot, and any traffic still in force stays applied.
            prev = self._vehicles.get(route_id)
            if prev:
                sim.position = prev.position
                sim.speed_factor = prev.speed_factor
            self._vehicles[route_id] = sim

    async def _load_routes(self) -> None:
        """Promote every PLANNED or ACTIVE route to ACTIVE and build its sim."""
        async with AsyncSessionLocal() as db:
            # Including ACTIVE, not just PLANNED, is what makes the engine
            # restartable: after a stop/start (or a process restart) routes
            # already under way are picked up again rather than ignored.
            routes = list(
                (
                    await db.execute(
                        select(Route).where(
                            Route.status.in_([RouteStatus.PLANNED, RouteStatus.ACTIVE])
                        )
                    )
                )
                .scalars()
                .all()
            )
            for route in routes:
                route.status = RouteStatus.ACTIVE
                # Preserve the original start time on a restart, so the eventual
                # actual_duration is measured from the real beginning.
                route.started_at = route.started_at or datetime.now(timezone.utc)
                vehicle = (
                    await db.execute(select(Vehicle).where(Vehicle.id == route.vehicle_id))
                ).scalar_one_or_none()
                if vehicle:
                    vehicle.status = VehicleStatus.IN_TRANSIT
                sim = await self._build_sim(db, route.id, only_remaining=False)
                if sim:
                    self._vehicles[route.id] = sim
            # One commit for all the promotions.
            await db.commit()

    async def _build_sim(
        self, db, route_id: int, only_remaining: bool
    ) -> VehicleSim | None:
        """Turn a route into a waypoint list. `None` if it cannot be simulated.

        `only_remaining=True` keeps just the PENDING stops, which is what makes
        re-optimization safe: completed deliveries are not revisited.
        """
        route = (
            await db.execute(
                select(Route)
                # Both eager loads are required: stops and depot are read below,
                # and a lazy load under asyncio raises MissingGreenlet.
                .options(selectinload(Route.stops), selectinload(Route.depot))
                .where(Route.id == route_id)
            )
        ).scalar_one_or_none()
        # A route with no vehicle cannot move. Possible because vehicle_id is
        # ON DELETE SET NULL.
        if route is None or route.vehicle_id is None:
            return None

        depot = route.depot
        stops = sorted(route.stops, key=lambda s: s.stop_sequence)
        if only_remaining:
            stops = [s for s in stops if s.status == RouteStopStatus.PENDING]

        # Depot at BOTH ends: a route is a round trip, and the trailing depot is
        # what triggers the route-complete handler when it is reached.
        waypoints = [Waypoint(depot.latitude, depot.longitude, None, None)]
        for s in stops:
            waypoints.append(Waypoint(s.latitude, s.longitude, s.order_id, s.id))
        waypoints.append(Waypoint(depot.latitude, depot.longitude, None, None))

        return VehicleSim(
            route_id=route.id,
            vehicle_id=route.vehicle_id,
            route_code=route.route_code,
            waypoints=waypoints,
            position=(depot.latitude, depot.longitude),
        )

    async def _run(self) -> None:
        """The loop. Ticks until stopped or until every vehicle is finished."""
        try:
            # `active_count > 0` means the loop also ends on its own when all
            # the work is done — no external stop needed.
            while self.running and self.active_count > 0:
                await self._tick()
                await asyncio.sleep(TICK_SECONDS)
        except asyncio.CancelledError:  # pragma: no cover
            # stop() cancels this task; that is a normal ending, not an error.
            pass
        finally:
            # Runs whether the loop was cancelled or simply ran out of work, so
            # a client is always told the simulation ended.
            self.running = False
            await manager.broadcast("SIMULATION_STOPPED", {"running": False})

    async def _tick(self) -> None:
        """Advance every active vehicle once.

        ONE session and ONE commit for the whole tick, not per vehicle: twenty
        moving vans are one transaction, not twenty.
        """
        async with AsyncSessionLocal() as db:
            # list(...) because _advance can mutate self._vehicles (via a
            # reload), and mutating a dict while iterating it raises.
            for sim in list(self._vehicles.values()):
                if sim.finished:
                    continue
                await self._advance(db, sim)
            await db.commit()

    async def _advance(self, db, sim: VehicleSim) -> None:
        """Move one vehicle for one tick, firing any arrivals it passes."""
        # distance covered this tick (km), accounting for sim speed + traffic
        #
        # km/h * (hours elapsed) * traffic. The elapsed term is the tick length
        # scaled by the clock multiplier, converted to hours by /3600.
        move_km = (
            settings.average_speed_kmh
            * (TICK_SECONDS * self.speed_multiplier / 3600.0)
            * sim.speed_factor
        )
        if move_km <= 0:
            return  # breakdown / stopped

        # A while loop, not a single step: at a high multiplier one tick can
        # cross several segments, and each crossing must fire its own arrival.
        while move_km > 0 and sim.seg_index < len(sim.waypoints) - 1:
            a = sim.waypoints[sim.seg_index]
            b = sim.waypoints[sim.seg_index + 1]
            # Raw haversine, with NO road factor — unlike the solver. The engine
            # draws a straight line between waypoints, so it measures the line
            # it actually draws.
            seg_len = haversine_km((a.latitude, a.longitude), (b.latitude, b.longitude))
            remaining = seg_len - sim.dist_into_seg

            if move_km < remaining:
                # Still mid-segment: interpolate linearly and stop here.
                sim.dist_into_seg += move_km
                # Guard a zero-length segment (two stops at the same address),
                # which would otherwise divide by zero.
                frac = sim.dist_into_seg / seg_len if seg_len > 0 else 1.0
                sim.position = (
                    a.latitude + (b.latitude - a.latitude) * frac,
                    a.longitude + (b.longitude - a.longitude) * frac,
                )
                sim.history_accum_km += move_km
                move_km = 0
            else:
                # reached waypoint b
                #
                # Consume only the part of the budget the segment used, then let
                # the loop continue into the next one with the remainder.
                move_km -= remaining
                sim.history_accum_km += remaining
                sim.seg_index += 1
                sim.dist_into_seg = 0.0
                # Snap exactly onto the waypoint, so accumulated float drift
                # cannot leave the vehicle slightly off its own stop.
                sim.position = (b.latitude, b.longitude)
                await self._on_waypoint(db, sim, b)
                if sim.finished:
                    break  # route over; do not keep spending the budget

        # persist current vehicle position + broadcast
        vehicle = (await db.execute(select(Vehicle).where(Vehicle.id == sim.vehicle_id))).scalar_one()
        vehicle.current_latitude = sim.position[0]
        vehicle.current_longitude = sim.position[1]

        if sim.history_accum_km >= 0.3:  # snapshot every ~300m of travel
            # Sampled by DISTANCE, not time. A row per vehicle per second would
            # be ~20/s for a 20-van fleet — over a million a day — for a track
            # nobody needs at that resolution. Distance sampling also behaves
            # correctly under traffic: a stationary vehicle writes nothing,
            # because it is not going anywhere.
            db.add(
                VehicleLocationHistory(
                    vehicle_id=sim.vehicle_id,
                    route_id=sim.route_id,
                    latitude=sim.position[0],
                    longitude=sim.position[1],
                    # Effective speed, so a slowed stretch is visible in history.
                    speed=settings.average_speed_kmh * sim.speed_factor,
                )
            )
            sim.history_accum_km = 0.0

        # Every tick, for every moving vehicle — this is what the map draws.
        await manager.broadcast(
            "VEHICLE_LOCATION_UPDATED",
            {
                "vehicle_id": sim.vehicle_id,
                "route_id": sim.route_id,
                # ~10cm precision is plenty for a map and keeps the payload small
                # at one message per vehicle per second.
                "latitude": round(sim.position[0], 6),
                "longitude": round(sim.position[1], 6),
                "speed_factor": sim.speed_factor,
            },
        )

    async def _on_waypoint(self, db, sim: VehicleSim, wp: Waypoint) -> None:
        """Handle arriving at a waypoint: a delivery, or the end of the route.

        This is where the simulation writes real business state — the only place
        orders become DELIVERED.
        """
        if wp.order_id is not None:
            # delivered an order
            order = (await db.execute(select(Order).where(Order.id == wp.order_id))).scalar_one()
            order.status = OrderStatus.DELIVERED
            stop = (await db.execute(select(RouteStop).where(RouteStop.id == wp.stop_id))).scalar_one()
            stop.status = RouteStopStatus.COMPLETED
            # The actual arrival, recorded beside the solver's estimate. The
            # pair is the raw material for on-time analysis.
            stop.actual_arrival = datetime.now(timezone.utc)
            route = (await db.execute(select(Route).where(Route.id == sim.route_id))).scalar_one()
            # The denormalised progress cursor, so a client can draw progress
            # without scanning every stop.
            route.progress_stop_index = stop.stop_sequence
            db.add(
                DeliveryEvent(
                    order_id=order.id,
                    route_id=sim.route_id,
                    event_type="DELIVERY_COMPLETED",
                    event_metadata={"stop_sequence": stop.stop_sequence},
                )
            )
            # TWO events on purpose: one feeds the orders table, the other the
            # activity feed, and each carries what that view needs so the client
            # joins nothing.
            await manager.broadcast(
                "ORDER_STATUS_UPDATED",
                {"order_id": order.id, "order_number": order.order_number, "status": "DELIVERED"},
            )
            await manager.broadcast(
                "DELIVERY_COMPLETED",
                {"order_id": order.id, "route_id": sim.route_id, "stop_sequence": stop.stop_sequence},
            )
        elif sim.seg_index >= len(sim.waypoints) - 1:
            # returned to depot -> route complete
            #
            # The index test distinguishes the CLOSING depot from the opening
            # one: both have order_id None, but only the last is the end.
            sim.finished = True
            route = (await db.execute(select(Route).where(Route.id == sim.route_id))).scalar_one()
            route.status = RouteStatus.COMPLETED
            route.completed_at = datetime.now(timezone.utc)
            if route.started_at:
                # Wall-clock elapsed, which under a speed multiplier is much
                # shorter than the simulated journey — a real deployment would
                # want the simulated duration instead.
                delta = (route.completed_at - route.started_at).total_seconds() / 60.0
                route.actual_duration_minutes = round(delta, 1)
            vehicle = (await db.execute(select(Vehicle).where(Vehicle.id == sim.vehicle_id))).scalar_one()
            # Back in the pool the optimizer can draw on.
            vehicle.status = VehicleStatus.AVAILABLE
            vehicle.current_load_kg = 0.0
            await manager.broadcast(
                "ROUTE_STATUS_UPDATED",
                {"route_id": sim.route_id, "route_code": sim.route_code, "status": "COMPLETED"},
            )


# The one clock for this process.
#
# A module-level singleton, imported by health.py, ws.py, main.py and
# simulation_service.py — all of which therefore see the same object. Note this
# is also the scaling limit: a second replica would run its own clock over the
# same rows. See app/simulation/README.md.
engine = SimulationEngine()
