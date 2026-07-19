"""Fictional Delhi NCR geography used to generate realistic demo coordinates.

All customer data is synthetic. Coordinates are jittered around real district
centroids so orders spread believably across the metro region.
"""
from __future__ import annotations

import random

# (name, center_lat, center_lon, spread_degrees)
NCR_ZONES = [
    ("Central Delhi", 28.6330, 77.2190, 0.045),
    ("South Delhi", 28.5245, 77.2066, 0.045),
    ("Gurugram", 28.4595, 77.0266, 0.055),
    ("Noida", 28.5355, 77.3910, 0.050),
    ("Ghaziabad", 28.6692, 77.4538, 0.050),
    ("Faridabad", 28.4089, 77.3178, 0.050),
]

DEPOT = {
    "name": "RouteOS Central Hub — Okhla",
    "address": "Okhla Industrial Area Phase II, New Delhi",
    "latitude": 28.5478,
    "longitude": 77.2733,
}

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
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


def random_address(rng: random.Random) -> tuple[str, str]:
    zone = rng.choice(NCR_ZONES)
    street = rng.choice(STREETS)
    house = rng.randint(1, 240)
    return f"{house}, {street}, {zone[0]}", zone[0]


def random_point(rng: random.Random) -> tuple[float, float, str]:
    name, lat, lon, spread = rng.choice(NCR_ZONES)
    return (
        round(lat + rng.uniform(-spread, spread), 6),
        round(lon + rng.uniform(-spread, spread), 6),
        name,
    )
