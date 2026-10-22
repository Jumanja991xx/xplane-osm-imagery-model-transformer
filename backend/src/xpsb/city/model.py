"""A building of the city mode, whatever the source (official 3D data, OSM)."""

from __future__ import annotations

from dataclasses import dataclass, field

Vec3 = tuple[float, float, float]


@dataclass
class CityBuilding:
    id: str
    source: str  # "lod2" | "osm"
    # Outline on the ground: rings of (lon, lat), outer first, then courtyards.
    footprint: list[list[tuple[float, float]]]
    ground_z: float | None = None  # metres above sea level (official data only)
    eave_m: float = 0.0  # wall height above ground
    ridge_m: float = 0.0  # highest point above ground
    roof_type: str = "flat"  # flat, gabled, hipped, mansard, pyramid, shed, mixed ...
    use: str = "residential"  # residential, commercial, industrial, public, farm, ...
    levels: int | None = None
    # Official 3D data: the real faces as UTM easting, northing and height above
    # sea level. Without them walls are extruded and the roof is generated.
    walls: list[list[Vec3]] = field(default_factory=list)
    roofs: list[list[Vec3]] = field(default_factory=list)
    zone: int | None = None  # UTM zone of the faces
    tags: dict[str, str] = field(default_factory=dict)
    # Filled in by the classification:
    style: str = ""
    variant: int = 0
    roof_color: tuple[int, int, int] | None = None
