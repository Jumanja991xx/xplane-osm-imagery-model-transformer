"""Official 3D buildings (LoD2, CityGML 1.0/2.0) - the real roof shapes and heights.

Bavaria publishes all ~9.8 million buildings as open data (CC BY 4.0) in tiles of
2 x 2 km, ETRS89 / UTM zone 32 (EPSG:25832), heights above sea level. The tile
list is a metalink file; we download only the tiles the drawn area touches.

The parser reads elements by their local names, so CityGML 1.0 and 2.0 files
(and other German states using the AdV profile) work alike.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import Polygon
from shapely.ops import unary_union

from xpsb import __version__
from xpsb.city import utm
from xpsb.city.model import CityBuilding

METALINK_BAYERN = "https://geodaten.bayern.de/odd/a/lod2/citygml/meta/metalink/09.meta4"
ATTRIBUTION_BAYERN = "3D-Gebäudemodelle LoD2 © Bayerische Vermessungsverwaltung (CC BY 4.0)"
USER_AGENT = f"XPlaneAISceneryBuilder/{__version__}"
METALINK_MAX_AGE_S = 30 * 24 * 3600
ZONE_BAYERN = 32

# Bavaria as a rough box (lon/lat): where the official data is tried first.
BAYERN_BOX = (8.95, 47.25, 13.85, 50.6)

# AdV roof type codes (bldg:roofType)
ROOF_TYPES = {
    "1000": "flat",
    "2100": "shed",
    "2200": "shed",
    "3100": "gabled",
    "3200": "hipped",
    "3300": "hipped",  # half-hipped
    "3400": "mansard",
    "3500": "pyramid",
    "3600": "pyramid",  # cone
    "3700": "dome",
    "3800": "sawtooth",
    "3900": "barrel",
    "4000": "pyramid",  # tower roof
    "5000": "mixed",
    "9999": "mixed",
}


class Lod2Error(RuntimeError):
    pass


def covers(lon: float, lat: float) -> bool:
    w, s, e, n = BAYERN_BOX
    return w <= lon <= e and s <= lat <= n


# ---- tiles --------------------------------------------------------------------------


@dataclass(frozen=True)
class TileRef:
    name: str
    url: str
    east_km: int
    north_km: int


_TILE_NAME = re.compile(r"(\d{3,4})_(\d{4})")


def parse_metalink(data: bytes) -> list[TileRef]:
    root = ET.fromstring(data)
    out = []
    for f in root.iter():
        if _local(f.tag) != "file":
            continue
        name = f.get("name", "")
        m = _TILE_NAME.search(name)
        url = next((u.text or "" for u in f if _local(u.tag) == "url"), "")
        if m and url.startswith("https://"):
            out.append(TileRef(name, url.strip(), int(m.group(1)), int(m.group(2))))
    return out


def tiles_for(tiles: list[TileRef], bounds_utm: tuple[float, float, float, float]) -> list[TileRef]:
    """Tiles whose square touches the area (west, south, east, north in metres)."""
    if not tiles:
        return []
    easts = sorted({t.east_km for t in tiles})
    steps = [b - a for a, b in zip(easts, easts[1:], strict=False) if b > a]
    size_km = min(steps) if steps else 2
    w, s, e, n = bounds_utm
    return [
        t
        for t in tiles
        if t.east_km * 1000 < e
        and (t.east_km + size_km) * 1000 > w
        and t.north_km * 1000 < n
        and (t.north_km + size_km) * 1000 > s
    ]


def _get(url: str, timeout: float = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise Lod2Error(f"{url}: HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise Lod2Error(f"{url}: {exc}") from exc


def _fixture_dir() -> Path | None:
    value = os.environ.get("XPSB_FIXTURES_DIR")
    return Path(value) if value else None


def download(
    ring: list[tuple[float, float]],
    cache_dir: Path,
    progress: Callable[[int, int], None] | None = None,
) -> list[Path]:
    """The CityGML files covering the area (cached), Bavaria only."""
    fixtures = _fixture_dir()
    if fixtures is not None:
        return sorted(fixtures.glob("*.gml"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    meta = cache_dir / "bayern.meta4"
    if not meta.exists() or time.time() - meta.stat().st_mtime > METALINK_MAX_AGE_S:
        meta.write_bytes(_get(METALINK_BAYERN))
    tiles = parse_metalink(meta.read_bytes())
    pts = [utm.from_lonlat(lon, lat, ZONE_BAYERN) for lon, lat in ring]
    box = (
        min(p[0] for p in pts),
        min(p[1] for p in pts),
        max(p[0] for p in pts),
        max(p[1] for p in pts),
    )
    todo = tiles_for(tiles, box)
    out = []
    for n, t in enumerate(todo):
        if progress:
            progress(n, len(todo))
        key = hashlib.sha256(t.url.encode()).hexdigest()[:12]
        path = cache_dir / f"{t.east_km}_{t.north_km}_{key}.gml"
        if not path.exists():
            tmp = path.with_suffix(".part")
            tmp.write_bytes(_get(t.url, timeout=600))
            tmp.replace(path)
        out.append(path)
    if progress:
        progress(len(todo), len(todo))
    return out


# ---- CityGML ------------------------------------------------------------------------


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(el: ET.Element, name: str) -> Iterator[ET.Element]:
    return (c for c in el if _local(c.tag) == name)


def _text(el: ET.Element, name: str) -> str | None:
    c = next(_children(el, name), None)
    return c.text.strip() if c is not None and c.text else None


def _ring(lin: ET.Element) -> list[tuple[float, float, float]]:
    pos_list = next((c for c in lin.iter() if _local(c.tag) == "posList"), None)
    if pos_list is not None and pos_list.text:
        dim = int(pos_list.get("srsDimension", "3"))
        v = [float(x) for x in pos_list.text.split()]
        pts = [tuple(v[i : i + 3]) for i in range(0, len(v) - dim + 1, dim)]
    else:
        pts = [
            tuple(float(x) for x in p.text.split()[:3])
            for p in lin.iter()
            if _local(p.tag) == "pos" and p.text
        ]
    pts = [p for p in pts if len(p) == 3]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return pts  # type: ignore[return-value]


def _polygons(surface: ET.Element) -> list[list[tuple[float, float, float]]]:
    out = []
    for poly in surface.iter():
        if _local(poly.tag) != "Polygon":
            continue
        ext = next(_children(poly, "exterior"), None)
        if ext is None:
            continue
        ring = _ring(ext)
        if len(ring) >= 3:
            out.append(ring)
    return out


def _use(function: str | None) -> str:
    if not function:
        return "residential"
    code = function.rsplit("_", 1)[-1]
    if not code.isdigit():
        return "residential"
    c = int(code)
    if 1000 <= c < 2000:
        return "residential"
    if 2460 <= c <= 2465 or c in (1610, 2463):
        return "garage"
    if 2700 <= c < 2800:
        return "farm"
    if 2100 <= c < 2200 or 2500 <= c < 2600:
        return "industrial"
    if 2000 <= c < 3000:
        return "commercial"
    if 3040 <= c < 3050:
        return "religious"
    if 3000 <= c < 4000:
        return "public"
    return "residential"


def _part(
    el: ET.Element, ident: str, inherited: dict[str, str | None], zone: int
) -> CityBuilding | None:
    walls: list = []
    roofs: list = []
    ground: list = []
    for bounded in _children(el, "boundedBy"):
        for surf in bounded:
            kind = _local(surf.tag)
            polys = _polygons(surf)
            if kind == "RoofSurface":
                roofs += polys
            elif kind in ("WallSurface", "ClosureSurface"):
                walls += polys
            elif kind == "GroundSurface":
                ground += polys
    if not roofs:
        return None
    if ground:
        shapes = [Polygon([(x, y) for x, y, _ in g]) for g in ground]
    else:  # outline from above: the roof faces projected down
        shapes = [Polygon([(x, y) for x, y, _ in r]) for r in roofs]
    shapes = [s.buffer(0) for s in shapes if len(s.exterior.coords) >= 4]
    outline = unary_union([s for s in shapes if s.area > 0.01])
    if outline.is_empty:
        return None
    if outline.geom_type == "MultiPolygon":
        outline = max(outline.geoms, key=lambda g: g.area)
    zs_ground = [p[2] for g in ground for p in g] or [p[2] for w in walls for p in w]
    ground_z = min(zs_ground) if zs_ground else min(p[2] for r in roofs for p in r)
    roof_z = [p[2] for r in roofs for p in r]

    def lonlat(ring) -> list[tuple[float, float]]:
        return [utm.to_lonlat(x, y, zone) for x, y in list(ring.coords)[:-1]]

    footprint = [lonlat(outline.exterior)] + [lonlat(r) for r in outline.interiors]
    roof_type = ROOF_TYPES.get(
        (_text(el, "roofType") or inherited.get("roofType") or "").strip(), "mixed"
    )
    levels = _text(el, "storeysAboveGround") or inherited.get("storeysAboveGround")
    return CityBuilding(
        id=ident,
        source="lod2",
        footprint=footprint,
        ground_z=ground_z,
        eave_m=max(0.0, min(roof_z) - ground_z),
        ridge_m=max(0.0, max(roof_z) - ground_z),
        roof_type=roof_type,
        use=_use(_text(el, "function") or inherited.get("function")),
        levels=int(float(levels)) if levels and levels.replace(".", "").isdigit() else None,
        walls=walls,
        roofs=roofs,
        zone=zone,
    )


def parse(path: Path, zone: int = ZONE_BAYERN) -> Iterator[CityBuilding]:
    """Buildings (and building parts, each on its own) of a CityGML file."""
    for _event, el in ET.iterparse(path, events=("end",)):
        if _local(el.tag) != "Building":
            continue
        ident = el.get("{http://www.opengis.net/gml}id") or el.get("id") or "b"
        inherited = {k: _text(el, k) for k in ("roofType", "function", "storeysAboveGround")}
        parts = [
            p for c in _children(el, "consistsOfBuildingPart") for p in _children(c, "BuildingPart")
        ]
        whole = _part(el, ident, inherited, zone)
        if whole is not None:
            yield whole
        for n, p in enumerate(parts, start=1):
            b = _part(p, f"{ident}/{n}", inherited, zone)
            if b is not None:
                yield b
        el.clear()
