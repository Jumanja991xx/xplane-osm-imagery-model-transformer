"""Turns the confirmed scene list into 3D objects, ground surfaces and a DSF.

Used twice: for the 3D preview when the plan is made (job folder only) and for
the real package build after approval C. Generation is deterministic, so the
build produces exactly what the preview showed.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from shapely.geometry import shape
from shapely.geometry.polygon import orient

from xpsb.build.bake import bake
from xpsb.build.objexport import to_obj8, to_preview
from xpsb.build.sandbox import ScriptError, ScriptInputs, run_script
from xpsb.build.scripts import DEFAULT_SCRIPTS
from xpsb.geo import METERS_PER_DEG_LAT, meters_per_deg_lon
from xpsb.scene import SceneItem
from xpsb.xplane.dsf_text import DsfText, Tile
from xpsb.xplane.pol import DrapedPolygonDef

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


# ---- local frames ----------------------------------------------------------------


def _anchor(item: SceneItem) -> tuple[float, float]:
    geom = shape(item.geometry)
    p = (
        geom.centroid
        if geom.geom_type in ("Polygon", "MultiPolygon")
        else (geom.representative_point())
    )
    return p.x, p.y


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
    if geom.geom_type == "Polygon":
        ring = list(orient(geom, 1.0).exterior.coords)[:-1]
        footprint = [local(x, y) for x, y in ring]
    elif geom.geom_type == "LineString":
        path = [local(x, y) for x, y in geom.coords]
    return ScriptInputs(footprint=footprint, path=path, params=_params(item, items, local, detail))


def _params(item: SceneItem, items: list[SceneItem], local, detail: str) -> dict:
    a = item.attrs
    summary = (item.ai.summary if item.ai else "").lower()
    material = (a.material or "").lower()
    solar = any(w in summary or w in material for w in SOLAR_WORDS)
    return {
        "height": a.height_m,
        "levels": a.levels,
        "roof_shape": a.roof_shape,
        "roof_color": a.roof_color,
        "wall_color": a.wall_color,
        "seat_color": a.seat_color,
        "covered": a.covered,
        "material": a.material,
        "roof_material": "solar" if solar else None,
        "facing": _facing(item, items, local),
        "detail": detail,
        "name": item.name,
    }


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


def generate(item: SceneItem, items: list[SceneItem], detail: str):
    """Runs the item's build script. Returns (mesh, baked) or raises ScriptError."""
    script = DEFAULT_SCRIPTS.get(str(item.category))
    if script is None:
        raise LookupError("no generator yet")
    budget, atlas, max_ppm = DETAIL.get(detail, DETAIL["medium"])
    inputs = _script_inputs(item, items, detail)
    if inputs.footprint is None and str(item.category) != "masts":
        raise LookupError("needs an area outline")
    mesh = run_script(script, inputs, budget, name=f"{item.category}.star")
    return mesh, bake(mesh, atlas, max_ppm)


def _lod(mesh) -> float:
    (x0, y0, z0), (x1, y1, z1) = mesh.bounds()
    size = max(x1 - x0, y1 - y0, z1 - z0)
    return min(8000.0, max(1500.0, size * 40))


def generate_all(
    items: list[SceneItem],
    detail: str,
    out_dir: Path,
    preview: bool,
    progress: Callable[[float], None] | None = None,
) -> list[ItemResult]:
    """Writes OBJ8 + PNG (package) or preview JSON + PNG (job folder) per object."""
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = [it for it in items if buildable(it)]
    results: list[ItemResult] = []
    for n, item in enumerate(todo, start=1):
        stem = f"xpsb_obj_{n:03d}"
        res = ItemResult(item.id, stem, str(item.category), item.name, _anchor(item), "ok")
        try:
            mesh, baked = generate(item, items, detail)
        except LookupError as exc:
            res.status, res.message = "skipped", str(exc)
        except (ScriptError, ValueError) as exc:
            res.status, res.message = "error", str(exc)[:2000]
        else:
            res.triangles = mesh.triangle_count
            baked.image.save(out_dir / f"{stem}.png", optimize=False)
            if preview:
                data = to_preview(mesh, baked)
                (out_dir / f"{stem}.json").write_text(json.dumps(data), encoding="utf-8")
            else:
                obj = to_obj8(mesh, baked, f"{stem}.png", _lod(mesh))
                obj.comment = f"xpsb {item.category} {item.id}"
                (out_dir / f"{stem}.obj").write_text(obj.to_text(), encoding="ascii", newline="\n")
        results.append(res)
        if progress:
            progress(n / max(1, len(todo)))
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
    progress: Callable[[float], None] | None = None,
) -> PackageResult:
    objects_dir = package / "objects"
    polygons_dir = package / "polygons"
    results = generate_all(items, detail, objects_dir, preview=False, progress=progress)

    dsf = DsfText(tile=tile)
    for kind, bounds in exclusions:
        dsf.add_exclusion(kind, *bounds)
    for res in results:
        if res.status != "ok":
            continue
        idx = dsf.add_object_def(f"objects/{res.stem}.obj")
        dsf.add_object(idx, res.anchor[0], res.anchor[1], 0.0)

    surface_defs: dict[str, int] = {}
    surfaces = 0
    for item in sorted(
        (i for i in items if i.kind == "surface" and i.included and not i.proposal),
        key=lambda i: SURFACE_ORDER.get(str(i.category), 0),
    ):
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
