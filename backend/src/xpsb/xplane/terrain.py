"""The ground as X-Plane itself draws it.

An object stands on X-Plane's terrain at its origin; everything else of it is at
fixed heights above that point. A bridge whose origin is over the river must
know how much higher the banks are, or its deck ends in the air or in the bank.
The heights are read from the terrain mesh X-Plane uses for the tile: the first
enabled pack in Custom Scenery with its own mesh (ortho, HD mesh), else the
Global Scenery. DSFTool turns that DSF into text; the triangles around the
drawn area are kept (cached per DSF and area, the conversion takes a while).
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable
from pathlib import Path

import numpy as np

from xpsb.xplane import dsftool
from xpsb.xplane.dsf_text import Tile
from xpsb.xplane.neighbors import for_tile

MARGIN_DEG = 0.004  # ~400 m around the area: approach roads reach beyond it
CELL_DEG = 0.002  # lookup grid for the triangles


class TerrainError(RuntimeError):
    pass


def mesh_dsf(xplane_path: str | None, tile: Tile, own: str | None = None) -> Path | None:
    """The DSF whose terrain X-Plane draws for this tile."""
    if not xplane_path:
        return None
    root = Path(xplane_path)
    for pack in for_tile(xplane_path, tile, own):
        if pack.enabled and pack.kind in ("ortho", "mesh"):
            dsf = root / "Custom Scenery" / pack.name / tile.dsf_relative_path
            if dsf.is_file():
                return dsf
    for glob in sorted((root / "Global Scenery").glob("*")):
        dsf = glob / tile.dsf_relative_path
        if dsf.is_file():
            return dsf
    return None


def triangles_from_text(lines: Iterable[str], bbox) -> np.ndarray:
    """Terrain triangles (N, 3, 3: lon, lat, elevation) of a DSF text with at least
    one corner inside bbox (west, south, east, north)."""
    w, s, e, n = bbox
    out: list = []
    prim = None
    verts: list = []

    def flush() -> None:
        if prim is None or len(verts) < 3:
            return
        if prim == 0:
            tris = [verts[i : i + 3] for i in range(0, len(verts) - 2, 3)]
        elif prim == 1:  # strip
            tris = [
                (verts[i], verts[i + 1], verts[i + 2])
                if i % 2 == 0
                else (verts[i + 1], verts[i], verts[i + 2])
                for i in range(len(verts) - 2)
            ]
        else:  # fan
            tris = [(verts[0], verts[i], verts[i + 1]) for i in range(1, len(verts) - 1)]
        for t in tris:
            if any(w <= v[0] <= e and s <= v[1] <= n for v in t):
                out.append(t)

    for line in lines:
        if line.startswith("PATCH_VERTEX"):
            if prim is not None:
                parts = line.split(None, 4)
                verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif line.startswith("BEGIN_PRIMITIVE"):
            prim = int(line.split()[1])
            verts = []
        elif line.startswith("END_PRIMITIVE"):
            flush()
            prim = None
            verts = []
    return np.array(out, dtype=np.float64).reshape(-1, 3, 3)


class Terrain:
    """Heights of the ground (metres above sea level) from terrain triangles."""

    def __init__(self, tris: np.ndarray):
        self.tris = tris
        self.cells: dict[tuple[int, int], list[int]] = {}
        for i, t in enumerate(tris):
            x0, x1 = t[:, 0].min(), t[:, 0].max()
            y0, y1 = t[:, 1].min(), t[:, 1].max()
            for cx in range(math.floor(x0 / CELL_DEG), math.floor(x1 / CELL_DEG) + 1):
                for cy in range(math.floor(y0 / CELL_DEG), math.floor(y1 / CELL_DEG) + 1):
                    self.cells.setdefault((cx, cy), []).append(i)

    def height(self, lon: float, lat: float) -> float | None:
        key = (math.floor(lon / CELL_DEG), math.floor(lat / CELL_DEG))
        for i in self.cells.get(key, []):
            (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = self.tris[i]
            det = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
            if abs(det) < 1e-18:
                continue
            a = ((y2 - y3) * (lon - x3) + (x3 - x2) * (lat - y3)) / det
            b = ((y3 - y1) * (lon - x3) + (x1 - x3) * (lat - y3)) / det
            c = 1 - a - b
            if min(a, b, c) >= -1e-9:
                return float(a * z1 + b * z2 + c * z3)
        return None


def load(
    xplane_path: str | None,
    dsftool_exe: Path | None,
    tile: Tile,
    bbox,
    cache_dir: Path,
    own: str | None = None,
) -> Terrain | None:
    """X-Plane's terrain around `bbox` (west, south, east, north), or None if X-Plane
    or DSFTool is not set up. Raises TerrainError if reading the DSF failed."""
    dsf = mesh_dsf(xplane_path, tile, own)
    if dsf is None or dsftool_exe is None:
        return None
    w, s, e, n = bbox
    area = (w - MARGIN_DEG, s - MARGIN_DEG, e + MARGIN_DEG, n + MARGIN_DEG)
    stat = dsf.stat()
    key = hashlib.sha1(
        f"{dsf}|{stat.st_size}|{stat.st_mtime_ns}|{[round(v, 4) for v in area]}".encode()
    ).hexdigest()[:20]
    cached = cache_dir / f"{key}.npy"
    if cached.is_file():
        return Terrain(np.load(cached))
    text = dsftool.ascii_work_root() / f"terrain-{key}.txt"
    try:
        try:
            dsftool.dsf2text(dsftool_exe, dsf, text)
        except dsftool.DsfToolError as exc:
            raise TerrainError(str(exc)[:300]) from exc
        with text.open(encoding="utf-8", errors="replace") as f:
            tris = triangles_from_text(f, area)
    finally:
        text.unlink(missing_ok=True)
    if not len(tris):
        raise TerrainError(f"no terrain around the area in {dsf.name}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    np.save(cached, tris)
    return Terrain(tris)


GRID_STEP_M = 20.0


class Grid:
    """Ground heights on a regular grid (kept with the job: the build needs no DSF)."""

    def __init__(self, bbox, nx: int, ny: int, heights: list[float | None]):
        self.bbox = tuple(bbox)
        self.nx, self.ny = nx, ny
        self.h = heights  # row by row from the south-west corner

    @classmethod
    def sample(cls, ground: Terrain, bbox, step_m: float = GRID_STEP_M) -> Grid:
        from xpsb.geo import METERS_PER_DEG_LAT, meters_per_deg_lon

        w, s, e, n = bbox
        mx = meters_per_deg_lon((s + n) / 2)
        nx = max(2, math.ceil((e - w) * mx / step_m) + 1)
        ny = max(2, math.ceil((n - s) * METERS_PER_DEG_LAT / step_m) + 1)
        heights = [
            ground.height(w + (e - w) * i / (nx - 1), s + (n - s) * j / (ny - 1))
            for j in range(ny)
            for i in range(nx)
        ]
        return cls(bbox, nx, ny, heights)

    def height(self, lon: float, lat: float) -> float | None:
        w, s, e, n = self.bbox
        fx = (lon - w) / (e - w) * (self.nx - 1)
        fy = (lat - s) / (n - s) * (self.ny - 1)
        fx = min(max(fx, 0.0), self.nx - 1.0)
        fy = min(max(fy, 0.0), self.ny - 1.0)
        i, j = min(int(fx), self.nx - 2), min(int(fy), self.ny - 2)
        tx, ty = fx - i, fy - j
        corners = [
            (self.h[j * self.nx + i], (1 - tx) * (1 - ty)),
            (self.h[j * self.nx + i + 1], tx * (1 - ty)),
            (self.h[(j + 1) * self.nx + i], (1 - tx) * ty),
            (self.h[(j + 1) * self.nx + i + 1], tx * ty),
        ]
        known = [(v, wgt) for v, wgt in corners if v is not None]
        total = sum(wgt for _, wgt in known)
        if not known or total <= 1e-9:
            return next((v for v, _ in known), None)
        return sum(v * wgt for v, wgt in known) / total

    def to_json(self) -> dict:
        return {
            "bbox": list(self.bbox),
            "nx": self.nx,
            "ny": self.ny,
            "h": [None if v is None else round(v, 2) for v in self.h],
        }

    @classmethod
    def from_json(cls, data: dict) -> Grid:
        return cls(data["bbox"], data["nx"], data["ny"], data["h"])
