"""Rows of houses under one roof.

OpenStreetMap maps a terrace the way it is lived in: every house its own outline.
Built one by one, every house got its own roof - a row of little gables where the
aerial image shows one long roof along the whole row. Neighbours that share a wall
are joined into one building when nothing tells them apart: no name or landmark of
their own, about the same height, the same roof form and - the aerial image has the
last word - the same roof colour.
"""

from __future__ import annotations

from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from xpsb.geo import METERS_PER_DEG_LAT, meters_per_deg_lon
from xpsb.scene import SceneItem

ROW_BUILDINGS = {
    "house",
    "terrace",
    "semidetached_house",
    "detached",
    "residential",
    "apartments",
    "yes",
}
MIN_SHARED_M = 3.0  # wall two houses must share
MAX_HEIGHT_DIFF_M = 1.5
MAX_COLOR_DIFF = 45  # roof colours further apart (RGB distance) are different roofs
MAX_ROW_AREA_M2 = 4000.0


def _rgb(value: str | None) -> tuple[int, int, int] | None:
    if not value or len(value) != 7:
        return None
    try:
        return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]
    except ValueError:
        return None


def _candidate(it: SceneItem) -> bool:
    return (
        it.kind == "object"
        and str(it.category) == "buildings"
        and it.source == "osm"
        and not it.name
        and not it.parts
        and not it.edited
        and not it.remembered
        and not it.script
        and not it.photos
        and it.tags.get("building", "yes") in ROW_BUILDINGS
        and not any(k in it.tags for k in ("wikidata", "historic", "heritage", "tourism"))
        and not it.tags.get("site:name")  # wings of a castle, abbey ... are no terrace
        and it.geometry.get("type") == "Polygon"
    )


def _height(it: SceneItem) -> float | None:
    if it.attrs.height_m is not None:
        return it.attrs.height_m
    if it.attrs.levels is not None:
        return it.attrs.levels * 3.0
    return None


def _alike(a: SceneItem, b: SceneItem, seen: dict[str, str]) -> bool:
    ha, hb = _height(a), _height(b)
    if (ha is None) != (hb is None):
        return False
    if ha is not None and hb is not None and abs(ha - hb) > MAX_HEIGHT_DIFF_M:
        return False
    ra, rb = a.attrs.roof_shape, b.attrs.roof_shape
    if ra and rb and ra != rb:
        return False
    ca = _rgb(a.attrs.roof_color or seen.get(a.id))
    cb = _rgb(b.attrs.roof_color or seen.get(b.id))
    if not (ca and cb):
        return True
    return sum((x - y) ** 2 for x, y in zip(ca, cb, strict=True)) ** 0.5 <= MAX_COLOR_DIFF


def merge(items: list[SceneItem], seen: dict[str, str] | None = None) -> int:
    """Joins rows of houses (in place). `seen`: roof colours measured on the aerial
    image per item id. Returns how many houses went into rows."""
    seen = seen or {}
    cands = [it for it in items if _candidate(it)]
    if len(cands) < 2:
        return 0
    lat = shape(cands[0].geometry).centroid.y
    mx, my = meters_per_deg_lon(lat), METERS_PER_DEG_LAT
    geoms = {it.id: shape(it.geometry) for it in cands}
    eps = 0.15 / my  # ~15 cm: outlines that touch only nearly still touch

    def shared_m(a: SceneItem, b: SceneItem) -> float:
        line = geoms[a.id].buffer(eps).intersection(geoms[b.id].boundary)
        if line.is_empty:
            return 0.0
        x0, y0, x1, y1 = line.bounds
        return (((x1 - x0) * mx) ** 2 + ((y1 - y0) * my) ** 2) ** 0.5

    def area_m2(geom) -> float:
        return geom.area * mx * my

    # Union-find over neighbours that belong together.
    parent = {it.id: it.id for it in cands}

    def find(i: str) -> str:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for n, a in enumerate(cands):
        ga = geoms[a.id]
        for b in cands[n + 1 :]:
            if not ga.buffer(eps).intersects(geoms[b.id]):
                continue
            if _alike(a, b, seen) and shared_m(a, b) >= MIN_SHARED_M:
                ra, rb = find(a.id), find(b.id)
                if ra != rb:
                    members = [i for i in parent if find(i) in (ra, rb)]
                    if sum(area_m2(geoms[i]) for i in members) <= MAX_ROW_AREA_M2:
                        parent[rb] = ra

    groups: dict[str, list[SceneItem]] = {}
    for it in cands:
        groups.setdefault(find(it.id), []).append(it)
    joined = 0
    gone: set[str] = set()
    for members in groups.values():
        if len(members) < 2:
            continue
        union = unary_union([geoms[m.id].buffer(eps) for m in members]).buffer(-eps)
        if union.geom_type != "Polygon" or union.is_empty:
            continue  # the houses do not form one outline after all
        first = members[0]
        originals = [m.model_dump(mode="json", exclude={"row_members"}) for m in members]
        first.row_members = originals
        first.geometry = mapping(union.simplify(eps / 3))
        heights = [h for h in (_height(m) for m in members) if h is not None]
        if heights:
            first.attrs.height_m = round(max(heights), 1)
        first.attrs.roof_shape = next(
            (m.attrs.roof_shape for m in members if m.attrs.roof_shape), None
        )
        first.tags = {
            **first.tags,
            "xpsb:row_of": str(len(members)),
            "xpsb:merged": ";".join(m.osm_ref or m.id for m in members[1:]),
        }
        gone.update(m.id for m in members[1:])
        joined += len(members)
    if gone:
        items[:] = [it for it in items if it.id not in gone]
    return joined


def split(item: SceneItem) -> list[SceneItem]:
    """The houses of a joined row again, as OSM maps them (they keep the roof colour
    measured for the row and are not joined again)."""
    out = []
    for data in item.row_members:
        house = SceneItem.model_validate(data)
        if house.attrs.roof_color is None:
            house.attrs.roof_color = item.attrs.roof_color
        house.edited = True  # the user decided: separate houses
        house.included = item.included
        out.append(house)
    return out
