"""What other sceneries place where our objects stand.

Exclusion zones remove objects of packs lower in scenery_packs.ini only if the
object's origin is inside the zone (facades, forests and polygons by their
points). A rectangle around our model can miss the old model of the same
landmark: another author put its origin a few metres aside (the Friedensengel).
So the DSFs of the other packs for the tile are read; every object, facade,
forest and polygon of theirs that overlaps one of ours gets a zone that holds
its origin and its whole footprint.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry

from xpsb.geo import METERS_PER_DEG_LAT, meters_per_deg_lon
from xpsb.xplane import dsftool
from xpsb.xplane.dsf_text import Tile
from xpsb.xplane.neighbors import for_tile

LIBRARY_RADIUS_M = 6.0  # size assumed for library objects (no file to measure)
KINDS = {".obj": "objects", ".agp": "objects", ".fac": "facades", ".for": "forests"}
KINDS.update({".pol": "polygons", ".lin": "polygons", ".str": "objects"})


@dataclass
class Placed:
    pack: str
    path: str  # the definition (OBJ, facade ...) as the DSF names it
    kind: str  # exclusion kind: objects | facades | forests | polygons
    lon: float
    lat: float
    footprint: list[list[float]]  # lon/lat outline


def _obj_extent(file: Path) -> tuple[float, float, float, float] | None:
    """x (east) and z (south) extent of an OBJ8 in metres, from its vertices."""
    try:
        text = file.read_text(encoding="latin-1", errors="replace")
    except OSError:
        return None
    xs, zs = [], []
    for line in text.splitlines():
        if line.startswith("VT ") or line.startswith("VERTEX "):
            parts = line.split()
            try:
                xs.append(float(parts[1]))
                zs.append(float(parts[3]))
            except (IndexError, ValueError):
                continue
    if not xs:
        return None
    return min(xs), min(zs), max(xs), max(zs)


def _object_outline(lon, lat, heading, extent) -> list[list[float]]:
    mx, my = meters_per_deg_lon(lat), METERS_PER_DEG_LAT
    if extent is None:
        r = LIBRARY_RADIUS_M
        extent = (-r, -r, r, r)
    x0, z0, x1, z1 = extent
    h = math.radians(heading)
    out = []
    for x, z in ((x0, z0), (x1, z0), (x1, z1), (x0, z1)):
        north = -z  # OBJ: +z is south
        e = x * math.cos(h) + north * math.sin(h)
        n = -x * math.sin(h) + north * math.cos(h)
        out.append([lon + e / mx, lat + n / my])
    return out


def parse(text_lines, pack: str, pack_dir: Path, bbox) -> list[Placed]:
    """Objects and polygons of one DSF text inside bbox (west, south, east, north)."""
    w, s, e, n = bbox
    obj_defs: list[str] = []
    pol_defs: list[str] = []
    extents: dict[str, tuple | None] = {}
    out: list[Placed] = []
    poly: list[list[float]] | None = None
    poly_def = ""
    for line in text_lines:
        if line.startswith("OBJECT_DEF "):
            obj_defs.append(line[len("OBJECT_DEF ") :].strip())
        elif line.startswith("POLYGON_DEF "):
            pol_defs.append(line[len("POLYGON_DEF ") :].strip())
        elif line.startswith("OBJECT "):
            parts = line.split()
            try:
                idx, lon, lat = int(parts[1]), float(parts[2]), float(parts[3])
                heading = float(parts[4]) if len(parts) > 4 else 0.0
            except (IndexError, ValueError):
                continue
            if not (w <= lon <= e and s <= lat <= n) or idx >= len(obj_defs):
                continue
            path = obj_defs[idx]
            if path not in extents:
                f = pack_dir / path
                extents[path] = _obj_extent(f) if f.suffix.lower() == ".obj" else None
            out.append(
                Placed(
                    pack,
                    path,
                    "objects",
                    lon,
                    lat,
                    _object_outline(lon, lat, heading, extents[path]),
                )
            )
        elif line.startswith("BEGIN_POLYGON "):
            parts = line.split()
            idx = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else -1
            poly_def = pol_defs[idx] if 0 <= idx < len(pol_defs) else ""
            poly = []
        elif line.startswith("POLYGON_POINT ") and poly is not None:
            parts = line.split()
            try:
                poly.append([float(parts[1]), float(parts[2])])
            except (IndexError, ValueError):
                continue
        elif line.startswith("END_POLYGON") and poly is not None:
            kind = KINDS.get(Path(poly_def).suffix.lower())
            inside = any(w <= p[0] <= e and s <= p[1] <= n for p in poly)
            if kind and kind != "objects" and inside and len(poly) >= 2:
                out.append(Placed(pack, poly_def, kind, poly[0][0], poly[0][1], poly))
            poly = None
    return out


def near(
    xplane_path: str | None,
    dsftool_exe: Path | None,
    tile: Tile,
    bbox,
    cache_dir: Path,
    own: str | None = None,
) -> tuple[list[Placed], list[str]]:
    """What the other enabled packs place around bbox; and the packs that could not
    be read."""
    if not xplane_path or dsftool_exe is None:
        return [], []
    custom = Path(xplane_path) / "Custom Scenery"
    found: list[Placed] = []
    failed: list[str] = []
    for pack in for_tile(xplane_path, tile, own):
        if not pack.enabled:
            continue
        dsf = custom / pack.name / tile.dsf_relative_path
        try:
            stat = dsf.stat()
        except OSError:
            continue
        key = hashlib.sha1(
            f"{dsf}|{stat.st_size}|{stat.st_mtime_ns}|{[round(v, 4) for v in bbox]}".encode()
        ).hexdigest()[:20]
        cached = cache_dir / f"{key}.json"
        if cached.is_file():
            found += [Placed(**p) for p in json.loads(cached.read_text(encoding="utf-8"))]
            continue
        text = dsftool.ascii_work_root() / f"foreign-{key}.txt"
        try:
            dsftool.dsf2text(dsftool_exe, dsf, text)
            with text.open(encoding="utf-8", errors="replace") as f:
                placed = parse(f, pack.name, custom / pack.name, bbox)
        except (dsftool.DsfToolError, OSError):
            failed.append(pack.name)
            continue
        finally:
            text.unlink(missing_ok=True)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps([asdict(p) for p in placed]), encoding="utf-8")
        found += placed
    return found, failed


def overlapping(found: list[Placed], ours: list[BaseGeometry]) -> list[Placed]:
    """Their objects and polygons that overlap one of ours (lon/lat geometries)."""
    out = []
    for p in found:
        shape_ = Polygon(p.footprint) if len(p.footprint) >= 3 else box(p.lon, p.lat, p.lon, p.lat)
        if not shape_.is_valid:
            shape_ = shape_.buffer(0)
        if any(shape_.intersects(g) for g in ours):
            out.append(p)
    return out


def zone(p: Placed, margin_deg: float = 0.00002) -> tuple[float, float, float, float]:
    """A rectangle holding the origin and the whole footprint."""
    xs = [q[0] for q in p.footprint] + [p.lon]
    ys = [q[1] for q in p.footprint] + [p.lat]
    return (min(xs) - margin_deg, min(ys) - margin_deg, max(xs) + margin_deg, max(ys) + margin_deg)
