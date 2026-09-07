"""Distance / travel-time matrix construction.

Primary source: OSRM ``/table`` service (when USE_OSRM=true and reachable).
Fallback: Haversine * road factor. The fallback guarantees the optimizer always
runs, even with no internet access.

Matrices are symmetric lists-of-lists indexed by node. Node 0 is conventionally
the depot; nodes 1..n are the orders in the given order.

Why precompute at all
---------------------
The solver asks "what does it cost to get from node i to node j?" thousands of
times per second while searching. Computing that on demand — a haversine call,
or worse an HTTP request — would dominate the runtime. So every pair is computed
once up front and answered thereafter by a list index, which is O(1).

The cost is O(n²) time and memory: 150 orders means a 151x151 table, about
23,000 pairs. That is the real reason a very large order count becomes
expensive before the search has even started.
"""
from __future__ import annotations

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.geospatial.distance import Coord, road_distance_km, travel_time_minutes

logger = get_logger(__name__)


class DistanceMatrix:
    """Two n x n lookup tables plus a note of where they came from.

    Both are indexed [from_node][to_node]:

        distances_km[0][3]   km from the depot to the third order
        durations_min[2][1]  minutes from order 2 to order 1

    Distance and duration are kept separately rather than deriving one from the
    other, because OSRM reports them independently — a motorway detour can be
    longer in km yet faster in minutes. Collapsing them would throw away exactly
    the information that makes MIN_TIME different from MIN_DISTANCE.
    """

    def __init__(self, distances_km: list[list[float]], durations_min: list[list[float]], source: str):
        self.distances_km = distances_km
        self.durations_min = durations_min
        self.source = source  # "osrm" | "haversine"
        # Cached so callers don't recompute len() while iterating; also the
        # number of nodes including the depot, i.e. len(orders) + 1.
        self.size = len(distances_km)


def _haversine_matrix(coords: list[Coord]) -> DistanceMatrix:
    """Build the matrix locally, with no network involved.

    Exploits symmetry: the straight-line distance from i to j equals j to i, so
    only the upper triangle is computed and each result is written to both
    cells. That halves the work — n(n-1)/2 haversine calls instead of n².

    (A real road network is NOT symmetric — one-way streets — which is why the
    OSRM path below fills every cell independently and does not do this.)
    """
    n = len(coords)
    # Pre-allocate n x n of zeros. The diagonal stays 0.0 throughout, which is
    # correct: the distance from a node to itself.
    dist = [[0.0] * n for _ in range(n)]
    dur = [[0.0] * n for _ in range(n)]

    # Read the settings once rather than per pair — this loop runs O(n²) times.
    factor = settings.road_distance_factor
    speed = settings.average_speed_kmh

    for i in range(n):
        for j in range(i + 1, n):  # upper triangle only
            d = road_distance_km(coords[i], coords[j], factor)
            t = travel_time_minutes(d, speed)
            # Mirror into both halves.
            dist[i][j] = dist[j][i] = d
            dur[i][j] = dur[j][i] = t
    return DistanceMatrix(dist, dur, "haversine")


def _osrm_matrix(coords: list[Coord]) -> DistanceMatrix | None:
    """Ask an OSRM server for real road distances. `None` on any failure.

    Returning None rather than raising is deliberate: the caller treats it as
    "use the fallback", so an unreachable or rate-limited routing service
    degrades the *quality* of the answer without ever failing the request.
    """
    # OSRM expects lon,lat;lon,lat...
    #
    # Note the swap in the f-string: we unpack (lat, lon) and emit lon first.
    # Same convention trap as PostGIS — getting it wrong produces a plausible
    # matrix full of wrong numbers rather than an error.
    locs = ";".join(f"{lon},{lat}" for lat, lon in coords)
    url = f"{settings.osrm_base_url}/table/v1/driving/{locs}"
    # Both annotations in one request: two round trips would double the latency
    # and could disagree if the server updated between them.
    params = {"annotations": "distance,duration"}
    try:
        # A short timeout on purpose. The fallback is cheap and always works, so
        # waiting a long time for a public demo server is worse than not using
        # it — the whole solve is on a clock.
        resp = httpx.get(url, params=params, timeout=8.0)
        resp.raise_for_status()
        data = resp.json()
        # OSRM signals application-level problems in the body with HTTP 200, so
        # the status check above is not sufficient on its own.
        if data.get("code") != "Ok":
            return None
        # OSRM distances are metres, durations seconds
        #
        # `c or 0.0` guards against nulls, which OSRM returns for a pair it
        # could not route (an unreachable island, a coordinate off the network).
        # Zero is a deliberate lie, but a survivable one: it makes that arc look
        # free rather than crashing the solve on a None.
        dist = [[(c or 0.0) / 1000.0 for c in row] for row in data["distances"]]
        dur = [[(c or 0.0) / 60.0 for c in row] for row in data["durations"]]
        return DistanceMatrix(dist, dur, "osrm")
    except Exception as exc:  # noqa: BLE001 - network fallbacks are expected
        logger.warning("OSRM table request failed (%s); using haversine fallback", exc)
        return None


def build_matrix(coords: list[Coord]) -> DistanceMatrix:
    """Build a distance/duration matrix for the given coordinates.

    Tries the real road network first when enabled, and always has a local
    answer to fall back on.

    The `<= 100` cap is a property of the public OSRM demo server, which rejects
    larger tables — and note a table request grows quadratically, so 100 points
    is already 10,000 pairs. A self-hosted OSRM would allow more, at which point
    this limit is worth revisiting.
    """
    if settings.use_osrm and len(coords) <= 100:
        m = _osrm_matrix(coords)
        if m is not None:
            return m
    return _haversine_matrix(coords)
