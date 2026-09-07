"""Fictional Delhi NCR geography used to generate realistic demo coordinates.

All customer data is synthetic. Coordinates are jittered around real district
centroids so orders spread believably across the metro region.

Pure data and pure functions — no database, no imports from `app`. This module
is the source the two seeding scripts draw on.

Why bother with real centroids at all: orders scattered uniformly over a
bounding box would make every optimizer's job artificially easy and uniform.
Clustering them around actual districts, with gaps between, produces the kind of
problem a real dispatcher faces — and makes the greedy baseline's weakness
(stranding a distant cluster) actually show up.
"""
from __future__ import annotations

import random

# (name, center_lat, center_lon, spread_degrees)
#
# `spread` is a radius in DEGREES, applied as uniform jitter around the centre.
# At this latitude 0.045 degrees is roughly 5 km, so each zone is about a 10 km
# square — realistic for a delivery district.
NCR_ZONES = [
    ("Central Delhi", 28.6330, 77.2190, 0.045),
    ("South Delhi", 28.5245, 77.2066, 0.045),
    ("Gurugram", 28.4595, 77.0266, 0.055),
    ("Noida", 28.5355, 77.3910, 0.050),
    ("Ghaziabad", 28.6692, 77.4538, 0.050),
    ("Faridabad", 28.4089, 77.3178, 0.050),
]

# The single depot the demo fleet works from — node 0 of every optimization run.
# Okhla is roughly central to the six zones above, which is what makes the
# routing problem sensible rather than lopsided.
DEPOT = {
    "name": "RouteOS Central Hub — Okhla",
    "address": "Okhla Industrial Area Phase II, New Delhi",
    "latitude": 28.5478,
    "longitude": 77.2733,
}

# Name pools for generated addresses and customers. Purely cosmetic — they make
# the demo data readable in a UI, and nothing depends on their content.
STREETS = [
    "MG Road", "Ring Road", "Sector 18", "DLF Phase 3", "Vasant Kunj",
    "Rajouri Garden", "Lajpat Nagar", "Sector 62", "Golf Course Road",
    "Nehru Place", "Connaught Place", "Indirapuram", "Sushant Lok",
]
FIRST_NAMES = ["Aarav", "Isha", "Kabir", "Meera", "Rohan", "Ananya", "Vikram",
               "Neha", "Arjun", "Priya", "Sameer", "Diya", "Karan", "Tara"]
LAST_NAMES = ["Sharma", "Verma", "Kapoor", "Singh", "Nair", "Reddy", "Bose",
              "Gupta", "Iyer", "Malhotra", "Chopra", "Das"]


def random_customer(rng: random.Random) -> str:
    """A synthetic customer name.

    Every function here takes an `rng` rather than calling the `random` module
    directly. That is what makes the seeding reproducible: the caller owns the
    seed, so the same seed yields the same dataset — which is what lets a
    benchmark number be compared across code changes.
    """
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


def random_address(rng: random.Random) -> tuple[str, str]:
    """A synthetic address, and the zone name it belongs to.

    NOTE: the zone is picked independently of `random_point` below, so an
    order's printed address and its actual coordinates generally name different
    districts. Harmless for a demo — the coordinates are what the optimizer
    uses and the text is only decoration — but they are not consistent with
    each other.
    """
    zone = rng.choice(NCR_ZONES)
    street = rng.choice(STREETS)
    house = rng.randint(1, 240)
    return f"{house}, {street}, {zone[0]}", zone[0]


def random_point(rng: random.Random) -> tuple[float, float, str]:
    """A coordinate inside a randomly chosen zone: (lat, lon, zone_name).

    Two-step: pick a district, then jitter uniformly within its spread. That
    two-level choice is what produces clustering — a single uniform draw over
    the whole region would not.

    Rounded to 6 decimal places, about 10 cm — far finer than the data
    deserves, but it keeps the stored values tidy.
    """
    name, lat, lon, spread = rng.choice(NCR_ZONES)
    return (
        round(lat + rng.uniform(-spread, spread), 6),
        round(lon + rng.uniform(-spread, spread), 6),
        name,
    )
