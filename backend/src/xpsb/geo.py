"""Small geodesy helpers for the area of interest (AOI).

Areas are small (a few km²), so a local equirectangular projection is accurate
to well below a percent. Full geometry handling (shapely/pyproj) arrives in M2.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field, field_validator

EARTH_RADIUS_M = 6_371_008.8
METERS_PER_DEG_LAT = math.pi * EARTH_RADIUS_M / 180.0
MAX_AOI_AREA_KM2 = 25.0
MAX_AOI_VERTICES = 2000


class GeoJsonPolygon(BaseModel):
    type: str = Field(pattern="^Polygon$")
    coordinates: list[list[list[float]]]

    @field_validator("coordinates")
    @classmethod
    def _check_rings(cls, rings: list[list[list[float]]]) -> list[list[list[float]]]:
        if len(rings) != 1:
            raise ValueError("aoi_must_have_single_ring")
        ring = rings[0]
        if len(ring) < 4 or len(ring) > MAX_AOI_VERTICES:
            raise ValueError("aoi_vertex_count")
        for pt in ring:
            if len(pt) < 2 or not (-180 <= pt[0] <= 180 and -85 <= pt[1] <= 85):
                raise ValueError("aoi_coordinate_out_of_range")
        if ring[0][:2] != ring[-1][:2]:
            raise ValueError("aoi_ring_not_closed")
        return rings

    @property
    def ring(self) -> list[tuple[float, float]]:
        return [(p[0], p[1]) for p in self.coordinates[0]]


def meters_per_deg_lon(lat: float) -> float:
    return METERS_PER_DEG_LAT * math.cos(math.radians(lat))


def _local_xy(ring: list[tuple[float, float]]) -> tuple[list[tuple[float, float]], float, float]:
    lat0 = sum(p[1] for p in ring[:-1]) / (len(ring) - 1)
    lon0 = sum(p[0] for p in ring[:-1]) / (len(ring) - 1)
    mx = meters_per_deg_lon(lat0)
    return [((lon - lon0) * mx, (lat - lat0) * METERS_PER_DEG_LAT) for lon, lat in ring], lon0, lat0


def ring_area_m2(ring: list[tuple[float, float]]) -> float:
    xy, _, _ = _local_xy(ring)
    s = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(xy, xy[1:], strict=False))
    return abs(s) / 2.0


def ring_centroid(ring: list[tuple[float, float]]) -> tuple[float, float]:
    """Area centroid as (lon, lat)."""
    xy, lon0, lat0 = _local_xy(ring)
    a = cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(xy, xy[1:], strict=False):
        cross = x1 * y2 - x2 * y1
        a += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(a) < 1e-9:
        return lon0, lat0
    cx /= 3.0 * a
    cy /= 3.0 * a
    return lon0 + cx / meters_per_deg_lon(lat0), lat0 + cy / METERS_PER_DEG_LAT


def offset(lon: float, lat: float, east_m: float, north_m: float) -> tuple[float, float]:
    return lon + east_m / meters_per_deg_lon(lat), lat + north_m / METERS_PER_DEG_LAT
