"""Distance / travel-time matrix construction.

Primary source: OSRM ``/table`` service (when USE_OSRM=true and reachable).
Fallback: Haversine * road factor. The fallback guarantees the optimizer always
runs, even with no internet access.

Matrices are symmetric lists-of-lists indexed by node. Node 0 is conventionally
the depot; nodes 1..n are the orders in the given order.
"""
from __future__ import annotations

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.geospatial.distance import Coord, road_distance_km, travel_time_minutes

logger = get_logger(__name__)


class DistanceMatrix:
    def __init__(self, distances_km: list[list[float]], durations_min: list[list[float]], source: str):
        self.distances_km = distances_km
        self.durations_min = durations_min
        self.source = source  # "osrm" | "haversine"
        self.size = len(distances_km)


def _haversine_matrix(coords: list[Coord]) -> DistanceMatrix:
    n = len(coords)
    dist = [[0.0] * n for _ in range(n)]
    dur = [[0.0] * n for _ in range(n)]
    factor = settings.road_distance_factor
    speed = settings.average_speed_kmh
    for i in range(n):
        for j in range(i + 1, n):
            d = road_distance_km(coords[i], coords[j], factor)
            t = travel_time_minutes(d, speed)
            dist[i][j] = dist[j][i] = d
            dur[i][j] = dur[j][i] = t
    return DistanceMatrix(dist, dur, "haversine")


def _osrm_matrix(coords: list[Coord]) -> DistanceMatrix | None:
    # OSRM expects lon,lat;lon,lat...
    locs = ";".join(f"{lon},{lat}" for lat, lon in coords)
    url = f"{settings.osrm_base_url}/table/v1/driving/{locs}"
    params = {"annotations": "distance,duration"}
    try:
        resp = httpx.get(url, params=params, timeout=8.0)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "Ok":
            return None
        # OSRM distances are metres, durations seconds
        dist = [[(c or 0.0) / 1000.0 for c in row] for row in data["distances"]]
        dur = [[(c or 0.0) / 60.0 for c in row] for row in data["durations"]]
        return DistanceMatrix(dist, dur, "osrm")
    except Exception as exc:  # noqa: BLE001 - network fallbacks are expected
        logger.warning("OSRM table request failed (%s); using haversine fallback", exc)
        return None


def build_matrix(coords: list[Coord]) -> DistanceMatrix:
    """Build a distance/duration matrix for the given coordinates."""
    if settings.use_osrm and len(coords) <= 100:
        m = _osrm_matrix(coords)
        if m is not None:
            return m
    return _haversine_matrix(coords)
