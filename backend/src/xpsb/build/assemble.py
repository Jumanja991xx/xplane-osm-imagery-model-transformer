"""Turns the confirmed scene list into 3D objects, ground surfaces and a DSF.

Used twice: for the 3D preview when the plan is made (job folder only) and for
the real package build after approval C. Generation is deterministic, so the
build produces exactly what the preview showed.
"""

from __future__ import annotations

import json
import math
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageEnhance
from shapely.geometry import Polygon, shape
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from xpsb.build import phototex
from xpsb.build.bake import bake
from xpsb.build.objexport import to_obj8, to_preview
from xpsb.build.sandbox import ScriptError, ScriptInputs, run_script
from xpsb.build.scripts import DEFAULT_SCRIPTS
from xpsb.geo import METERS_PER_DEG_LAT, meters_per_deg_lon
from xpsb.scene import SceneItem
from xpsb.xplane.dsf_text import DsfText, Tile
from xpsb.xplane.library import Catalog
from xpsb.xplane.pol import DrapedPolygonDef

if TYPE_CHECKING:
    from xpsb.blender import Status as BlenderStatus

DETAIL = {
    # triangle budget per object, atlas size, max texels per metre
    "low": (8_000, 1024, 12.0),
    "medium": (30_000, 2048, 24.0),
    "high": (80_000, 4096, 40.0),
}
SURFACE_LAYER = "airports"  # above the default terrain's own polygons (Spike A)
SURFACE_ORDER = {
    "grass": 0,
    "dirt": 1,
    "gravel": 1,
    "concrete": 2,
    "asphalt": 2,
    "tartan": 3,
    "markings": 4,
}
SURFACE_TILE_M = {
    "grass": 16.0,
    "dirt": 16.0,
    "gravel": 12.0,
    "concrete": 12.0,
    "asphalt": 20.0,
    "tartan": 12.0,
    "markings": 8.0,
}
SOLAR_WORDS = ("solar", "photovolt", "pv-")
# OSM building:material / roof:material → our materials
WALL_MATERIALS = {
    "brick": "brick",
    "concrete": "concrete",
    "glass": "glass",
    "metal": "metal_sheet",
    "steel": "metal_sheet",
    "wood": "wood",
    "timber_framing": "plaster",
    "plaster": "plaster",
    "cement_block": "concrete",
    "stone": "stone",
    "sandstone": "stone",
    "limestone": "stone",
}
OSM_ROOF_MATERIALS = {
    "roof_tiles": "tiles",
    "tile": "tiles",
    "metal": "metal",
    "metal_sheet": "metal",
    "glass": "glass",
    "concrete": "flat",
    "tar_paper": "flat",
    "gravel": "flat",
    "asphalt": "flat",
    "membrane": "membrane",
    "plastic": "membrane",
    "solar_panels": "solar",
}
ROOF_MATERIALS = {
    "metal": "roof_metal",
    "membrane": "membrane",
    "tiles": "roof_tiles",
    "glass": "glass",
    "flat": "roof_flat",
}


@dataclass
class ItemResult:
    item_id: str
    stem: str  # file name stem, e.g. "xpsb_obj_003"
    category: str
    name: str | None
    anchor: tuple[float, float]  # lon, lat
    status: str  # "ok" | "error" | "skipped"
    message: str = ""
    triangles: int = 0
    has_mesh: bool = True  # False: only X-Plane library objects
    designed: bool = False  # built from an own script of the AI architect
    lit: bool = False  # has a night texture
    lights: int = 0
    photo_textures: list[str] = field(default_factory=list)  # ids of photos painted on it
    scan: bool = False  # its free 3D scan was placed (credited in the README)
    heading: float = 0.0  # degrees clockwise, as turned in the 3D preview
    # What the built object covers on the ground (local metres around the anchor,
    # before turning): west, south, east, north. Sizes the exclusion zone.
    extent: tuple[float, float, float, float] | None = None
    shaded: bool = False  # ambient occlusion baked in by Blender
    detailed: bool = False  # fine detail (surrounds, dentils) baked into its normal map
    blender_input: str | None = None  # prepared for Blender (package build only)
    blender_problem: str = ""  # why Blender did not shade it (package build only)
    # X-Plane library objects: path, x/y in metres from the anchor, heading, size
    library: list[dict] = field(default_factory=list)


# ---- local frames ----------------------------------------------------------------


def _anchor(item: SceneItem) -> tuple[float, float]:
    geom = shape(item.geometry)
    p = (
        geom.centroid
        if geom.geom_type in ("Polygon", "MultiPolygon")
        else (geom.representative_point())
    )
    return p.x, p.y


def _extent(mesh) -> tuple[float, float, float, float] | None:
    xs, ys = [], []
    for patch in mesh.patches:
        if patch.lod != "bake":
            xs += [p[0] for p in patch.positions]
            ys += [p[1] for p in patch.positions]
    xs += [p.x for p in mesh.placements]
    ys += [p.y for p in mesh.placements]
    if not xs:
        return None
    return (round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2))


def placement(item: SceneItem) -> tuple[tuple[float, float], float]:
    """Where the object stands (lon, lat) and its heading (degrees clockwise): the
    centre of its outline, moved and turned as set in the 3D preview."""
    lon, lat = _anchor(item)
    a = item.attrs
    lon += (a.shift_x_m or 0.0) / meters_per_deg_lon(lat)
    lat += (a.shift_y_m or 0.0) / METERS_PER_DEG_LAT
    return (lon, lat), (a.turn_deg or 0.0) % 360.0


def _to_local(lon0: float, lat0: float) -> Callable[[float, float], list[float]]:
    mx = meters_per_deg_lon(lat0)
    return lambda lon, lat: [
        round((lon - lon0) * mx, 3),
        round((lat - lat0) * METERS_PER_DEG_LAT, 3),
    ]


def _script_inputs(item: SceneItem, items: list[SceneItem], detail: str) -> ScriptInputs:
    lon0, lat0 = _anchor(item)
    local = _to_local(lon0, lat0)
    geom = shape(item.geometry)
    footprint = path = None
    if geom.geom_type == "MultiPolygon":
        geom = max(geom.geoms, key=lambda g: g.area)
    courtyards: list = []
    if geom.geom_type == "Polygon":
        shaped = orient(geom, 1.0)
        ring = list(shaped.exterior.coords)[:-1]
        footprint = [local(x, y) for x, y in ring]
        courtyards = [
            [local(x, y) for x, y in list(h.coords)[:-1]]
            for h in shaped.interiors
            if Polygon(h).area * meters_per_deg_lon(lat0) * METERS_PER_DEG_LAT > 20  # m2
        ]
    elif geom.geom_type == "LineString":
        path = [local(x, y) for x, y in geom.coords]
    textures = {
        t.name: {"width": t.width_m, "height": t.height_m, "repeat": t.repeat, "walls": t.walls}
        for t in item.textures
    }
    return ScriptInputs(
        footprint=footprint,
        path=path,
        params=_params(item, items, local, detail),
        courtyards=courtyards,
        textures=textures,
    )


def _params(item: SceneItem, items: list[SceneItem], local, detail: str) -> dict:
    a = item.attrs
    summary = (item.ai.summary if item.ai else "").lower()
    material = (a.material or "").lower()
    tags = item.tags
    if not material and tags.get("roof:material"):
        material = "roof:" + OSM_ROOF_MATERIALS.get(tags["roof:material"], "")
    if material.startswith("roof:"):  # chosen explicitly (AI find, the user or OSM)
        roof_material = "solar" if "solar" in material else ROOF_MATERIALS.get(material[5:])
    elif a.covered is False:  # the user took the roof off
        roof_material = None
    else:
        solar = any(w in summary or w in material for w in SOLAR_WORDS)
        roof_material = "solar" if solar else ROOF_MATERIALS.get(material)
    return {
        "height": a.height_m,
        "levels": a.levels,
        "roof_shape": a.roof_shape,
        "roof_color": a.roof_color,
        "wall_color": a.wall_color,
        "seat_color": a.seat_color,
        "covered": a.covered,
        "material": a.material,
        "roof_material": roof_material,
        "facing": _facing(item, items, local),
        "flip": bool(a.flip),
        "rows": a.rows,
        "wall_material": a.wall_material or WALL_MATERIALS.get(tags.get("building:material", "")),
        "building": _building_type(item),
        "min_height": a.min_height_m,
        "parts": _parts(item, local),
        "parts_cover": _parts_cover(item),
        "tags": dict(tags),
        "detail": detail,
        "name": item.name,
        "monument": _monument(item) if str(item.category) == "monuments" else None,
    }


# Statue material from OSM's material tag.
_BRONZE = ("bronze", "copper", "brass", "metal", "iron", "steel")


def _monument(item: SceneItem) -> dict:
    """What the default monument script builds: kind, pose and material, from OSM
    tags (the AI architect can do better from photos and Wikipedia)."""
    from xpsb.osm.classify import monument_kind

    tags, a = item.tags, item.attrs
    kind = a.monument_kind or monument_kind(tags) or "statue"
    material = (a.material or tags.get("material") or "").lower()
    figure = kind in ("statue", "rider", "bust")
    if any(w in material for w in _BRONZE):
        mat = "bronze"
    elif material:
        mat = "stone"
    else:
        mat = "bronze" if figure else "stone"
    text = " ".join(tags.get(k, "") for k in ("name", "name:en", "subject", "description")).lower()
    robe = any(
        w in text
        for w in (
            "madonna",
            "mary",
            "mária",
            "maria",
            "saint",
            "szent",
            "heilige",
            "liberty",
            "freiheit",
            "szabadság",
            "victoria",
            "allegor",
        )
    )
    return {
        "kind": kind,
        "material": mat,
        "robe": robe if a.robe is None else a.robe,
        "pose": a.pose,
        "holding": a.holding,
    }


def _building_type(item: SceneItem) -> str | None:
    tags = item.tags
    b = tags.get("building") or tags.get("building:part")
    # The main building of stadium grounds is the stadium, whatever its own tag says.
    if b in (None, "yes") and tags.get("site:type") == "stadium" and tags.get("site:main") == "yes":
        return "stadium"
    return b


def _parts(item: SceneItem, local) -> list[dict]:
    """OSM building parts in local metres (the default script builds them instead of
    the plain outline; the AI architect sees them as a shape description)."""
    out = []
    for part in item.parts:
        geom = shape(part.geometry)
        if geom.geom_type != "Polygon" or geom.is_empty:
            continue
        ring = list(orient(geom, 1.0).exterior.coords)[:-1]
        out.append(
            {
                "footprint": [local(x, y) for x, y in ring],
                "height": part.height_m,
                "min_height": part.min_height_m,
                "roof_shape": part.roof_shape,
                "wall_color": part.wall_color,
                "roof_color": part.roof_color,
                "part": part.part,
            }
        )
    return out


def _parts_cover(item: SceneItem) -> float:
    """Share of the outline covered by its parts (below 0.8 the rest is built too)."""
    if not item.parts:
        return 0.0
    outline = shape(item.geometry)
    if outline.area <= 0:
        return 0.0
    covered = unary_union([shape(p.geometry) for p in item.parts]).intersection(outline)
    return round(min(1.0, covered.area / outline.area), 3)


def _facing(item: SceneItem, items: list[SceneItem], local) -> list[float] | None:
    """Unit vector from the object to the nearest track/pitch (stands face the action)."""
    here = shape(item.geometry)
    best = None
    for other in items:
        if other.kind != "surface" or not other.included or other.proposal:
            continue
        geom = shape(other.geometry)
        d = geom.distance(here)
        if best is None or d < best[0]:
            best = (d, geom)
    if best is None:
        return None
    c = best[1].centroid
    x, y = local(c.x, c.y)
    length = math.hypot(x, y)
    return [round(x / length, 4), round(y / length, 4)] if length > 1e-6 else None


# ---- objects -----------------------------------------------------------------------


def buildable(item: SceneItem) -> bool:
    return item.kind == "object" and item.included and not item.proposal


def generate(
    item: SceneItem,
    items: list[SceneItem],
    detail: str,
    library: Catalog | None = None,
    photo_dir: Path | None = None,
):
    """Runs the item's build script. Returns (mesh, baked or None, ids of the photos
    painted onto it) or raises ScriptError."""
    script = item.script or DEFAULT_SCRIPTS.get(str(item.category))
    if script is None:
        raise LookupError("no generator yet")
    budget, atlas, max_ppm = DETAIL.get(detail, DETAIL["medium"])
    inputs = _script_inputs(item, items, detail)
    if item.scan is not None and photo_dir is not None and item.attrs.use_scan is not False:
        scan_path = photo_dir.parent / item.scan.file
        inputs.scan = str(scan_path) if scan_path.is_file() else None
    if (
        inputs.footprint is None
        and inputs.path is None
        and str(item.category)
        not in (
            "masts",
            "monuments",
        )
    ):
        raise LookupError("needs an area outline")
    name = f"{item.id}.star" if item.script else f"{item.category}.star"
    lib = frozenset(library.objects) if library else None
    mesh = run_script(script, inputs, budget, name=name, library=lib)
    photos = phototex.load(item.textures, item.photos, photo_dir) if item.textures else {}
    used = {p.material[6:] for p in mesh.patches if p.material.startswith("photo:")}
    photo_ids = sorted({t.photo_id for t in item.textures if t.name in used and t.name in photos})
    baked = bake(mesh, atlas, max_ppm, photos) if mesh.patches else None
    return mesh, baked, photo_ids


def _lod(mesh) -> float:
    (x0, y0, z0), (x1, y1, z1) = mesh.bounds()
    size = max(x1 - x0, y1 - y0, z1 - z0)
    return min(8000.0, max(1500.0, size * 40))


def _near(mesh, lod: float) -> float:
    """Up to here the close-up level (real windows, small parts) is drawn."""
    (x0, y0, z0), (x1, y1, z1) = mesh.bounds()
    size = max(x1 - x0, y1 - y0, z1 - z0)
    return min(lod * 0.5, max(300.0, size * 4))


def generate_one(
    item: SceneItem,
    items: list[SceneItem],
    detail: str,
    out_dir: Path,
    stem: str,
    preview: bool,
    library: Catalog | None = None,
    photo_dir: Path | None = None,
    blender_exe: str | None = None,
) -> ItemResult:
    anchor, heading = placement(item)
    res = ItemResult(item.id, stem, str(item.category), item.name, anchor, "ok", heading=heading)
    res.designed = bool(item.script)
    for name in (
        f"{stem}.png",
        f"{stem}.json",
        f"{stem}.obj",
        f"{stem}_nml.png",
        f"{stem}_lit.png",
    ):
        (out_dir / name).unlink(missing_ok=True)
    try:
        mesh, baked, photo_ids = generate(item, items, detail, library, photo_dir)
    except LookupError as exc:
        res.status, res.message = "skipped", str(exc)
    except (ScriptError, ValueError) as exc:
        res.status, res.message = "error", str(exc)[:2000]
    else:
        res.triangles = mesh.triangle_count
        res.extent = _extent(mesh)
        res.photo_textures = photo_ids
        res.scan = mesh.used_scan
        res.library = [
            {
                "path": p.path,
                "x": round(p.x, 2),
                "y": round(p.y, 2),
                "heading": round(p.heading, 1),
                "size": list(library.objects[p.path].size or ()) if library else [],
                "pack": library.objects[p.path].pack if library else "",
                "default": library.objects[p.path].default if library else True,
            }
            for p in mesh.placements
        ]
        if baked is None:
            res.has_mesh = False
            return res
        if blender_exe and not preview:  # Blender shades it afterwards (all in one run)
            from xpsb import blender

            npz = out_dir / BLENDER_DIR / f"{stem}.npz"
            if blender.prepare(mesh, baked, npz):
                res.blender_input = str(npz)
        baked.image.save(out_dir / f"{stem}.png", optimize=False)
        normal = None
        if baked.normal is not None:
            normal = f"{stem}_nml.png"
            baked.normal.save(out_dir / normal, optimize=False)
        lit = None
        res.lights = len(mesh.lights)
        if baked.lit is not None:
            res.lit = True
            lit = f"{stem}_lit.png"
            baked.lit.save(out_dir / lit, optimize=True)
        if preview:
            data = to_preview(mesh, baked)
            (out_dir / f"{stem}.json").write_text(json.dumps(data), encoding="utf-8")
        else:
            lod = _lod(mesh)
            obj = to_obj8(mesh, baked, f"{stem}.png", lod, normal, lit, _near(mesh, lod))
            obj.comment = f"xpsb {item.category} {item.id}"
            (out_dir / f"{stem}.obj").write_text(obj.to_text(), encoding="ascii", newline="\n")
    return res


def generate_all(
    items: list[SceneItem],
    detail: str,
    out_dir: Path,
    preview: bool,
    progress: Callable[..., None] | None = None,
    library: Catalog | None = None,
    photo_dir: Path | None = None,
    blender_exe: str | None = None,
) -> list[ItemResult]:
    """progress(fraction, object number, objects, object name)."""
    """Writes OBJ8 + PNG (package) or preview JSON + PNG (job folder) per object."""
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = [it for it in items if buildable(it)]
    results: list[ItemResult] = []
    for n, item in enumerate(todo, start=1):
        results.append(
            generate_one(
                item,
                items,
                detail,
                out_dir,
                f"xpsb_obj_{n:03d}",
                preview,
                library,
                photo_dir,
                blender_exe,
            )
        )
        if progress:
            progress(n / max(1, len(todo)), n, len(todo), item.name or str(item.category))
    return results


# ---- surfaces ----------------------------------------------------------------------


def _surface_texture(kind: str, px: int = 512) -> Image.Image:
    from xpsb.build.materials import _noise

    tile = SURFACE_TILE_M.get(kind, 16.0)
    coords = (np.arange(px, dtype=np.float32) + 0.5) * tile / px
    u = coords[None, :] + np.zeros((px, 1), dtype=np.float32)
    v = coords[:, None] + np.zeros((1, px), dtype=np.float32)

    def tiled(scale, seed):  # noise that wraps around the tile edges
        cells = max(1, round(tile / scale))
        su = np.floor(u / tile * cells) % cells
        sv = np.floor(v / tile * cells) % cells
        return _noise(su, sv, 1.0, seed)

    base = {
        "grass": (88, 128, 60),
        "dirt": (168, 128, 92),
        "gravel": (160, 156, 148),
        "concrete": (170, 168, 162),
        "asphalt": (78, 78, 82),
        "tartan": (176, 64, 48),
        "markings": (235, 235, 230),
    }.get(kind, (128, 128, 128))
    img = np.empty((px, px, 3), dtype=np.float32)
    img[...] = base
    amount = 0.07 * tiled(0.25, 3) + 0.06 * tiled(2.0, 7)
    if kind == "grass":
        amount += 0.05 * np.sin(u / tile * 2 * np.pi * 4)  # mowing stripes
    img *= 1.0 + amount[..., None]
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")


def _windings(geom) -> list[list[list[tuple[float, float]]]]:
    polys = list(getattr(geom, "geoms", [geom]))
    out = []
    for poly in polys:
        if poly.geom_type != "Polygon" or poly.is_empty:
            continue
        p = orient(poly, 1.0)  # outer counter-clockwise, holes clockwise
        rings = [list(p.exterior.coords)[:-1]] + [list(r.coords)[:-1] for r in p.interiors]
        out.append([[(x, y) for x, y in ring] for ring in rings])
    return out


# ---- package -----------------------------------------------------------------------


@dataclass
class Ortho:
    """Aerial image (north up, plain lon/lat grid) used as ground texture."""

    image: Path
    bounds: tuple[float, float, float, float]  # west, south, east, north


ORTHO_MAX_PX = 4096


ORTHO_MARGIN_M = 5.0
# Orthophotos look washed out under X-Plane 12's lighting; a mild lift helps.
ORTHO_CONTRAST = 1.12
ORTHO_SATURATION = 1.08
ORTHO_GAMMA = 1.12


def _ortho_region(ortho: Ortho, geoms) -> tuple[float, float, float, float]:
    """Bounds of the surfaces plus a small margin, within the image."""
    west, south, east, north = ortho.bounds
    w, s, e, n = unary_union(geoms).bounds
    lat = (s + n) / 2
    dx = ORTHO_MARGIN_M / meters_per_deg_lon(lat)
    dy = ORTHO_MARGIN_M / METERS_PER_DEG_LAT
    return max(west, w - dx), max(south, s - dy), min(east, e + dx), min(north, n + dy)


def _write_ortho(ortho: Ortho, polygons_dir: Path, region) -> str:
    """Crops the image to `region` (more pixels per metre than the whole image),
    lifts contrast a little, saves it at a power-of-two size (what X-Plane's
    texture loader likes) and writes the matching non-repeating .pol."""
    west, south, east, north = ortho.bounds
    rw, rs, re_, rn = region
    with Image.open(ortho.image) as src:
        img = src.convert("RGB")
    sx = img.width / (east - west)
    sy = img.height / (north - south)
    img = img.crop(
        (
            int((rw - west) * sx),
            int((north - rn) * sy),
            math.ceil((re_ - west) * sx),
            math.ceil((north - rs) * sy),
        )
    )
    img = ImageEnhance.Contrast(img).enhance(ORTHO_CONTRAST)
    img = ImageEnhance.Color(img).enhance(ORTHO_SATURATION)
    img = img.point([round(255 * (i / 255) ** ORTHO_GAMMA) for i in range(256)] * 3)

    def pot(n: int) -> int:
        return min(ORTHO_MAX_PX, 1 << max(6, math.ceil(math.log2(max(1, n)))))

    img.resize((pot(img.width), pot(img.height)), Image.Resampling.LANCZOS).save(
        polygons_dir / "xpsb_ortho.png"
    )
    lat = (rs + rn) / 2
    (polygons_dir / "xpsb_ortho.pol").write_text(
        DrapedPolygonDef(
            texture="xpsb_ortho.png",
            wrap=False,
            scale_s_m=(re_ - rw) * meters_per_deg_lon(lat),
            scale_t_m=(rn - rs) * METERS_PER_DEG_LAT,
            layer_group=SURFACE_LAYER,
            layer_offset=0,
            no_alpha=True,
        ).to_text(),
        encoding="ascii",
        newline="\n",
    )
    return "polygons/xpsb_ortho.pol"


def _ortho_uv(windings, bounds):
    west, south, east, north = bounds
    return [
        [(x, y, (x - west) / (east - west), (y - south) / (north - south)) for x, y in ring]
        for ring in windings
    ]


BLENDER_DIR = ".blender"  # temporary inputs/outputs of the Blender run


def _shade_with_blender(
    results: list[ItemResult],
    objects_dir: Path,
    blender_exe: str,
    progress: Callable[[BlenderStatus], None] | None,
    should_stop: Callable[[], bool] = lambda: False,
    cpu: bool = False,
) -> None:
    """One Blender run bakes the soft shading of all prepared objects, which is then
    multiplied into their colour textures."""
    from xpsb import blender

    todo = [r for r in results if r.blender_input]
    work = objects_dir / BLENDER_DIR
    log: list[str] = []
    problems: list[str] = []
    # Kept next to the package (not in it) to find out what went wrong.
    log_path = objects_dir.parent.parent / "blender.log"
    try:
        jobs = [
            (Path(r.blender_input), work / f"{r.stem}_ao.png", work / f"{r.stem}_detail.png")  # type: ignore[arg-type]
            for r in todo
        ]
        done = blender.bake_batch(
            blender_exe,
            jobs,
            progress or (lambda s: None),
            should_stop=should_stop,
            log=log,
            log_file=log_path,
            cpu=cpu,  # written as Blender works: can be watched while it runs
        )
        for i in sorted(done):
            res = todo[i]
            try:
                ao = blender.ao_from(jobs[i][0], jobs[i][1])
                blender.shade_texture(objects_dir / f"{res.stem}.png", ao)
                res.shaded = True
                nml = objects_dir / f"{res.stem}_nml.png"
                if nml.is_file():
                    res.detailed = blender.detail_normals(jobs[i][0], jobs[i][2], nml)
            except (OSError, ValueError) as exc:
                problems.append(f"{res.stem}: {exc}")
                continue
        if len(done) < len(todo):
            problems.append(blender.problem(log))
    finally:
        log_path.write_text("\n".join(log + problems) + "\n", encoding="utf-8")
        for r in todo:
            r.blender_input = None
            if not r.shaded and problems:
                r.blender_problem = problems[-1]
        shutil.rmtree(work, ignore_errors=True)


@dataclass
class PackageResult:
    dsf_text: Path
    objects: list[ItemResult]
    surfaces: int


def write_package(
    items: list[SceneItem],
    package: Path,
    tile: Tile,
    detail: str,
    exclusions: list[tuple[str, tuple[float, float, float, float]]],
    ortho: Ortho | None = None,
    progress: Callable[..., None] | None = None,
    library: Catalog | None = None,
    photo_dir: Path | None = None,
    blender_exe: str | None = None,
    blender_progress: Callable[[BlenderStatus], None] | None = None,
    blender_stop: Callable[[], bool] = lambda: False,
    blender_cpu: bool = False,
) -> PackageResult:
    objects_dir = package / "objects"
    polygons_dir = package / "polygons"
    results = generate_all(
        items, detail, objects_dir, False, progress, library, photo_dir, blender_exe
    )
    if blender_exe:
        _shade_with_blender(
            results, objects_dir, blender_exe, blender_progress, blender_stop, blender_cpu
        )

    dsf = DsfText(tile=tile)
    for kind, bounds in exclusions:
        dsf.add_exclusion(kind, *bounds)
    lib_defs: dict[str, int] = {}
    for res in results:
        if res.status != "ok":
            continue
        if res.has_mesh:
            idx = dsf.add_object_def(f"objects/{res.stem}.obj")
            dsf.add_object(idx, res.anchor[0], res.anchor[1], res.heading)
        # Library objects: only referenced by their virtual path (X-Plane has them);
        # they turn with the object around its anchor.
        lon0, lat0 = res.anchor
        h = math.radians(res.heading)
        for p in res.library:
            if p["path"] not in lib_defs:
                lib_defs[p["path"]] = dsf.add_object_def(p["path"])
            x = p["x"] * math.cos(h) + p["y"] * math.sin(h)
            y = -p["x"] * math.sin(h) + p["y"] * math.cos(h)
            dsf.add_object(
                lib_defs[p["path"]],
                lon0 + x / meters_per_deg_lon(lat0),
                lat0 + y / METERS_PER_DEG_LAT,
                (p["heading"] + res.heading) % 360.0,
            )

    surface_defs: dict[str, int] = {}
    surfaces = 0
    ortho_items = [i for i in items if i.kind == "surface" and i.included and not i.proposal]
    if ortho is not None and ortho_items:
        # One aerial image under every surface: they look like the real place.
        polygons_dir.mkdir(parents=True, exist_ok=True)
        region = _ortho_region(ortho, [shape(i.geometry) for i in ortho_items])
        ortho_def = dsf.add_polygon_def(_write_ortho(ortho, polygons_dir, region))
        for item in ortho_items:
            for windings in _windings(shape(item.geometry)):
                # 65535: texture coordinates come with each point (no tiling).
                dsf.add_polygon(ortho_def, 65535, _ortho_uv(windings, region))
                surfaces += 1
    for item in sorted(
        (i for i in items if i.kind == "surface" and i.included and not i.proposal),
        key=lambda i: SURFACE_ORDER.get(str(i.category), 0),
    ):
        if ortho is not None:
            break
        kind = str(item.category)
        if kind not in surface_defs:
            polygons_dir.mkdir(parents=True, exist_ok=True)
            _surface_texture(kind).save(polygons_dir / f"xpsb_{kind}.png")
            tile_m = SURFACE_TILE_M.get(kind, 16.0)
            (polygons_dir / f"xpsb_{kind}.pol").write_text(
                DrapedPolygonDef(
                    texture=f"xpsb_{kind}.png",
                    wrap=True,
                    scale_s_m=tile_m,
                    scale_t_m=tile_m,
                    layer_group=SURFACE_LAYER,
                    layer_offset=SURFACE_ORDER.get(kind, 0),
                ).to_text(),
                encoding="ascii",
                newline="\n",
            )
            surface_defs[kind] = dsf.add_polygon_def(f"polygons/xpsb_{kind}.pol")
        for windings in _windings(shape(item.geometry)):
            dsf.add_polygon(surface_defs[kind], 0, windings)
            surfaces += 1

    if not dsf.objects and not dsf.polygons:
        raise ValueError("nothing could be built")
    text_path = package.parent / f"{package.name}.dsf.txt"
    text_path.write_text(dsf.to_text(), encoding="ascii", newline="\n")
    return PackageResult(dsf_text=text_path, objects=results, surfaces=surfaces)
