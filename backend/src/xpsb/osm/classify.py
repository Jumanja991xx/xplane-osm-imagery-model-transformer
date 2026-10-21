"""Turns Overpass elements into scene items (objects and surfaces).

Rules are deliberately simple and visible; the AI analysis refines the result
and the user has the last word in the editor.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable

import numpy as np
import shapely
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, mapping, shape
from shapely.ops import linemerge, polygonize, transform, unary_union
from shapely.strtree import STRtree

from xpsb.catalog import ObjectCategory, SurfaceType
from xpsb.geo import METERS_PER_DEG_LAT, meters_per_deg_lon
from xpsb.osm import bridges
from xpsb.scene import BuildingPart, ItemAttrs, SceneItem

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
    if el["type"] == "relation" and el.get("tags", {}).get("type") == "multipolygon":
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

GREEN_LANDUSE = ("grass", "recreation_ground", "village_green", "meadow", "flowerbed", "cemetery")
GREEN_LEISURE = ("park", "garden", "playground", "dog_park", "golf_course", "common")


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
    min_height = parse_length_m(tags.get("min_height"))
    if min_height is None and tags.get("building:min_level"):
        try:
            min_height = round(float(tags["building:min_level"]) * 3.0, 1)
        except ValueError:
            min_height = None
    return ItemAttrs(
        height_m=height,
        min_height_m=min_height if min_height and (height is None or min_height < height) else None,
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


# ---- statues and monuments ----------------------------------------------------------

MONUMENT_MAX_AREA_M2 = 400.0  # larger memorial areas are squares/parks, not a statue
_MEMORIAL_KINDS = {
    "statue": "statue",
    "bust": "bust",
    "obelisk": "obelisk",
    "column": "column",
    "stele": "stone",
    "stone": "stone",
    "war_memorial": "statue",
    "sculpture": "statue",
}
_RIDER_WORDS = ("reiter", "equestrian", "lovas", "equestre", "jezdec", "ruiter", "ryttar")


def monument_kind(tags: dict[str, str]) -> str | None:
    """statue | rider | bust | obelisk | column | stone, or None (plaques, graves,
    memorial areas and everything that is not a free-standing monument)."""
    name = " ".join(tags.get(k, "") for k in ("name", "name:en", "name:de", "description"))
    rider = any(w in name.lower() for w in _RIDER_WORDS)
    if tags.get("man_made") == "obelisk":
        return "obelisk"
    if tags.get("tourism") == "artwork" and tags.get("artwork_type") in ("statue", "sculpture"):
        return "rider" if rider else "statue"
    if tags.get("tourism") == "artwork" and tags.get("artwork_type") == "bust":
        return "bust"
    h = tags.get("historic")
    if h == "memorial":
        kind = _MEMORIAL_KINDS.get(tags.get("memorial", ""))
        if kind == "statue" and rider:
            return "rider"
        return kind
    if h == "monument":
        return "rider" if rider else "statue"
    return None


def _area_m2(geom) -> float:
    lat = geom.centroid.y
    return geom.area * METERS_PER_DEG_LAT * meters_per_deg_lon(lat)


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
    if monument_kind(tags) and (not is_area or _area_m2(geom) <= MONUMENT_MAX_AREA_M2):
        return "object", ObjectCategory.MONUMENTS
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
        if tags.get("amenity") in ("parking", "parking_space"):
            return "surface", _surface_from_tag(tags, SurfaceType.ASPHALT)
        if tags.get("area:highway"):
            return "surface", _surface_from_tag(tags, SurfaceType.ASPHALT)
        # Squares, pedestrian zones, market places: paved.
        if (
            tags.get("place") == "square"
            or tags.get("amenity") == "marketplace"
            or (tags.get("highway") in ("pedestrian", "footway") and tags.get("area") == "yes")
        ):
            return "surface", _surface_from_tag(tags, SurfaceType.CONCRETE)
        green = tags.get("landuse") in GREEN_LANDUSE or leisure in GREEN_LEISURE
        if green:
            return "surface", _surface_from_tag(tags, SurfaceType.GRASS)
        if tags.get("landuse") in ("construction", "brownfield") or tags.get("natural") in (
            "sand",
            "beach",
        ):
            return "surface", _surface_from_tag(tags, SurfaceType.DIRT)
        # Any other area with a known surface (closed highway ways are lines, not areas).
        if tags.get("surface") in SURFACE_TAG_MAP and (
            tags.get("area") == "yes" or not tags.get("highway")
        ):
            return "surface", SURFACE_TAG_MAP[tags["surface"]]
    if tags.get("leisure") == "track" and geom.geom_type == "LineString":
        default = SurfaceType.TARTAN if tags.get("sport") == "athletics" else SurfaceType.DIRT
        return "surface", _surface_from_tag(tags, default)
    return None


# ---- sites: named areas around buildings (stadium grounds, castle grounds) -----------

# Any named area that is not a building itself can be a site (grounds of a
# stadium, castle, university, hospital, park, zoo, fair ...). Order = priority.
SITE_KEYS = (
    "leisure",
    "historic",
    "amenity",
    "tourism",
    "aeroway",
    "man_made",
    "office",
    "healthcare",
    "military",
    "landuse",
    "shop",
    "power",
    "railway",
    "place",
)
SITE_EXCLUDE = {
    "pitch",
    "track",
    "nature_reserve",
    "forest",
    "farmland",
    "meadow",
    "orchard",
    "vineyard",
    "district",
    "grass",
    "yes",
    "residential",
    "commercial",
    "retail",
    "industrial",
}
# What a building inherits from its site when it has nothing of its own.
SITE_INHERIT = (
    "wikidata",
    "wikipedia",
    "name:en",
    "name:de",
    "capacity",
    "sport",
    "architect",
    "start_date",
    "historic",
    "tourism",
    "operator",
    "description",
)
MAIN_SHARE = 0.12  # the largest building counts as "the" building of the site above this share


def site_kind(tags: dict[str, str]) -> str | None:
    if tags.get("building") or tags.get("building:part") or tags.get("boundary"):
        return None
    if not (tags.get("name") or tags.get("wikidata")):
        return None
    for key in SITE_KEYS:
        value = tags.get(key)
        if value and value not in SITE_EXCLUDE:
            return value
    return None


class _Site:
    def __init__(self, ref: str, tags: dict[str, str], geom) -> None:
        self.ref, self.tags, self.geom, self.kind = ref, tags, geom, site_kind(tags)
        self.pitches: list = []  # pitches and tracks inside (stadium bowls go around them)


def _local_frame(geom):
    """Vectorised lon/lat <-> local metres around the centre of `geom`."""
    c = geom.centroid
    mx = meters_per_deg_lon(c.y)
    scale = np.array([mx, METERS_PER_DEG_LAT])
    origin = np.array([c.x, c.y])
    return (
        lambda g: shapely.transform(g, lambda a: (a - origin) * scale),
        lambda g: shapely.transform(g, lambda a: a / scale + origin),
    )


def _area_m2(geom) -> float:
    fwd, _ = _local_frame(geom)
    return fwd(geom).area


def _capacity(tags: dict[str, str]) -> int | None:
    try:
        return int(float(tags.get("capacity", "").replace(" ", "").replace(",", "")))
    except ValueError:
        return None


def _stadium_outline(site: _Site):
    """Rough outline of the stands when OSM has only the stadium grounds: a ring of
    stands around the pitch (depth from the capacity), cut to the grounds."""
    fwd, back = _local_frame(site.geom)
    ground = fwd(site.geom)
    cap = _capacity(site.tags)
    if site.pitches:
        field = max((fwd(p) for p in site.pitches), key=lambda g: g.area)
        inner = field.minimum_rotated_rectangle.buffer(4.0, join_style="mitre")
        depth = min(70.0, max(14.0, 0.3 * math.sqrt(cap))) if cap else 22.0
        outline = inner.buffer(depth, quad_segs=4).intersection(ground)
    elif ground.area <= 60_000:
        outline = ground
    else:
        return None
    if outline.geom_type == "MultiPolygon":
        outline = max(outline.geoms, key=lambda g: g.area)
    if outline.is_empty or outline.geom_type != "Polygon" or outline.area < 400:
        return None
    outline = Polygon(outline.exterior).simplify(1.0)  # the script builds inwards
    return back(outline)


def apply_sites(
    items: list[SceneItem], sites: list[_Site], aoi: Polygon, wanted_objects: set[str]
) -> list[SceneItem]:
    """Buildings inside a named site learn what they belong to; the main building
    takes over name and Wikidata of the site. A stadium that OSM knows only as
    grounds (leisure=stadium without a building) gets an outline from the grounds."""
    buildings = [
        it
        for it in items
        if it.kind == "object" and str(it.category) in ("buildings", "grandstands")
    ]
    out = list(items)
    points = [shape(it.geometry).representative_point() for it in buildings]
    tree = STRtree(points) if points else None
    # Small sites first: a stadium inside a sports park belongs to the stadium.
    for site in sorted(sites, key=lambda s: s.geom.area):
        hits = tree.query(site.geom, predicate="contains") if tree is not None else []
        inside = [buildings[int(i)] for i in sorted(hits)]
        for it in inside:
            if site.tags.get("name"):
                it.tags.setdefault("site:name", site.tags["name"])
            it.tags.setdefault("site:type", site.kind or "")
        site_area = _area_m2(site.geom)
        field = max((_area_m2(p) for p in site.pitches), default=0.0)
        areas = {it.id: _area_m2(shape(it.geometry)) for it in inside}
        main = max(
            (it for it in inside if str(it.category) == "buildings"),
            key=lambda it: areas[it.id],
            default=None,
        )
        main_ok = main is not None and areas[main.id] >= MAIN_SHARE * site_area
        if main_ok and site.kind == "stadium":  # a clubhouse is not the stadium
            main_ok = areas[main.id] >= max(1500.0, 0.5 * field)
        if main is not None and main_ok and main.tags.get("site:main") != "yes":
            main.tags["site:main"] = "yes"
            for key in SITE_INHERIT:
                if key in site.tags:
                    main.tags.setdefault(key, site.tags[key])
            if not main.name and site.tags.get("name"):
                main.name = site.tags["name"]
        if site.kind != "stadium" or main_ok or "buildings" not in wanted_objects:
            continue
        stands = sum(areas[it.id] for it in inside if str(it.category) == "grandstands")
        if field and stands >= 0.5 * field:
            continue  # mapped as single grandstands: those are built
        outline = _stadium_outline(site)
        if outline is None or not aoi.contains(outline.representative_point()):
            continue
        tags = {k: v for k, v in site.tags.items() if k in KEEP_TAGS}
        tags.update(
            {
                "building": "stadium",
                "site:main": "yes",
                "site:type": "stadium",
                "site:derived": "yes",
            }
        )
        out.append(
            SceneItem(
                id=f"{site.ref}/stands",
                kind="object",
                category=ObjectCategory.BUILDINGS,
                geometry=_rounded(mapping(outline)),
                source="osm",
                name=site.tags.get("name"),
                osm_ref=site.ref,
                tags=tags,
                attrs=_attrs(site.tags),
            )
        )
    return out


# ---- building parts (Simple 3D Buildings) -----------------------------------------


def fold_parts(items: list[SceneItem]) -> list[SceneItem]:
    """building:part areas inside a building outline become parts of that object
    (one object per building, its parts give the shape). Parts without an outline
    stay objects of their own."""
    outlines = [
        it
        for it in items
        if it.kind == "object"
        and it.tags.get("building")
        and it.geometry.get("type") in ("Polygon", "MultiPolygon")
    ]
    if not outlines:
        return items
    geoms = [shape(it.geometry) for it in outlines]
    tree = STRtree(geoms)
    out: list[SceneItem] = []
    for it in items:
        is_part = (
            it.kind == "object" and it.tags.get("building:part") and not it.tags.get("building")
        )
        if not is_part or it.geometry.get("type") != "Polygon":
            out.append(it)
            continue
        geom = shape(it.geometry)
        owner = None
        for idx in tree.query(geom):
            g = geoms[int(idx)]
            if g.contains(geom.representative_point()) or (
                g.intersection(geom).area >= 0.6 * geom.area
            ):
                owner = outlines[int(idx)]
                break
        if owner is None:
            out.append(it)
            continue
        owner.parts.append(
            BuildingPart(
                geometry=it.geometry,
                osm_ref=it.osm_ref,
                height_m=it.attrs.height_m,
                min_height_m=it.attrs.min_height_m,
                roof_shape=it.attrs.roof_shape,
                wall_color=it.attrs.wall_color,
                roof_color=it.attrs.roof_color,
                part=it.tags.get("building:part"),
            )
        )
    return out


def _mostly_inside(geom, aoi: Polygon) -> bool:
    """Buildings are never cut in half: kept if their centre is inside the area or
    at least half of them is (big buildings at the edge of a hand-drawn area)."""
    if aoi.contains(geom.representative_point()):
        return True
    if geom.geom_type in ("Polygon", "MultiPolygon") and geom.area > 0:
        return geom.intersection(aoi).area >= 0.5 * geom.area
    return False


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
    # identity and look: used by the research and the AI architect
    "wikidata",
    "wikipedia",
    "name:en",
    "name:de",
    "description",
    "operator",
    "tourism",
    "historic",
    "start_date",
    "architect",
    "capacity",
    "material",
    "roof:material",
    "roof:height",
    "min_height",
    "building:min_level",
)


def classify(
    elements: Iterable[dict], aoi: Polygon, wanted_objects: set[str], wanted_surfaces: set[str]
) -> list[SceneItem]:
    elements = list(elements)
    items: list[SceneItem] = []
    seen: set[str] = set()
    sites: list[_Site] = []
    site_refs: set[str] = set()
    fields: list = []  # pitches and tracks (for stadium outlines)
    # type=building relations: their name/Wikidata belong to the outline way.
    outline_tags: dict[str, dict[str, str]] = {}
    for el in elements:
        rtags = el.get("tags") or {}
        if el["type"] == "relation" and rtags.get("type") == "building":
            for m in el.get("members", []):
                if m.get("role") == "outline" and m.get("type") == "way":
                    extra = {k: v for k, v in rtags.items() if k != "type"}
                    outline_tags[f"way/{m['ref']}"] = extra
    for el in elements:
        tags = el.get("tags") or {}
        ref = f"{el['type']}/{el['id']}"
        if not tags or ref in seen:
            continue
        if ref in outline_tags:
            tags = {**outline_tags[ref], **tags}
        geom = element_geometry(el)
        if geom is None or geom.is_empty:
            continue
        if not geom.is_valid:
            geom = geom.buffer(0) if geom.geom_type != "LineString" else geom
        if geom.geom_type in ("Polygon", "MultiPolygon"):
            if site_kind(tags) and ref not in site_refs:  # may come twice from Overpass
                site_refs.add(ref)
                sites.append(_Site(ref, tags, geom))
            if tags.get("leisure") in ("pitch", "track"):
                fields.append(geom)
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
            if not _mostly_inside(geom, aoi):
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
    for site in sites:
        site.pitches = [f for f in fields if site.geom.contains(f.representative_point())]
    if ObjectCategory.BRIDGES in wanted_objects:
        items += bridges.find(elements, aoi)
    items = fold_parts(items)
    items = apply_sites(items, sites, aoi, wanted_objects)
    # Surfaces first (drawn underneath), larger first; then objects.
    items.sort(key=lambda it: (it.kind != "surface", -shape(it.geometry).area))
    return items


def _rounded(geo: dict) -> dict:
    def r(c):
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            return [round(c[0], 7), round(c[1], 7)]
        return [r(x) for x in c]

    return {"type": geo["type"], "coordinates": r(geo["coordinates"])}
