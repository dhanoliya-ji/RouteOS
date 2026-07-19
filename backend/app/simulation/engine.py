"""Backend-authoritative delivery simulation.

A single asyncio loop advances every ACTIVE vehicle along its route polyline,
mutating real database state (order/route/vehicle status, location history) and
broadcasting events over WebSockets. The frontend only renders what this engine
publishes — it never invents movement.

Traffic events apply a per-route speed factor; breakdown sets it to zero.
Re-optimization is handled by simulation_service, which rebuilds a route's
remaining waypoints and the engine picks them up on the next tick.
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
SPEED_FACTORS = {"clear": 1.0, "moderate": 0.6, "severe": 0.35, "breakdown": 0.0}


@dataclass
class Waypoint:
    latitude: float
    longitude: float
    order_id: int | None  # None => depot
    stop_id: int | None


@dataclass
class VehicleSim:
    route_id: int
    vehicle_id: int
    route_code: str
    waypoints: list[Waypoint]
    seg_index: int = 0  # index of the segment start waypoint
    dist_into_seg: float = 0.0
    position: tuple[float, float] = (0.0, 0.0)
    speed_factor: float = 1.0
    finished: bool = False
    history_accum_km: float = 0.0


class SimulationEngine:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._vehicles: dict[int, VehicleSim] = {}  # keyed by route_id
        self.speed_multiplier: float = 1.0
        self.running: bool = False

    @property
    def active_count(self) -> int:
        return sum(1 for v in self._vehicles.values() if not v.finished)

    def status(self) -> dict:
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
        self.speed_multiplier = max(1.0, float(speed_multiplier))
        await self._load_routes()
        if not self._vehicles:
            return {"started": False, "reason": "No planned/active routes to simulate"}
        self.running = True
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        await manager.broadcast("SIMULATION_STARTED", self.status())
        return {"started": True, **self.status()}

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            self._task = None
        await manager.broadcast("SIMULATION_STOPPED", {"running": False})

    def set_speed(self, multiplier: float) -> None:
        self.speed_multiplier = max(1.0, float(multiplier))

    def apply_traffic(self, route_id: int, severity: str) -> dict | None:
        sim = self._vehicles.get(route_id)
        if sim is None:
            return None
        sim.speed_factor = SPEED_FACTORS.get(severity, 1.0)
        return {"route_id": route_id, "severity": severity, "speed_factor": sim.speed_factor}

    def has_route(self, route_id: int) -> bool:
        return route_id in self._vehicles

    async def reload_route(self, route_id: int) -> None:
        """Rebuild a single route's remaining waypoints (after re-optimization)."""
        async with AsyncSessionLocal() as db:
            sim = await self._build_sim(db, route_id, only_remaining=True)
        if sim is not None:
            # preserve current speed factor if the route was under traffic
            prev = self._vehicles.get(route_id)
            if prev:
                sim.position = prev.position
                sim.speed_factor = prev.speed_factor
            self._vehicles[route_id] = sim

    async def _load_routes(self) -> None:
        async with AsyncSessionLocal() as db:
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
                route.started_at = route.started_at or datetime.now(timezone.utc)
                vehicle = (
                    await db.execute(select(Vehicle).where(Vehicle.id == route.vehicle_id))
                ).scalar_one_or_none()
                if vehicle:
                    vehicle.status = VehicleStatus.IN_TRANSIT
                sim = await self._build_sim(db, route.id, only_remaining=False)
                if sim:
                    self._vehicles[route.id] = sim
            await db.commit()

    async def _build_sim(
        self, db, route_id: int, only_remaining: bool
    ) -> VehicleSim | None:
        route = (
            await db.execute(
                select(Route)
                .options(selectinload(Route.stops), selectinload(Route.depot))
                .where(Route.id == route_id)
            )
        ).scalar_one_or_none()
        if route is None or route.vehicle_id is None:
            return None
        depot = route.depot
        stops = sorted(route.stops, key=lambda s: s.stop_sequence)
        if only_remaining:
            stops = [s for s in stops if s.status == RouteStopStatus.PENDING]

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
        try:
            while self.running and self.active_count > 0:
                await self._tick()
                await asyncio.sleep(TICK_SECONDS)
        except asyncio.CancelledError:  # pragma: no cover
            pass
        finally:
            self.running = False
            await manager.broadcast("SIMULATION_STOPPED", {"running": False})

    async def _tick(self) -> None:
        async with AsyncSessionLocal() as db:
            for sim in list(self._vehicles.values()):
                if sim.finished:
                    continue
                await self._advance(db, sim)
            await db.commit()

    async def _advance(self, db, sim: VehicleSim) -> None:
        # distance covered this tick (km), accounting for sim speed + traffic
        move_km = (
            settings.average_speed_kmh
            * (TICK_SECONDS * self.speed_multiplier / 3600.0)
            * sim.speed_factor
        )
        if move_km <= 0:
            return  # breakdown / stopped

        while move_km > 0 and sim.seg_index < len(sim.waypoints) - 1:
            a = sim.waypoints[sim.seg_index]
            b = sim.waypoints[sim.seg_index + 1]
            seg_len = haversine_km((a.latitude, a.longitude), (b.latitude, b.longitude))
            remaining = seg_len - sim.dist_into_seg
            if move_km < remaining:
                sim.dist_into_seg += move_km
                frac = sim.dist_into_seg / seg_len if seg_len > 0 else 1.0
                sim.position = (
                    a.latitude + (b.latitude - a.latitude) * frac,
                    a.longitude + (b.longitude - a.longitude) * frac,
                )
                sim.history_accum_km += move_km
                move_km = 0
            else:
                # reached waypoint b
                move_km -= remaining
                sim.history_accum_km += remaining
                sim.seg_index += 1
                sim.dist_into_seg = 0.0
                sim.position = (b.latitude, b.longitude)
                await self._on_waypoint(db, sim, b)
                if sim.finished:
                    break

        # persist current vehicle position + broadcast
        vehicle = (await db.execute(select(Vehicle).where(Vehicle.id == sim.vehicle_id))).scalar_one()
        vehicle.current_latitude = sim.position[0]
        vehicle.current_longitude = sim.position[1]

        if sim.history_accum_km >= 0.3:  # snapshot every ~300m of travel
            db.add(
                VehicleLocationHistory(
                    vehicle_id=sim.vehicle_id,
                    route_id=sim.route_id,
                    latitude=sim.position[0],
                    longitude=sim.position[1],
                    speed=settings.average_speed_kmh * sim.speed_factor,
                )
            )
            sim.history_accum_km = 0.0

        await manager.broadcast(
            "VEHICLE_LOCATION_UPDATED",
            {
                "vehicle_id": sim.vehicle_id,
                "route_id": sim.route_id,
                "latitude": round(sim.position[0], 6),
                "longitude": round(sim.position[1], 6),
                "speed_factor": sim.speed_factor,
            },
        )

    async def _on_waypoint(self, db, sim: VehicleSim, wp: Waypoint) -> None:
        if wp.order_id is not None:
            # delivered an order
            order = (await db.execute(select(Order).where(Order.id == wp.order_id))).scalar_one()
            order.status = OrderStatus.DELIVERED
            stop = (await db.execute(select(RouteStop).where(RouteStop.id == wp.stop_id))).scalar_one()
            stop.status = RouteStopStatus.COMPLETED
            stop.actual_arrival = datetime.now(timezone.utc)
            route = (await db.execute(select(Route).where(Route.id == sim.route_id))).scalar_one()
            route.progress_stop_index = stop.stop_sequence
            db.add(
                DeliveryEvent(
                    order_id=order.id,
                    route_id=sim.route_id,
                    event_type="DELIVERY_COMPLETED",
                    event_metadata={"stop_sequence": stop.stop_sequence},
                )
            )
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
            sim.finished = True
            route = (await db.execute(select(Route).where(Route.id == sim.route_id))).scalar_one()
            route.status = RouteStatus.COMPLETED
            route.completed_at = datetime.now(timezone.utc)
            if route.started_at:
                delta = (route.completed_at - route.started_at).total_seconds() / 60.0
                route.actual_duration_minutes = round(delta, 1)
            vehicle = (await db.execute(select(Vehicle).where(Vehicle.id == sim.vehicle_id))).scalar_one()
            vehicle.status = VehicleStatus.AVAILABLE
            vehicle.current_load_kg = 0.0
            await manager.broadcast(
                "ROUTE_STATUS_UPDATED",
                {"route_id": sim.route_id, "route_code": sim.route_code, "status": "COMPLETED"},
            )


engine = SimulationEngine()
