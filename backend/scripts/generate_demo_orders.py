"""Generate N demo orders for a depot (used by the seed script and for load tests).

Usage:
    python -m scripts.generate_demo_orders --count 500 --depot 1
"""
from __future__ import annotations

import argparse
import asyncio
import random
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import func, select

from app.db.session import AsyncSessionLocal
from app.geospatial.queries import make_point
from app.models.enums import OrderPriority
from app.models.order import Order
from scripts.demo_geo import random_address, random_customer, random_point

PRIORITY_WEIGHTS = [
    (OrderPriority.LOW, 0.20),
    (OrderPriority.NORMAL, 0.45),
    (OrderPriority.HIGH, 0.25),
    (OrderPriority.URGENT, 0.10),
]


def _pick_priority(rng: random.Random) -> OrderPriority:
    r = rng.random()
    cum = 0.0
    for pr, w in PRIORITY_WEIGHTS:
        cum += w
        if r <= cum:
            return pr
    return OrderPriority.NORMAL


def _window(rng: random.Random) -> tuple[datetime, datetime]:
    today = datetime.now(timezone.utc).date()
    start_hour = rng.choice([9, 10, 11, 12, 13, 14, 15, 16])
    start = datetime.combine(today, time(start_hour, 0), tzinfo=timezone.utc)
    end = start + timedelta(hours=rng.choice([2, 3, 4]))
    return start, end


async def generate_orders(count: int, depot_id: int, seed: int | None = None) -> int:
    rng = random.Random(seed)
    async with AsyncSessionLocal() as db:
        base = (await db.execute(select(func.coalesce(func.max(Order.id), 0)))).scalar_one()
        objs = []
        for i in range(count):
            lat, lon, _zone = random_point(rng)
            address, _ = random_address(rng)
            has_window = rng.random() < 0.6
            ws, we = _window(rng) if has_window else (None, None)
            objs.append(
                Order(
                    order_number=f"ORD-{base + i + 1:05d}",
                    customer_name=random_customer(rng),
                    customer_phone=f"+9198{rng.randint(10000000, 99999999)}",
                    delivery_address=address,
                    latitude=lat,
                    longitude=lon,
                    location=make_point(lat, lon),
                    weight_kg=round(rng.uniform(1, 40), 1),
                    volume=round(rng.uniform(0.01, 0.5), 3),
                    priority=_pick_priority(rng),
                    delivery_window_start=ws,
                    delivery_window_end=we,
                    service_time_minutes=rng.choice([5, 8, 10, 12, 15]),
                    depot_id=depot_id,
                )
            )
        db.add_all(objs)
        await db.commit()
    return count


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--depot", type=int, default=1)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    n = await generate_orders(args.count, args.depot, args.seed)
    print(f"Generated {n} demo orders for depot {args.depot}.")


if __name__ == "__main__":
    asyncio.run(_main())
