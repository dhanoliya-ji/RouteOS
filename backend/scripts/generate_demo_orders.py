"""Generate N demo orders for a depot (used by the seed script and for load tests).

Usage:
    python -m scripts.generate_demo_orders --count 500 --depot 1

Two callers: seed_data imports `generate_orders` directly, and the command line
runs `_main`. Keeping the work in an importable function rather than only in
__main__ is what allows both.
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

# (priority, share of orders). Weighted to look like a real workload: mostly
# NORMAL, a meaningful HIGH tail, and URGENT rare enough that the solver's
# priority handling is exercised without dominating every plan.
#
# Shares must sum to 1.0 for _pick_priority's cumulative walk to cover the
# whole range.
PRIORITY_WEIGHTS = [
    (OrderPriority.LOW, 0.20),
    (OrderPriority.NORMAL, 0.45),
    (OrderPriority.HIGH, 0.25),
    (OrderPriority.URGENT, 0.10),
]


def _pick_priority(rng: random.Random) -> OrderPriority:
    """Weighted random choice, by cumulative sum.

    Draw once in [0, 1), then walk the list accumulating shares until the total
    passes the draw — the standard way to sample from a discrete distribution.

    The trailing return is a floating-point safety net: if the shares summed to
    slightly under 1.0, a draw in the gap would fall through the loop.
    """
    r = rng.random()
    cum = 0.0
    for pr, w in PRIORITY_WEIGHTS:
        cum += w
        if r <= cum:
            return pr
    return OrderPriority.NORMAL


def _window(rng: random.Random) -> tuple[datetime, datetime]:
    """A delivery window somewhere in a working day.

    Starts on the hour between 09:00 and 16:00 and runs 2-4 hours. Both bounds
    are UTC, matching how the columns are stored.

    Windows of a few hours rather than minutes matter for the demo: a tight
    window makes most problems infeasible, and the solver would report almost
    everything as unassigned.
    """
    today = datetime.now(timezone.utc).date()
    start_hour = rng.choice([9, 10, 11, 12, 13, 14, 15, 16])
    start = datetime.combine(today, time(start_hour, 0), tzinfo=timezone.utc)
    end = start + timedelta(hours=rng.choice([2, 3, 4]))
    return start, end


async def generate_orders(count: int, depot_id: int, seed: int | None = None) -> int:
    """Insert `count` demo orders for one depot. Returns how many were made.

    `seed=None` means genuinely random; passing an int makes the dataset
    reproducible.
    """
    rng = random.Random(seed)
    async with AsyncSessionLocal() as db:
        # Read the current highest id ONCE and number the batch from it, rather
        # than querying per order. Same MAX(id) approach as
        # order_service._next_order_number, and the same caveat: not safe
        # against a concurrent writer, which would collide on the UNIQUE
        # order_number. Fine for a seeding script run by hand.
        base = (await db.execute(select(func.coalesce(func.max(Order.id), 0)))).scalar_one()

        objs = []
        for i in range(count):
            lat, lon, _zone = random_point(rng)
            # The zone from the address is discarded (see the note in
            # demo_geo.random_address) — only the text is used.
            address, _ = random_address(rng)
            # 60% get a window, so a generated problem mixes constrained and
            # unconstrained orders and exercises both solver paths.
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
                    # Set the PostGIS point too, so the seeded orders work with
                    # /orders/nearby — a plain insert of lat/lng would leave
                    # them invisible to radius search.
                    location=make_point(lat, lon),
                    # 1-40 kg against vehicle capacities of 30-1500 kg, so a
                    # single van holds a useful number of orders and the
                    # capacity constraint actually binds.
                    weight_kg=round(rng.uniform(1, 40), 1),
                    volume=round(rng.uniform(0.01, 0.5), 3),
                    priority=_pick_priority(rng),
                    delivery_window_start=ws,
                    delivery_window_end=we,
                    service_time_minutes=rng.choice([5, 8, 10, 12, 15]),
                    depot_id=depot_id,
                )
            )
        # add_all + one commit: a single transaction and a single flush for the
        # whole batch, rather than a round trip per order.
        db.add_all(objs)
        await db.commit()
    return count


async def _main() -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--depot", type=int, default=1)
    # No default seed, so running this twice by hand produces different data —
    # the opposite of seed_data, which fixes its seed deliberately.
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    n = await generate_orders(args.count, args.depot, args.seed)
    print(f"Generated {n} demo orders for depot {args.depot}.")


if __name__ == "__main__":
    # asyncio.run because everything below the session factory is async.
    asyncio.run(_main())
