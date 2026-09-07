"""PostGIS helpers and spatial queries.

Uses genuine PostGIS: geography POINT columns, ST_SetSRID/ST_MakePoint to write
them, and ST_DWithin / ST_Distance for radius search sorted by true distance.

None of these functions compute anything. Each returns a SQLAlchemy *expression*
that gets compiled into SQL, so the work happens inside Postgres — which is the
point. "Find every order within 5 km" is a search problem, and Postgres can
answer it through a GiST index instead of shipping every row to Python to be
filtered.

Typical use, one round trip:

    dist = distance_meters(Order.location, lat, lon)
    select(Order, dist).where(within(Order.location, lat, lon, 5)).order_by(dist)
"""
from __future__ import annotations

from sqlalchemy import func


def make_point(latitude: float, longitude: float):
    """SQL expression for a WGS84 geography point (note: MakePoint is lon,lat).

    This wrapper exists *because* of that note. Humans say "latitude,
    longitude"; PostGIS follows the mathematical (x, y) convention, and x is
    longitude. Swapping them raises no error — it silently relocates Delhi
    (28.5 N, 77.2 E) to 77.5 N, 28.2 E, in the Arctic Ocean, and every distance
    computed afterwards is wrong.

    So the argument order here is the human one, and the swap happens once, in
    this one place, where it can be checked. Callers never have to remember.

    SRID 4326 is WGS 84 — ordinary GPS degrees, the same thing a phone reports.
    It has to be set explicitly: ST_MakePoint alone produces a point with no
    declared coordinate system, and PostGIS refuses to compare that against a
    column that has one.
    """
    return func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326)


def distance_meters(location_col, latitude: float, longitude: float):
    """ST_Distance between a geography column and a point, in metres.

    Metres — not degrees — because the columns are declared `Geography` rather
    than `Geometry`. That distinction is load-bearing: on a Geometry column
    ST_Distance returns degrees, and a degree of longitude is a different
    number of kilometres at every latitude, so the result could not be
    thresholded or compared. Geography does the spherical maths and answers in
    a real unit.

    This is a VALUE, computed per row. Use it to sort and to report the actual
    distance — but filter with `within()` first, or Postgres will measure every
    row in the table.
    """
    return func.ST_Distance(location_col, make_point(latitude, longitude))


def within(location_col, latitude: float, longitude: float, radius_km: float):
    """ST_DWithin predicate (radius in km -> metres).

    This is a PREDICATE, and unlike `distance_meters` it is **GiST-indexable**:
    Postgres can use the spatial index to discard almost every row without
    measuring it. That is the difference between a bounded lookup and a full
    scan, which is why a radius query should always filter on this and only
    then sort by distance.

    A B-tree index is useless for "within N km" — it can order on one dimension
    at a time, and proximity is two-dimensional. GiST is what makes this fast.

    The km-to-metres conversion is here so callers can speak in kilometres while
    PostGIS geography gets the metres it expects.
    """
    return func.ST_DWithin(location_col, make_point(latitude, longitude), radius_km * 1000.0)
