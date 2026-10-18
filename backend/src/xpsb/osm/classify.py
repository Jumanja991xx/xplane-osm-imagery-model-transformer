"""Turns Overpass elements into scene items (objects and surfaces).

Rules are deliberately simple and visible; the AI analysis refines the result
and the user has the last word in the editor.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable

from shapely.geometry import LineString, MultiPolygon, Point, Polygon, mapping, shape
from shapely.ops import linemerge, polygonize, transform, unary_union

from xpsb.catalog import ObjectCategory, SurfaceType
from xpsb.geo import METERS_PER_DEG_LAT, meters_per_deg_lon
from xpsb.scene import ItemAttrs, SceneItem

DEFAULT_RACEWAY_WIDTH_M = 12.0
DEFAULT_TRACK_WIDTH_M = 8.0

SURFACE_TAG_MAP = {
    "asphalt": SurfaceType.ASPHALT,
    "paved": SurfaceType.ASPHALT,
    "concrete": SurfaceType.CONCRETE,
    "concrete:plates": SurfaceType.CONCRETE,
    "paving_stones": SurfaceType.CONCRETE,
    "gravel": SurfaceType.GRAVEL,
    "fine_gravel": SurfaceType.GRAVEL,
    "pebblestone": SurfaceType.GRAVEL,
    "dirt": SurfaceType.DIRT,
    "earth": SurfaceType.DIRT,
    "ground": SurfaceType.DIRT,
    "sand": SurfaceType.DIRT,
    "compacted": SurfaceType.DIRT,
    "clay": SurfaceType.DIRT,
    "cinder": SurfaceType.DIRT,
    "grass": SurfaceType.GRASS,
    "artificial_turf": SurfaceType.GRASS,
    "tartan": SurfaceType.TARTAN,
}


# ---- geometry -----------------------------------------------------------------


def _coords(geom: list[dict]) -> list[tuple[float, float]]:
    return [(p["lon"], p["lat"]) for p in geom]


def _assemble_multipolygon(members: list[dict]) -> Polygon | MultiPolygon | None:
    outer = [
        LineString(_coords(m["geometry"]))
        for m in members
        if m.get("role") in ("outer", "") and m.get("geometry")
    ]
    inner = [
        LineString(_coords(m["geometry"]))
        for m in members
        if m.get("role") == "inner" and m.get("geometry")
    ]
    if not outer:
        return None
    outers = list(polygonize(linemerge(outer)))
    if not outers:
        return None
    area = unary_union(outers)
    holes = list(polygonize(linemerge(inner))) if inner else []
    if holes:
        area = area.difference(unary_union(holes))
    return area if not area.is_empty else None


def element_geometry(el: dict):
    if el["type"] == "node":
        return Point(el["lon"], el["lat"])
    if el["type"] == "way":
        pts = _coords(el.get("geometry") or [])
        if len(pts) < 2:
            return None
        if len(pts) >= 4 and pts[0] == pts[-1]:
            return Polygon(pts)
        return LineString(pts)
    if el["type"] == "relation" and el.get("tags", {}).get("type") in ("multipolygon", "building"):
        return _assemble_multipolygon(el.get("members", []))
    return None


def _to_local(lon0: float, lat0: float):
    mx = meters_per_deg_lon(lat0)
    return (
        lambda x, y, z=None: ((x - lon0) * mx, (y - lat0) * METERS_PER_DEG_LAT),
        lambda x, y, z=None: (x / mx + lon0, y / METERS_PER_DEG_LAT + lat0),
    )


def buffer_line_m(line: LineString, width_m: float) -> Polygon:
    c = line.centroid
    fwd, back = _to_local(c.x, c.y)
    return transform(back, transform(fwd, line).buffer(width_m / 2, cap_style="flat"))


# ---- tags ---------------------------------------------------------------------


_NUMBER = re.compile(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*(m|ft|')?\s*$")


def parse_length_m(value: str | None) -> float | None:
    if not value:
        return None
    m = _NUMBER.match(value)
    if not m:
        return None
    number = float(m.group(1).replace(",", "."))
    if m.group(2) in ("ft", "'"):
        number *= 0.3048
    return number if math.isfinite(number) else None


def _attrs(tags: dict[str, str]) -> ItemAttrs:
    levels = tags.get("building:levels")
    try:
        levels_n = int(float(levels)) if levels else None
    except ValueError:
        levels_n = None
    height = parse_length_m(tags.get("height"))
    if height is None and levels_n:
        height = round(levels_n * 3.0, 1)
    return ItemAttrs(
        height_m=height,
        levels=levels_n,
        roof_shape=tags.get("roof:shape"),
        roof_color=tags.get("roof:colour"),
        wall_color=tags.get("building:colour"),
        material=tags.get("building:material") or tags.get("material") or tags.get("surface"),
        covered=True if tags.get("covered") == "yes" else None,
        width_m=parse_length_m(tags.get("width")),
    )


def _surface_from_tag(tags: dict[str, str], default: SurfaceType) -> SurfaceType:
    return SURFACE_TAG_MAP.get(tags.get("surface", ""), default)


def classify_tags(tags: dict[str, str], geom) -> tuple[str, ObjectCategory | SurfaceType] | None:
    """(kind, category) or None if the element is not something we build."""
    is_area = geom.geom_type in ("Polygon", "MultiPolygon")
    b = tags.get("building") or tags.get("building:part")
    if b and b != "no" and is_area:
        if b == "grandstand" or tags.get("leisure") == "grandstand":
            return "object", ObjectCategory.GRANDSTANDS
        if b in ("roof", "carport", "canopy") or tags.get("covered") == "yes" and b == "yes":
            return "object", ObjectCategory.CANOPIES
        if b == "tent":
            return "object", ObjectCategory.CANOPIES
        if b == "construction" or tags.get("construction") == "scaffolding":
            return "object", ObjectCategory.SCAFFOLDING
        return "object", ObjectCategory.BUILDINGS
    if tags.get("barrier") and geom.geom_type in ("LineString", "Polygon"):
        return "object", ObjectCategory.BARRIERS
    if (
        tags.get("man_made") in ("mast", "tower", "flagpole")
        or tags.get("tower:type") == "lighting"
    ):
        return "object", ObjectCategory.MASTS

    if tags.get("highway") == "raceway":
        return "surface", _surface_from_tag(tags, SurfaceType.ASPHALT)
    if is_area:
        leisure = tags.get("leisure")
        if leisure == "pitch":
            return "surface", _surface_from_tag(tags, SurfaceType.GRASS)
        if leisure == "track":
            default = SurfaceType.TARTAN if tags.get("sport") == "athletics" else SurfaceType.DIRT
            if tags.get("sport") in ("motor", "karting", "motocross"):
                default = SurfaceType.ASPHALT
            return "surface", _surface_from_tag(tags, default)
        if tags.get("landuse") in ("grass", "recreation_ground"):
            return "surface", SurfaceType.GRASS
        if tags.get("amenity") == "parking":
            return "surface", _surface_from_tag(tags, SurfaceType.ASPHALT)
        if tags.get("area:highway"):
            return "surface", _surface_from_tag(tags, SurfaceType.ASPHALT)
        if tags.get("surface") in SURFACE_TAG_MAP and tags.get("area") == "yes":
            return "surface", SURFACE_TAG_MAP[tags["surface"]]
    if tags.get("leisure") == "track" and geom.geom_type == "LineString":
        default = SurfaceType.TARTAN if tags.get("sport") == "athletics" else SurfaceType.DIRT
        return "surface", _surface_from_tag(tags, default)
    return None


# ---- main ---------------------------------------------------------------------


KEEP_TAGS = (
    "name",
    "building",
    "building:part",
    "building:levels",
    "height",
    "roof:shape",
    "roof:colour",
    "building:colour",
    "building:material",
    "leisure",
    "sport",
    "surface",
    "barrier",
    "man_made",
    "tower:type",
    "highway",
    "width",
    "amenity",
    "landuse",
    "covered",
    "area:highway",
)


def classify(
    elements: Iterable[dict], aoi: Polygon, wanted_objects: set[str], wanted_surfaces: set[str]
) -> list[SceneItem]:
    items: list[SceneItem] = []
    seen: set[str] = set()
    for el in elements:
        tags = el.get("tags") or {}
        ref = f"{el['type']}/{el['id']}"
        if not tags or ref in seen:
            continue
        geom = element_geometry(el)
        if geom is None or geom.is_empty:
            continue
        if not geom.is_valid:
            geom = geom.buffer(0) if geom.geom_type != "LineString" else geom
        result = classify_tags(tags, geom)
        if result is None:
            continue
        kind, category = result
        if (kind == "object" and category not in wanted_objects) or (
            kind == "surface" and category not in wanted_surfaces
        ):
            continue

        attrs = _attrs(tags)
        if kind == "surface" and geom.geom_type == "LineString":
            default_w = (
                DEFAULT_RACEWAY_WIDTH_M
                if tags.get("highway") == "raceway"
                else DEFAULT_TRACK_WIDTH_M
            )
            attrs.width_m = attrs.width_m or default_w
            geom = buffer_line_m(geom, attrs.width_m)

        if kind == "object":
            # Buildings are never cut in half: keep them if their centre is inside.
            if not aoi.contains(geom.representative_point()):
                continue
        else:
            geom = geom.intersection(aoi)
            if geom.is_empty or geom.area <= 0:
                continue

        for i, part in enumerate(getattr(geom, "geoms", [geom])):
            item_id = ref if i == 0 else f"{ref}#{i}"
            items.append(
                SceneItem(
                    id=item_id,
                    kind=kind,
                    category=category,
                    geometry=_rounded(mapping(part)),
                    source="osm",
                    name=tags.get("name"),
                    osm_ref=ref,
                    tags={k: v for k, v in tags.items() if k in KEEP_TAGS},
                    attrs=attrs,
                )
            )
        seen.add(ref)
    # Surfaces first (drawn underneath), larger first; then objects.
    items.sort(key=lambda it: (it.kind != "surface", -shape(it.geometry).area))
    return items


def _rounded(geo: dict) -> dict:
    def r(c):
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            return [round(c[0], 7), round(c[1], 7)]
        return [r(x) for x in c]

    return {"type": geo["type"], "coordinates": r(geo["coordinates"])}
