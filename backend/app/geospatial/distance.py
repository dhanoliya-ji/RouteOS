"""Great-circle (Haversine) distance helpers.

These provide the local fallback used when OSRM is unavailable. Road distance
is approximated as haversine * ROAD_DISTANCE_FACTOR (default 1.25), a common
rule of thumb for urban networks. Travel time = road_distance / AVERAGE_SPEED.

Pure functions over plain tuples: no database, no settings, no imports from
`app`. That is what lets the solver and the simulation both use them freely, and
what makes them trivial to test.
"""
from __future__ import annotations

import math

# Earth's mean radius. The planet is an oblate spheroid — wider at the equator
# than pole to pole — so treating it as a sphere of this radius carries a small
# inherent error (well under 1% at city scale). That is far smaller than the
# straight-line-vs-road error the factor below corrects for, so it is not the
# thing to worry about here.
EARTH_RADIUS_KM = 6371.0088

Coord = tuple[float, float]  # (latitude, longitude)


def haversine_km(a: Coord, b: Coord) -> float:
    """Distance in km along the Earth's surface between two lat/lon points.

    You cannot use Pythagoras on latitude and longitude: they are angles, and a
    degree of longitude shrinks from ~111 km at the equator to 0 at the poles.
    The haversine formula solves the correct spherical triangle instead.

        a = sin²(Δlat/2) + cos(lat₁)·cos(lat₂)·sin²(Δlon/2)
        d = 2R · asin(√a)

    The `cos(lat₁)·cos(lat₂)` term is what accounts for that shrinking, which is
    why the formula stays accurate away from the equator.
    """
    # Convert to radians FIRST. math.sin/cos take radians, and passing degrees
    # is the classic silent bug in this kind of code — no error is raised, the
    # answers are simply wrong by a large factor.
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    # asin(sqrt(h)) rather than the equivalent atan2 form; both are standard,
    # and h cannot exceed 1 for real coordinates so sqrt is safe here.
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def road_distance_km(a: Coord, b: Coord, factor: float = 1.25) -> float:
    """Approximate driving distance: the straight line, scaled up.

    Roads bend, one-way systems force detours, and rivers need bridges, so
    actual driving distance exceeds the crow-flies distance. Multiplying by
    ~1.25 is a standard rule of thumb for urban networks.

    It is an approximation, and knowingly so — the honest justification is that
    a real alternative exists: set USE_OSRM=true and optimization/matrix.py
    fetches true road distances from a routing engine instead. This is the
    offline fallback, not the ambition.

    Note the default is repeated here rather than read from settings, so the
    function stays free of configuration and testable in isolation. Callers
    pass settings.road_distance_factor explicitly.
    """
    return haversine_km(a, b) * factor


def travel_time_minutes(distance_km: float, avg_speed_kmh: float = 30.0) -> float:
    """Minutes to cover a distance at one flat average speed.

    A single average speed for everything: no traffic model, no road classes,
    no time-of-day effect. The simulation layers a traffic factor on top of
    this, but the solver plans against this flat figure.
    """
    # Guard against a zero or negative speed producing a ZeroDivisionError or a
    # nonsensical negative duration. Returning 0.0 keeps callers arithmetic-safe
    # rather than making every one of them check first.
    if avg_speed_kmh <= 0:
        return 0.0
    return distance_km / avg_speed_kmh * 60.0
