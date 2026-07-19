"""Great-circle (Haversine) distance helpers.

These provide the local fallback used when OSRM is unavailable. Road distance
is approximated as haversine * ROAD_DISTANCE_FACTOR (default 1.25), a common
rule of thumb for urban networks. Travel time = road_distance / AVERAGE_SPEED.
"""
from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0088

Coord = tuple[float, float]  # (latitude, longitude)


def haversine_km(a: Coord, b: Coord) -> float:
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def road_distance_km(a: Coord, b: Coord, factor: float = 1.25) -> float:
    return haversine_km(a, b) * factor


def travel_time_minutes(distance_km: float, avg_speed_kmh: float = 30.0) -> float:
    if avg_speed_kmh <= 0:
        return 0.0
    return distance_km / avg_speed_kmh * 60.0
