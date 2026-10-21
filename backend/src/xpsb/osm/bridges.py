"""Bridges from OpenStreetMap.

OSM maps a bridge as the ways that cross it (a road, a railway, a path, tagged
bridge=yes and the layer above the ground) and sometimes its outline
(man_made=bridge, with its name and Wikidata). One bridge item is made per
bridge: its axis (the deck's centre line), width, deck height, how it is carried
(beam, arch, truss, suspension, cable-stayed) and the roads that lead onto it
(the approach ramps follow them down to the ground).
"""

from __future__ import annotations

from collections.abc import Iterable

from shapely.geometry import LineString, Point, Polygon, mapping
from shapely.ops import linemerge, transform, unary_union

from xpsb.catalog import ObjectCategory
from xpsb.geo import METERS_PER_DEG_LAT, meters_per_deg_lon
from xpsb.scene import ItemAttrs, SceneItem

MIN_LENGTH_M = 8.0  # shorter "bridges" are culverts and small crossings
APPROACH_M = 160.0  # how much of each road leading onto a bridge is kept
END_SNAP_M = 1.5
ROAD_WIDTH_M = {
    "motorway": 12.0,
    "trunk": 11.0,
    "primary": 10.0,
    "secondary": 9.0,
    "tertiary": 8.0,
    "residential": 7.0,
    "unclassified": 7.0,
    "service": 5.0,
    "living_street": 6.0,
    "pedestrian": 6.0,
    "footway": 3.5,
    "path": 3.0,
    "cycleway": 3.5,
    "bridleway": 3.0,
    "track": 4.0,
    "steps": 3.0,
}
RAIL_WIDTH_M = 6.0
STRUCTURES = {
    "arch": "arch",
    "beam": "beam",
    "girder": "beam",
    "simple": "beam",
    "humpback": "arch",
    "truss": "truss",
    "suspension": "suspension",
    "simple-suspension": "suspension",
    "cable-stayed": "cable_stayed",
    "cable_stayed": "cable_stayed",
    "floating": "beam",
}
MATERIALS = {
    "stone": "stone",
    "brick": "brick",
    "concrete": "concrete",
    "reinforced_concrete": "concrete",
    "steel": "steel",
    "metal": "steel",
    "iron": "steel",
    "wood": "wood",
    "timber": "wood",
}
KEEP = (
    "bridge",
    "bridge:structure",
    "bridge:material",
    "material",
    "layer",
    "highway",
    "railway",
    "lanes",
    "wikidata",
    "wikipedia",
    "historic",
    "heritage",
    "name",
    "bridge:name",
    "start_date",
)


def _local(lon0: float, lat0: float):
    mx, my = meters_per_deg_lon(lat0), METERS_PER_DEG_LAT

    def fwd(x, y, z=None):
        return ((x - lon0) * mx, (y - lat0) * my)

    def back(x, y, z=None):
        return (lon0 + x / mx, lat0 + y / my)

    return fwd, back


def _line(el: dict) -> LineString | None:
    pts = [(p["lon"], p["lat"]) for p in el.get("geometry") or [] if p]
    return LineString(pts) if len(pts) >= 2 else None


def _polygon(el: dict) -> Polygon | None:
    from xpsb.osm.classify import element_geometry

    geom = element_geometry(el)
    if geom is None or geom.geom_type not in ("Polygon", "MultiPolygon"):
        return None
    if geom.geom_type == "MultiPolygon":
        geom = max(geom.geoms, key=lambda g: g.area)
    return geom if geom.is_valid else geom.buffer(0)


def _float(value: str | None) -> float | None:
    try:
        return float(str(value).split()[0].replace(",", ".")) if value else None
    except ValueError:
        return None


def _width(tags: dict[str, str]) -> float:
    w = _float(tags.get("width"))
    if w and 1.5 <= w <= 80:
        return w
    if tags.get("railway"):
        return RAIL_WIDTH_M
    lanes = _float(tags.get("lanes"))
    if lanes and tags.get("highway") not in ("footway", "path", "cycleway", "steps"):
        return lanes * 3.3 + 2.0
    return ROAD_WIDTH_M.get(tags.get("highway", ""), 7.0)


def _deck_height(tags: dict[str, str]) -> float:
    h = _float(tags.get("height"))
    if h and 1 <= h <= 200:
        return h
    layer = _float(tags.get("layer")) or 1.0
    base = 12.0 if tags.get("bridge") == "viaduct" else 7.0 if tags.get("railway") else 6.0
    return round(base + max(0.0, layer - 1) * 5.0, 1)


def structure(tags: dict[str, str]) -> str:
    s = STRUCTURES.get(tags.get("bridge:structure", ""))
    if s:
        return s
    if tags.get("bridge") == "aqueduct" or _material(tags) in ("stone", "brick"):
        return "arch"
    return "beam"


def _material(tags: dict[str, str]) -> str | None:
    return MATERIALS.get(tags.get("bridge:material") or tags.get("material") or "")


def find(elements: Iterable[dict], aoi: Polygon) -> list[SceneItem]:
    elements = list(elements)
    outlines = []
    ways = []
    roads = []
    for el in elements:
        tags = el.get("tags") or {}
        if el["type"] in ("way", "relation") and tags.get("man_made") == "bridge":
            poly = _polygon(el)
            if poly is not None:
                outlines.append((f"{el['type']}/{el['id']}", tags, poly))
        elif el["type"] == "way" and (tags.get("highway") or tags.get("railway")):
            line = _line(el)
            if line is None:
                continue
            if tags.get("bridge") not in (None, "no"):
                ways.append((f"way/{el['id']}", tags, line))
            elif tags.get("highway"):
                roads.append((tags, line))
    if not ways and not outlines:
        return []
    c = aoi.centroid
    fwd, back = _local(c.x, c.y)
    seen_ways: set[str] = set()
    groups: list[tuple[str, dict, Polygon | None, list]] = []
    for ref, tags, poly in outlines:
        inside = [
            w
            for w in ways
            if w[0] not in seen_ways
            and poly.buffer(1e-5).contains(w[2].interpolate(0.5, normalized=True))
        ]
        seen_ways.update(w[0] for w in inside)
        groups.append((ref, tags, poly, inside))
    # Bridge ways without an outline: joined where they continue each other.
    rest = [w for w in ways if w[0] not in seen_ways]
    unique: dict[str, tuple] = {}
    for w in rest:
        unique.setdefault(w[0], w)
    rest = list(unique.values())
    parent = list(range(len(rest)))

    def find_root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, (_, ti, li) in enumerate(rest):
        ends_i = [Point(fwd(*li.coords[0])), Point(fwd(*li.coords[-1]))]
        for j in range(i + 1, len(rest)):
            tj, lj = rest[j][1], rest[j][2]
            if ti.get("layer", "1") != tj.get("layer", "1"):
                continue
            ends_j = [Point(fwd(*lj.coords[0])), Point(fwd(*lj.coords[-1]))]
            if any(a.distance(b) < END_SNAP_M for a in ends_i for b in ends_j):
                parent[find_root(j)] = find_root(i)
    clusters: dict[int, list] = {}
    for i, w in enumerate(rest):
        clusters.setdefault(find_root(i), []).append(w)
    for members in clusters.values():
        groups.append((members[0][0], members[0][1], None, members))

    items = []
    for ref, tags, poly, members in groups:
        item = _bridge(ref, tags, poly, members, roads, fwd, back)
        if item is None:
            continue
        geom = LineString(item.geometry["coordinates"])
        if (
            not aoi.contains(geom.interpolate(0.5, normalized=True))
            and geom.intersection(aoi).length < 0.5 * geom.length
        ):
            continue
        items.append(item)
    return items


def _bridge(ref, tags, poly, members, roads, fwd, back) -> SceneItem | None:
    way_tags = members[0][1] if members else {}
    all_tags = {**way_tags, **tags}  # the outline's tags win
    if members:
        union = unary_union([m[2] for m in members])
        merged = linemerge(union) if union.geom_type == "MultiLineString" else union
        if merged.geom_type == "MultiLineString":
            merged = max(merged.geoms, key=lambda g: g.length)
        axis = transform(fwd, merged)
    else:
        axis = None
    if poly is not None:
        local_poly = transform(fwd, poly)
        if axis is None:  # the outline's long middle line
            rect = local_poly.minimum_rotated_rectangle
            pts = list(rect.exterior.coords)[:4]
            sides = [(pts[i], pts[(i + 1) % 4]) for i in range(4)]
            sides.sort(key=lambda s: -LineString(s).length)
            (a, b), (c, d) = sides[0], sides[1]
            axis = LineString(
                [((a[0] + d[0]) / 2, (a[1] + d[1]) / 2), ((b[0] + c[0]) / 2, (b[1] + c[1]) / 2)]
            )
        width = max(2.0, min(80.0, local_poly.area / max(axis.length, 1.0)))
    else:
        width = max(_width(m[1]) for m in members)
        if len(members) > 1 and len({m[1].get("highway") for m in members}) > 1:
            width += 2.0  # a road with a footway alongside on the same bridge
    if axis.length < MIN_LENGTH_M:
        return None
    axis = axis.simplify(0.3)
    name = (tags.get("name") if poly is not None else None) or (
        way_tags.get("bridge:name") or way_tags.get("name")
    )
    attrs = ItemAttrs(
        height_m=_deck_height(all_tags),
        width_m=round(width, 1),
        material=_material(all_tags),
        bridge_structure=structure(all_tags),
    )
    geometry = mapping(transform(back, axis))
    geometry = {
        "type": "LineString",
        "coordinates": [[round(x, 7), round(y, 7)] for x, y in geometry["coordinates"]],
    }
    item = SceneItem(
        id=ref if ref.startswith(("way/", "relation/")) else f"bridge/{ref}",
        kind="object",
        category=ObjectCategory.BRIDGES,
        geometry=geometry,
        source="osm",
        name=name,
        osm_ref=ref,
        tags={k: v for k, v in all_tags.items() if k in KEEP},
        attrs=attrs,
    )
    item.approaches = _approaches(axis, roads, fwd, back)
    return item


def _approaches(axis: LineString, roads, fwd, back) -> list[dict]:
    """The roads that start at the ends of the bridge axis, each oriented away
    from the bridge and cut to APPROACH_M."""
    ends = [Point(axis.coords[0]), Point(axis.coords[-1])]
    out = []
    for _, line in roads:
        local = transform(fwd, line)
        coords = list(local.coords)
        for end in ends:
            if Point(coords[0]).distance(end) < END_SNAP_M:
                path = coords
            elif Point(coords[-1]).distance(end) < END_SNAP_M:
                path = coords[::-1]
            else:
                continue
            cut = _cut(LineString(path), APPROACH_M)
            out.append(
                {
                    "type": "LineString",
                    "coordinates": [
                        [round(x, 7), round(y, 7)] for x, y in (back(*p) for p in cut.coords)
                    ],
                }
            )
    return out


def _cut(line: LineString, length: float) -> LineString:
    if line.length <= length:
        return line
    pts = [line.coords[0]]
    run = 0.0
    for a, b in zip(line.coords, line.coords[1:], strict=False):
        seg = LineString([a, b]).length
        if run + seg >= length:
            t = (length - run) / seg
            pts.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            break
        pts.append(b)
        run += seg
    return LineString(pts)
