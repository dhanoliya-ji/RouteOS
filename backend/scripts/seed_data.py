"""Idempotent demo seed: users, one depot, 20 vehicles, ~150 orders.

Run:  python -m scripts.seed_data
Safe to run repeatedly — it no-ops if a depot already exists.

That idempotency matters because docker-entrypoint.sh runs this on EVERY
container start. Without the early exit, each restart would add another depot,
another twenty vans and another 150 orders.
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

# (type, capacity_kg, capacity_volume). A deliberately MIXED fleet: a 30 kg bike
# and a 1500 kg truck in the same problem is what makes the solver's capacity
# dimension do interesting work — a uniform fleet would make every vehicle
# interchangeable and the assignment trivial.
VEHICLE_TYPES = [
    ("BIKE", 30, 0.3), ("MINI_VAN", 300, 3.0), ("VAN", 600, 6.0),
    ("VAN", 800, 8.0), ("TRUCK", 1500, 16.0),
]
# Twenty synthetic driver names, cycled to fill the fleet. Note the leading
# space on one entry, which is why the assignment below calls .strip().
DRIVERS = [
    "Ravi Kumar", "Sunil Yadav", "Amit Chauhan", "Deepak Rana", "Manoj Bisht",
    "Suresh Pillai", "Rakesh Jha", "Vinod Negi", "Ashok Mehta", "Pawan Saini",
    "Gopal Das", "Naveen Rawat", "Harish Goel", "Sanjay Dutt", "Mohan Lal",
    " Imran Khan", "Rajesh Nair", "Kunal Sethi", "Anil Kapadia", "Yogesh Tomar",
]


async def _seed_users(db) -> None:
    """Create the three demo logins, one per role, if they are missing.

    Checked and created individually — NOT covered by the depot guard below —
    so the accounts can be restored on an existing database if one is deleted.

    Credentials come from settings, so a deployment can override them rather
    than shipping with the documented defaults.
    """
    accounts = [
        (settings.demo_admin_email, "Ava Admin", settings.demo_admin_password, UserRole.ADMIN),
        (settings.demo_dispatcher_email, "Dev Dispatcher", settings.demo_dispatcher_password, UserRole.DISPATCHER),
        (settings.demo_viewer_email, "Vic Viewer", settings.demo_viewer_password, UserRole.VIEWER),
    ]
    for email, name, pw, role in accounts:
        exists = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if not exists:
            # Hashed like any real password — the seed does not take a shortcut
            # that would leave these accounts different from a registered one.
            db.add(User(name=name, email=email, password_hash=hash_password(pw), role=role))
    await db.commit()


async def _seed_depot(db) -> Depot:
    """Create the single demo depot from the constants in demo_geo."""
    depot = Depot(
        name=DEPOT["name"],
        address=DEPOT["address"],
        latitude=DEPOT["latitude"],
        longitude=DEPOT["longitude"],
        # The PostGIS point as well as the floats, matching what
        # depot_service.create_depot would do.
        location=make_point(DEPOT["latitude"], DEPOT["longitude"]),
    )
    db.add(depot)
    await db.commit()
    # refresh to get the generated id, which the vehicles and orders need.
    await db.refresh(depot)
    return depot


async def _seed_vehicles(db, depot: Depot, rng: random.Random) -> None:
    """Create twenty vehicles at the depot, cycling through the type table."""
    for i in range(20):
        # Modulo so the five types repeat evenly — four of each.
        vtype, cap, vol = VEHICLE_TYPES[i % len(VEHICLE_TYPES)]
        db.add(
            Vehicle(
                # A plausible Delhi-style plate. Random rather than sequential,
                # so the demo does not look mechanically generated. (Nothing
                # guarantees uniqueness here; a collision would violate the
                # UNIQUE constraint, but with ~10^5 combinations over 20 rows it
                # is vanishingly unlikely.)
                registration_number=f"DL{rng.randint(1, 9)}C{rng.choice('ABXYZ')}{rng.randint(1000, 9999)}",
                # .strip() because one DRIVERS entry has a leading space.
                driver_name=DRIVERS[i % len(DRIVERS)].strip(),
                vehicle_type=vtype,
                capacity_kg=float(cap),
                capacity_volume=vol,
                current_load_kg=0.0,
                # AVAILABLE, so the optimizer will plan for them immediately.
                status=VehicleStatus.AVAILABLE,
                # Parked at the depot, which gives the map something to draw
                # before any simulation has run.
                current_latitude=depot.latitude,
                current_longitude=depot.longitude,
                home_depot_id=depot.id,
                # Varied ranges, so the solver's Distance dimension binds for
                # some vehicles and not others.
                max_route_distance_km=float(rng.choice([120, 150, 200, 250])),
            )
        )
    await db.commit()


async def seed() -> None:
    """Seed the database. Safe to call repeatedly."""
    # A FIXED seed, so every fresh database gets the identical fleet. That is
    # what makes a screenshot, a benchmark or a bug report reproducible.
    rng = random.Random(42)

    async with AsyncSessionLocal() as db:
        # Users first and unconditionally — see _seed_users.
        await _seed_users(db)

        # THE IDEMPOTENCY GUARD. A depot's existence stands in for "this
        # database has already been seeded", because the depot is created first
        # of the three and everything else hangs off it.
        #
        # Coarse but adequate: it does mean a database seeded and then emptied of
        # orders will not be re-filled while its depot remains.
        existing_depot = (await db.execute(select(func.count(Depot.id)))).scalar_one()
        if existing_depot:
            print("[seed] Depot already present — skipping fleet/order seed (idempotent).")
            return

        depot = await _seed_depot(db)
        await _seed_vehicles(db, depot, rng)

    # Orders are generated AFTER the session closes, because generate_orders
    # opens its own. A different seed (7) from the fleet's 42, so the two
    # datasets are independent rather than correlated.
    order_count = random.Random(7).randint(140, 170)
    await generate_orders(order_count, depot.id, seed=7)
    print(f"[seed] Seeded users, 1 depot, 20 vehicles, {order_count} orders.")


if __name__ == "__main__":
    asyncio.run(seed())
