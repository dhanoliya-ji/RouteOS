"""Idempotent demo seed: users, one depot, 20 vehicles, ~150 orders.

Run:  python -m scripts.seed_data
Safe to run repeatedly — it no-ops if a depot already exists.
"""
from __future__ import annotations

import asyncio
import random

from sqlalchemy import func, select

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.geospatial.queries import make_point
from app.models.depot import Depot
from app.models.enums import UserRole, VehicleStatus
from app.models.user import User
from app.models.vehicle import Vehicle
from scripts.demo_geo import DEPOT
from scripts.generate_demo_orders import generate_orders

VEHICLE_TYPES = [
    ("BIKE", 30, 0.3), ("MINI_VAN", 300, 3.0), ("VAN", 600, 6.0),
    ("VAN", 800, 8.0), ("TRUCK", 1500, 16.0),
]
DRIVERS = [
    "Ravi Kumar", "Sunil Yadav", "Amit Chauhan", "Deepak Rana", "Manoj Bisht",
    "Suresh Pillai", "Rakesh Jha", "Vinod Negi", "Ashok Mehta", "Pawan Saini",
    "Gopal Das", "Naveen Rawat", "Harish Goel", "Sanjay Dutt", "Mohan Lal",
    " Imran Khan", "Rajesh Nair", "Kunal Sethi", "Anil Kapadia", "Yogesh Tomar",
]


async def _seed_users(db) -> None:
    accounts = [
        (settings.demo_admin_email, "Ava Admin", settings.demo_admin_password, UserRole.ADMIN),
        (settings.demo_dispatcher_email, "Dev Dispatcher", settings.demo_dispatcher_password, UserRole.DISPATCHER),
        (settings.demo_viewer_email, "Vic Viewer", settings.demo_viewer_password, UserRole.VIEWER),
    ]
    for email, name, pw, role in accounts:
        exists = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if not exists:
            db.add(User(name=name, email=email, password_hash=hash_password(pw), role=role))
    await db.commit()


async def _seed_depot(db) -> Depot:
    depot = Depot(
        name=DEPOT["name"],
        address=DEPOT["address"],
        latitude=DEPOT["latitude"],
        longitude=DEPOT["longitude"],
        location=make_point(DEPOT["latitude"], DEPOT["longitude"]),
    )
    db.add(depot)
    await db.commit()
    await db.refresh(depot)
    return depot


async def _seed_vehicles(db, depot: Depot, rng: random.Random) -> None:
    for i in range(20):
        vtype, cap, vol = VEHICLE_TYPES[i % len(VEHICLE_TYPES)]
        db.add(
            Vehicle(
                registration_number=f"DL{rng.randint(1, 9)}C{rng.choice('ABXYZ')}{rng.randint(1000, 9999)}",
                driver_name=DRIVERS[i % len(DRIVERS)].strip(),
                vehicle_type=vtype,
                capacity_kg=float(cap),
                capacity_volume=vol,
                current_load_kg=0.0,
                status=VehicleStatus.AVAILABLE,
                current_latitude=depot.latitude,
                current_longitude=depot.longitude,
                home_depot_id=depot.id,
                max_route_distance_km=float(rng.choice([120, 150, 200, 250])),
            )
        )
    await db.commit()


async def seed() -> None:
    rng = random.Random(42)
    async with AsyncSessionLocal() as db:
        await _seed_users(db)
        existing_depot = (await db.execute(select(func.count(Depot.id)))).scalar_one()
        if existing_depot:
            print("[seed] Depot already present — skipping fleet/order seed (idempotent).")
            return
        depot = await _seed_depot(db)
        await _seed_vehicles(db, depot, rng)
    order_count = random.Random(7).randint(140, 170)
    await generate_orders(order_count, depot.id, seed=7)
    print(f"[seed] Seeded users, 1 depot, 20 vehicles, {order_count} orders.")


if __name__ == "__main__":
    asyncio.run(seed())
