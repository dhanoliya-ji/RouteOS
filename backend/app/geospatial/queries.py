"""PostGIS helpers and spatial queries.

Uses genuine PostGIS: geography POINT columns, ST_SetSRID/ST_MakePoint to write
them, and ST_DWithin / ST_Distance for radius search sorted by true distance.
"""
from __future__ import annotations

from sqlalchemy import func


def make_point(latitude: float, longitude: float):
    """SQL expression for a WGS84 geography point (note: MakePoint is lon,lat)."""
    return func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326)


def distance_meters(location_col, latitude: float, longitude: float):
    """ST_Distance between a geography column and a point, in metres."""
    return func.ST_Distance(location_col, make_point(latitude, longitude))


def within(location_col, latitude: float, longitude: float, radius_km: float):
    """ST_DWithin predicate (radius in km -> metres)."""
    return func.ST_DWithin(location_col, make_point(latitude, longitude), radius_km * 1000.0)
