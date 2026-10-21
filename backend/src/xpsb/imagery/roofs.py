"""Roof colours from the aerial image.

Without a colour every roof gets the same red tiles; from above, a city has red,
brown, grey and copper-green roofs. The colour is the median of the pixels inside
the roof outline (a metre in from the edge, so walls and gutters do not count),
which the build script then uses as the roof's base colour. Only buildings without
a colour from OSM, the AI or the user are coloured, and only from images that may
go into the package (not from recognition-only sources).
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from xpsb.geo import METERS_PER_DEG_LAT, meters_per_deg_lon
from xpsb.imagery.wms import ImageryInfo
from xpsb.scene import SceneItem

INSET_M = 1.0
MIN_PIXELS = 12


def _pixels(img: np.ndarray, info: ImageryInfo, geom: BaseGeometry) -> np.ndarray:
    lat = geom.centroid.y
    inset = INSET_M / ((meters_per_deg_lon(lat) + METERS_PER_DEG_LAT) / 2)
    area = geom.buffer(-inset)
    if area.is_empty:
        area = geom
    sx = info.width_px / (info.east - info.west)
    sy = info.height_px / (info.north - info.south)
    w, s, e, n = area.bounds  # only the roof's own corner of the image
    x0 = max(0, int((w - info.west) * sx))
    y0 = max(0, int((info.north - n) * sy))
    x1 = min(info.width_px, int((e - info.west) * sx) + 2)
    y1 = min(info.height_px, int((info.north - s) * sy) + 2)
    if x1 <= x0 or y1 <= y0:
        return img[:0, 0]

    def px(coords):
        return [((x - info.west) * sx - x0, (info.north - y) * sy - y0) for x, y in coords]

    mask = Image.new("L", (x1 - x0, y1 - y0), 0)
    draw = ImageDraw.Draw(mask)
    for poly in getattr(area, "geoms", [area]):
        if poly.geom_type != "Polygon":
            continue
        draw.polygon(px(poly.exterior.coords), fill=255)
        for hole in poly.interiors:
            draw.polygon(px(hole.coords), fill=0)
    return img[y0:y1, x0:x1][np.asarray(mask) > 0]


def roof_color(img: np.ndarray, info: ImageryInfo, geom: BaseGeometry) -> str | None:
    """#rrggbb of the roof inside `geom` (lon/lat), or None if too few pixels."""
    px = _pixels(img, info, geom)
    if len(px) < MIN_PIXELS:
        return None
    # Pixels in shadow or glare (dormers, chimneys, skylights) say little about the
    # roofing: the middle half by brightness.
    light = px.sum(axis=1)
    lo, hi = np.percentile(light, [25, 75])
    mid = px[(light >= lo) & (light <= hi)]
    r, g, b = (int(v) for v in np.median(mid if len(mid) else px, axis=0))
    return f"#{r:02x}{g:02x}{b:02x}"


def measure(items: list[SceneItem], image: Image.Image, info: ImageryInfo) -> dict[str, str]:
    """Roof colour per building id as the aerial image shows it (without setting it)."""
    if not info.package_ok:
        return {}
    img = np.asarray(image.convert("RGB"))
    if img.shape[1] != info.width_px or img.shape[0] != info.height_px:
        return {}
    out = {}
    for it in items:
        geom = _building(it)
        if geom is not None:
            color = roof_color(img, info, geom)
            if color is not None:
                out[it.id] = color
    return out


def _building(it: SceneItem):
    if it.kind != "object" or str(it.category) != "buildings" or not it.geometry:
        return None
    try:
        geom = shape(it.geometry)
    except (ValueError, TypeError, AttributeError):
        return None
    return geom if geom.geom_type in ("Polygon", "MultiPolygon") else None


def apply(items: list[SceneItem], image: Image.Image, info: ImageryInfo) -> int:
    """Sets the roof colour of buildings (and their parts) that have none.
    Returns how many got one."""
    if not info.package_ok:
        return 0
    img = np.asarray(image.convert("RGB"))
    if img.shape[1] != info.width_px or img.shape[0] != info.height_px:
        return 0
    count = 0
    for it in items:
        geom = _building(it)
        if geom is None:
            continue
        if it.attrs.roof_color is None:
            it.attrs.roof_color = roof_color(img, info, geom)
            count += it.attrs.roof_color is not None
        parts = [(p, shape(p.geometry)) for p in it.parts if p.geometry]
        for part, pgeom in parts:
            if part.roof_color is not None or part.part == "base":
                continue
            # From above only the part's own roof shows where no higher part covers it.
            top = part.height_m or 0
            seen = pgeom
            for other, ogeom in parts:
                if other is not part and (other.height_m or 0) > top:
                    seen = seen.difference(ogeom)
            if not seen.is_empty and seen.area > 0.3 * pgeom.area:
                part.roof_color = roof_color(img, info, seen)
    return count
