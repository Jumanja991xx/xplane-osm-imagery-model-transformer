"""XYZ tile sources (slippy map tiles in Web Mercator), entered by the user.

The tiles are stitched and resampled row by row into the same lon/lat grid the
WMS path produces, so everything downstream (crops, AI, overlay) stays the same.
"""

from __future__ import annotations

import io
import math
import os
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image

from xpsb.imagery.sources import ImagerySource
from xpsb.imagery.wms import MAX_SIDE_PX, Bounds, WmsError, _http_get, service_message

TILE = 256
MAX_TILES = 600  # politeness: one job never pulls more than this from a server
EARTH_CIRCUMFERENCE_M = 40_075_016.686


def _merc_x(lon: float, z: int) -> float:
    return (lon + 180.0) / 360.0 * (2**z) * TILE


def _merc_y(lat: float, z: int) -> float:
    s = math.sin(math.radians(lat))
    return (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * (2**z) * TILE


def choose_zoom(source: ImagerySource, lat: float, width_m: float, height_m: float) -> int:
    """Finest zoom up to max_zoom that stays within the tile and image size limits."""
    for z in range(source.max_zoom, 0, -1):
        m_per_px = EARTH_CIRCUMFERENCE_M * math.cos(math.radians(lat)) / (TILE * 2**z)
        w_px, h_px = width_m / m_per_px, height_m / m_per_px
        tiles = (w_px / TILE + 2) * (h_px / TILE + 2)
        if tiles <= MAX_TILES and max(w_px, h_px) <= MAX_SIDE_PX:
            return z
    return 1


def tile_url(template: str, z: int, x: int, y: int) -> str:
    return (
        template.replace("{z}", str(z))
        .replace("{x}", str(x))
        .replace("{-y}", str(2**z - 1 - y))
        .replace("{y}", str(y))
    )


def _get_tile(source: ImagerySource, z: int, x: int, y: int) -> Image.Image:
    fixtures = os.environ.get("XPSB_FIXTURES_DIR")
    if fixtures:
        return _fixture_tile(Path(fixtures), z, x, y)
    data, ctype = _http_get(tile_url(source.url, z, x, y), timeout=60)
    try:
        return Image.open(io.BytesIO(data)).convert("RGB")
    except OSError as exc:
        raise WmsError(f"tile {z}/{x}/{y}: {service_message(data) or ctype}") from exc


def _tile_bounds(z: int, x: int, y: int) -> Bounds:
    n = 2**z

    def lat(yy: float) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))

    return Bounds(x / n * 360 - 180, lat(y + 1), (x + 1) / n * 360 - 180, lat(y))


def _fixture_tile(fixtures: Path, z: int, x: int, y: int) -> Image.Image:
    from xpsb.imagery.wms import _fixture_map

    # Small tiles: lon/lat is close enough to linear inside one tile for tests.
    return _fixture_map(fixtures, _tile_bounds(z, x, y), TILE, TILE)


def fetch_mosaic(
    source: ImagerySource, b: Bounds, progress: Callable[[float], None] | None = None
) -> tuple[Image.Image, float]:
    """Image of `b` on a regular lon/lat grid, and its ground resolution in metres."""
    lat_mid = (b.south + b.north) / 2
    w_m, h_m = b.size_m()
    z = choose_zoom(source, lat_mid, w_m, h_m)
    x0, x1 = _merc_x(b.west, z), _merc_x(b.east, z)
    y0, y1 = _merc_y(b.north, z), _merc_y(b.south, z)
    tx0, tx1 = int(x0 // TILE), int(x1 // TILE)
    ty0, ty1 = int(y0 // TILE), int(y1 // TILE)
    count = (tx1 - tx0 + 1) * (ty1 - ty0 + 1)
    if count > MAX_TILES:
        raise WmsError(f"area needs {count} tiles (max {MAX_TILES})")
    mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * TILE, (ty1 - ty0 + 1) * TILE))
    done = 0
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            mosaic.paste(_get_tile(source, z, tx, ty), ((tx - tx0) * TILE, (ty - ty0) * TILE))
            done += 1
            if progress:
                progress(done / count)

    # Resample into a lon/lat grid: columns are linear in both, rows need the
    # Mercator y of each output latitude.
    gsd = EARTH_CIRCUMFERENCE_M * math.cos(math.radians(lat_mid)) / (TILE * 2**z)
    width = max(1, round(w_m / gsd))
    height = max(1, round(h_m / gsd))
    src = np.asarray(mosaic)
    cols = np.clip(
        (np.linspace(x0, x1, width, endpoint=False) + (x1 - x0) / width / 2 - tx0 * TILE).astype(
            int
        ),
        0,
        src.shape[1] - 1,
    )
    lats = b.north - (np.arange(height) + 0.5) * (b.north - b.south) / height
    rows = np.clip(
        np.array([_merc_y(float(la), z) - ty0 * TILE for la in lats]).astype(int),
        0,
        src.shape[0] - 1,
    )
    out = src[rows][:, cols]
    return Image.fromarray(np.ascontiguousarray(out), "RGB"), gsd
