"""DSF text builder for DSFTool --text2dsf (overlay DSFs only).

Rules verified against xptools (README.dsf2text, WED_DSFExport.cpp):
- header "A / 800 / DSF2TEXT"
- sim/west|east|north|south must come after all other properties
- every point must lie inside the 1x1 degree tile
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from xpsb import APP_NAME, __version__

EXCLUSION_PROPERTIES = {
    "objects": "sim/exclude_obj",
    "facades": "sim/exclude_fac",
    "forests": "sim/exclude_for",
    "polygons": "sim/exclude_pol",
    "roads": "sim/exclude_net",
    "lines": "sim/exclude_lin",
    "strings": "sim/exclude_str",
    "beaches": "sim/exclude_bch",
}

# Points closer than this to a tile edge (in degrees, ~1 cm) are treated as on the edge.
_EDGE_EPS = 1e-7


@dataclass(frozen=True)
class Tile:
    lon: int  # west edge
    lat: int  # south edge

    @staticmethod
    def containing(lon: float, lat: float) -> Tile:
        return Tile(math.floor(lon), math.floor(lat))

    def contains(self, lon: float, lat: float) -> bool:
        return (
            self.lon - _EDGE_EPS <= lon <= self.lon + 1 + _EDGE_EPS
            and self.lat - _EDGE_EPS <= lat <= self.lat + 1 + _EDGE_EPS
        )

    @property
    def dsf_relative_path(self) -> str:
        bucket_lat = math.floor(self.lat / 10) * 10
        bucket_lon = math.floor(self.lon / 10) * 10
        return (
            f"Earth nav data/{bucket_lat:+03d}{bucket_lon:+04d}/{self.lat:+03d}{self.lon:+04d}.dsf"
        )


@dataclass
class PolygonInstance:
    def_index: int
    param: int
    # Each winding is a list of points; a point is (lon, lat) or (lon, lat, s, t).
    windings: list[list[tuple[float, ...]]]


@dataclass
class DsfText:
    tile: Tile
    object_defs: list[str] = field(default_factory=list)
    polygon_defs: list[str] = field(default_factory=list)
    objects: list[tuple[int, float, float, float]] = field(default_factory=list)
    polygons: list[PolygonInstance] = field(default_factory=list)
    # (property name, "west/south/east/north")
    exclusions: list[tuple[str, str]] = field(default_factory=list)

    def add_object_def(self, path: str) -> int:
        self.object_defs.append(_check_resource_path(path))
        return len(self.object_defs) - 1

    def add_polygon_def(self, path: str) -> int:
        self.polygon_defs.append(_check_resource_path(path))
        return len(self.polygon_defs) - 1

    def add_object(self, def_index: int, lon: float, lat: float, heading: float) -> None:
        self._require_inside(lon, lat)
        self.objects.append((def_index, lon, lat, heading % 360.0))

    def add_polygon(
        self, def_index: int, param: int, windings: list[list[tuple[float, ...]]]
    ) -> None:
        depth = {len(p) for w in windings for p in w}
        if len(depth) != 1 or depth.pop() not in (2, 4):
            raise ValueError("polygon points must all have 2 or 4 coordinates")
        for w in windings:
            if len(w) < 3:
                raise ValueError("winding needs at least 3 points")
            for p in w:
                self._require_inside(p[0], p[1])
        self.polygons.append(PolygonInstance(def_index, param, windings))

    def add_exclusion(
        self, kind: str, west: float, south: float, east: float, north: float
    ) -> None:
        self.exclusions.append(
            (EXCLUSION_PROPERTIES[kind], f"{west:.6f}/{south:.6f}/{east:.6f}/{north:.6f}")
        )

    def _require_inside(self, lon: float, lat: float) -> None:
        if not self.tile.contains(lon, lat):
            raise ValueError(f"point {lon:.6f},{lat:.6f} outside tile {self.tile}")

    def to_text(self) -> str:
        out = ["A", f"800 {APP_NAME} {__version__}", "DSF2TEXT", ""]
        out += [
            "PROPERTY sim/planet earth",
            "PROPERTY sim/overlay 1",
            f"PROPERTY sim/creation_agent {APP_NAME} {__version__}",
        ]
        out += [f"PROPERTY {name} {value}" for name, value in self.exclusions]
        # Must be the last properties (DSFTool requirement).
        out += [
            f"PROPERTY sim/west {self.tile.lon}",
            f"PROPERTY sim/east {self.tile.lon + 1}",
            f"PROPERTY sim/north {self.tile.lat + 1}",
            f"PROPERTY sim/south {self.tile.lat}",
            "",
        ]
        out += [f"OBJECT_DEF {p}" for p in self.object_defs]
        out += [f"POLYGON_DEF {p}" for p in self.polygon_defs]
        out.append("")
        for idx, lon, lat, heading in self.objects:
            out.append(f"OBJECT {idx} {lon:.9f} {lat:.9f} {heading:.3f}")
        for poly in self.polygons:
            depth = len(poly.windings[0][0])
            out.append(f"BEGIN_POLYGON {poly.def_index} {poly.param} {depth}")
            for winding in poly.windings:
                out.append("BEGIN_WINDING")
                for p in winding:
                    out.append("POLYGON_POINT " + " ".join(f"{c:.9f}" for c in p))
                out.append("END_WINDING")
            out.append("END_POLYGON")
        return "\n".join(out) + "\n"


def _check_resource_path(path: str) -> str:
    if "\\" in path or path.startswith("/") or ".." in path.split("/") or not path.isascii():
        raise ValueError(f"resource path must be relative, ASCII and use '/': {path}")
    return path
