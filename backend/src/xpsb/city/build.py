"""City mode: every ordinary building of the area, built block by block.

A building as its own object costs X-Plane a draw call each; a city district has
thousands. Here all ordinary buildings (landmarks stay single, detailed objects)
are grouped into blocks of about CELL_M x CELL_M: per block one OBJ for the walls
(one shared facade atlas for the whole city) and one for the roofs (painted, or
cut from the aerial image). Data: the official 3D buildings of Bavaria (LoD2,
real roof shapes) where available, else the OSM outlines with height and roof.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image
from shapely.geometry import Polygon, shape

from xpsb.city import atlas as atlas_mod
from xpsb.city import lod2, roofs, styles
from xpsb.city.mesh import Frame, build_block
from xpsb.city.model import CityBuilding
from xpsb.geo import METERS_PER_DEG_LAT, meters_per_deg_lon
from xpsb.scene import SceneItem

CELL_M = 250.0
ATLAS = "city_atlas.png"
ATLAS_LIT = "city_atlas_LIT.png"
ROOF_SHAPES = {
    "flat": "flat",
    "gabled": "gabled",
    "hipped": "hipped",
    "half-hipped": "hipped",
    "mansard": "mansard",
    "gambrel": "mansard",
    "pyramidal": "pyramid",
    "skillion": "shed",
    "dome": "flat",
    "onion": "pyramid",
}
USES = {
    "house": "residential",
    "detached": "residential",
    "semidetached_house": "residential",
    "terrace": "residential",
    "apartments": "residential",
    "residential": "residential",
    "bungalow": "residential",
    "commercial": "commercial",
    "retail": "commercial",
    "office": "commercial",
    "hotel": "commercial",
    "supermarket": "commercial",
    "industrial": "industrial",
    "warehouse": "industrial",
    "manufacture": "industrial",
    "garage": "garage",
    "garages": "garage",
    "carport": "garage",
    "farm": "farm",
    "barn": "farm",
    "farm_auxiliary": "farm",
    "church": "religious",
    "chapel": "religious",
    "cathedral": "religious",
    "school": "public",
    "university": "public",
    "hospital": "public",
    "public": "public",
    "civic": "public",
    "government": "public",
    "kindergarten": "public",
}


@dataclass
class CityBlock:
    n: int
    anchor: tuple[float, float]  # lon, lat of the block's origin
    buildings: int
    triangles: int
    files: list[str] = field(default_factory=list)  # in objects/ of the package
    objects: list[str] = field(default_factory=list)  # the OBJ files placed in the DSF


@dataclass
class CityResult:
    source: str  # "lod2" | "osm"
    blocks: list[CityBlock]
    buildings: int
    skipped: int
    attribution: str | None = None


# ---- the buildings ------------------------------------------------------------------


def from_items(items: list[SceneItem]) -> list[CityBuilding]:
    """OSM buildings as city buildings (height, roof form and colour as known)."""
    out = []
    for it in items:
        geom = shape(it.geometry)
        if geom.geom_type == "MultiPolygon":
            geom = max(geom.geoms, key=lambda g: g.area)
        if geom.geom_type != "Polygon" or geom.is_empty:
            continue
        a = it.attrs
        rings = [list(geom.exterior.coords)[:-1]] + [list(r.coords)[:-1] for r in geom.interiors]
        roof = ROOF_SHAPES.get(
            a.roof_shape or "", "flat" if a.levels and a.levels >= 5 else "gabled"
        )
        height = a.height_m
        if height is None:
            levels = a.levels or 2
            eave = levels * 3.0 + 0.5
            height = eave + (0 if roof == "flat" else 3.5)
        else:
            eave = height if roof == "flat" else max(2.5, height * 0.72)
        color = None
        if a.roof_color and len(a.roof_color) == 7:
            color = tuple(int(a.roof_color[i : i + 2], 16) for i in (1, 3, 5))
        out.append(
            CityBuilding(
                id=it.id,
                source="osm",
                footprint=rings,
                eave_m=eave,
                ridge_m=height,
                roof_type=roof,
                use=USES.get(it.tags.get("building", ""), "residential"),
                levels=a.levels,
                tags=dict(it.tags),
                roof_color=color,  # type: ignore[arg-type]
            )
        )
    return out


def lod2_buildings(
    ring: list[tuple[float, float]], cache_dir: Path, progress: Callable | None = None
) -> list[CityBuilding]:
    """The official 3D buildings (Bavaria) inside the area."""
    area = Polygon(ring)
    out = []
    for path in lod2.download(ring, cache_dir, progress):
        for b in lod2.parse(path):
            try:
                if area.contains(Polygon(b.footprint[0]).representative_point()):
                    out.append(b)
            except ValueError:
                continue
    return out


def without(buildings: list[CityBuilding], keep_out: list) -> list[CityBuilding]:
    """Drops the buildings that stand where a single object is built (landmarks)."""
    if not keep_out:
        return buildings
    out = []
    for b in buildings:
        try:
            p = Polygon(b.footprint[0])
        except ValueError:
            continue
        if not any(p.intersects(g) and p.intersection(g).area > 0.3 * p.area for g in keep_out):
            out.append(b)
    return out


# ---- building the blocks ---------------------------------------------------------------


def _area_m2(b: CityBuilding) -> float:
    lat = b.footprint[0][0][1]
    return Polygon(b.footprint[0]).area * meters_per_deg_lon(lat) * METERS_PER_DEG_LAT


def _blocks(buildings: list[CityBuilding], center: tuple[float, float]) -> dict:
    lon0, lat0 = center
    mx = meters_per_deg_lon(lat0)
    out: dict[tuple[int, int], list[CityBuilding]] = {}
    for b in buildings:
        c = Polygon(b.footprint[0]).centroid
        key = (
            math.floor((c.x - lon0) * mx / CELL_M),
            math.floor((c.y - lat0) * METERS_PER_DEG_LAT / CELL_M),
        )
        out.setdefault(key, []).append(b)
    return out


def _cell_center(key, center) -> tuple[float, float]:
    lon0, lat0 = center
    x = (key[0] + 0.5) * CELL_M
    y = (key[1] + 0.5) * CELL_M
    return lon0 + x / meters_per_deg_lon(lat0), lat0 + y / METERS_PER_DEG_LAT


def _roof_image(mesh, frame: Frame, aerial) -> Image.Image:
    """The block's roofs: cut from the aerial image (when it may go into the package),
    else painted from the roof faces."""
    crop = None
    if aerial is not None:
        image, (west, south, east, north) = aerial
        x0, y0, x1, y1 = mesh.roof_bounds
        w = frame.lon0 + x0 / frame.mx
        e = frame.lon0 + x1 / frame.mx
        s = frame.lat0 + y0 / METERS_PER_DEG_LAT
        n = frame.lat0 + y1 / METERS_PER_DEG_LAT
        if west <= w and e <= east and south <= s and n <= north:
            sx = image.width / (east - west)
            sy = image.height / (north - south)
            crop = image.crop(
                (
                    int((w - west) * sx),
                    int((north - n) * sy),
                    int((e - west) * sx),
                    int((north - s) * sy),
                )
            )
    return roofs.paint(mesh.roof_bounds, mesh.roof_faces, crop)


def build(
    buildings: list[CityBuilding],
    preset_id: str,
    center: tuple[float, float],
    out_dir: Path,
    aerial=None,
    preview_dir: Path | None = None,
    progress: Callable[[float], None] | None = None,
) -> list[CityBlock]:
    """Writes the blocks (OBJ + textures) into out_dir and, for the 3D preview, their
    meshes into preview_dir. `aerial`: (image, (w, s, e, n)) for real roofs."""
    preset = styles.PRESETS.get(preset_id) or styles.CENTRAL_EUROPE
    roof_material: dict[str, str] = {}
    for b in buildings:
        style, wall, roof = styles.assign(b, _area_m2(b), preset)
        b.style, b.variant = style.facade.id, wall
        b.roof_color = b.roof_color or style.roof.colors[roof]
        roof_material[style.facade.id] = style.roof.material
    used = {(b.style, b.variant) for b in buildings}
    if not used:
        return []
    atlas = atlas_mod.build(preset, used)
    out_dir.mkdir(parents=True, exist_ok=True)
    atlas.image.save(out_dir / ATLAS)
    atlas.lit.save(out_dir / ATLAS_LIT)
    if preview_dir is not None:
        preview_dir.mkdir(parents=True, exist_ok=True)
        atlas.image.save(preview_dir / ATLAS)
    blocks = []
    groups = sorted(_blocks(buildings, center).items())
    for n, (key, group) in enumerate(groups, start=1):
        lon, lat = _cell_center(key, center)
        frame = Frame(lon, lat)
        mesh = build_block(group, frame, atlas, roof_material)
        stem = f"city_{n:03d}"
        block = CityBlock(n, (lon, lat), mesh.buildings, mesh.triangles)
        mesh.walls.texture, mesh.walls.lit_texture = ATLAS, ATLAS_LIT
        mesh.roofs.texture = f"{stem}_roofs.png"
        _roof_image(mesh, frame, aerial).save(out_dir / f"{stem}_roofs.png")
        for part, obj in (("walls", mesh.walls), ("roofs", mesh.roofs)):
            if not obj.indices:
                continue
            (out_dir / f"{stem}_{part}.obj").write_text(
                obj.to_text(), encoding="ascii", newline="\n"
            )
            block.objects.append(f"{stem}_{part}.obj")
            if preview_dir is not None:
                (preview_dir / f"{stem}_{part}.json").write_text(
                    json.dumps(obj_preview(obj)), encoding="utf-8"
                )
        if preview_dir is not None and preview_dir != out_dir:
            with Image.open(out_dir / f"{stem}_roofs.png") as img:
                img.save(preview_dir / f"{stem}_roofs.png")
        block.files = [*block.objects, f"{stem}_roofs.png"]
        blocks.append(block)
        if progress:
            progress(n / len(groups))
    return blocks


def obj_preview(obj) -> dict:
    """An OBJ8 (x east, y up, z south; clockwise faces) as the 3D preview's arrays
    (x east, y north, z up; counter-clockwise)."""
    positions, normals, uvs = [], [], []
    for v in obj.vertices:
        positions += [round(v.x, 3), round(-v.z, 3), round(v.y, 3)]
        normals += [round(v.nx, 3), round(-v.nz, 3), round(v.ny, 3)]
        uvs += [round(v.s, 5), round(v.t, 5)]
    index = []
    for i in range(0, len(obj.indices) - 2, 3):
        a, b, c = obj.indices[i : i + 3]
        index += [a, c, b]
    return {
        "positions": positions,
        "normals": normals,
        "uvs": uvs,
        "index": index,
        "lights": [],
        "lit": False,
        "alpha": False,
    }
