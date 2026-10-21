"""Paints real objects out of the aerial ground image.

Under a modelled bridge the aerial image still shows the real bridge and its
shadow on the water: seen from the air there are two bridges. The places under
such objects (and a margin for the shadow) are filled from their surroundings -
water from the water around it, a bank from the bank - with the grain of the
surroundings, so no smooth blot shows.
"""

from __future__ import annotations

import hashlib
import json

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from shapely.geometry import LineString, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from xpsb.geo import METERS_PER_DEG_LAT, meters_per_deg_lon
from xpsb.scene import SceneItem

DEFAULT_BRIDGE_WIDTH_M = 10.0
MARGIN_M = 2.0  # around the object itself
SHADOW_SHARE = 0.6  # shadows reach about this share of the height sideways (bridges)
BUILDING_SHADOW_SHARE = 0.35


def hidden(it: SceneItem) -> bool:
    """Whether the real object is painted out under the model."""
    if not it.included or it.proposal or it.kind != "object":
        return False
    if it.attrs.hide_below is not None:
        return it.attrs.hide_below
    return str(it.category) == "bridges"


def _metres(lat: float) -> tuple[float, float]:
    return meters_per_deg_lon(lat), METERS_PER_DEG_LAT


def area(it: SceneItem) -> BaseGeometry | None:
    """The ground to paint over for one object (lon/lat), or None."""
    try:
        geom = shape(it.geometry)
    except (ValueError, TypeError, AttributeError):
        return None
    if geom.is_empty:
        return None
    lat = geom.centroid.y
    mx, my = _metres(lat)
    height = it.attrs.height_m or (8.0 if str(it.category) == "bridges" else 6.0)
    share = SHADOW_SHARE if str(it.category) == "bridges" else BUILDING_SHADOW_SHARE
    margin = MARGIN_M + share * height
    if geom.geom_type in ("LineString", "MultiLineString"):
        margin += (it.attrs.width_m or DEFAULT_BRIDGE_WIDTH_M) / 2
    elif geom.geom_type == "Point":
        margin += 2.0
    # Buffer in metres (the degrees of longitude are shorter than those of latitude).
    local = _scale(geom, mx, my)
    return _scale(
        local.buffer(margin, cap_style="flat" if isinstance(geom, LineString) else "round"),
        1 / mx,
        1 / my,
    )


def _scale(geom: BaseGeometry, sx: float, sy: float) -> BaseGeometry:
    from shapely.affinity import scale

    return scale(geom, xfact=sx, yfact=sy, origin=(0, 0))


def areas(items: list[SceneItem]) -> list[BaseGeometry]:
    out = []
    for it in items:
        if hidden(it):
            a = area(it)
            if a is not None and not a.is_empty:
                out.append(a)
    return out


def key(geoms: list[BaseGeometry]) -> str:
    """Short fingerprint of the painted-over places (to cache the result)."""
    text = json.dumps([round(v, 7) for g in geoms for v in g.bounds])
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def _mask(size: tuple[int, int], bounds, geoms: list[BaseGeometry]) -> np.ndarray:
    west, south, east, north = bounds
    w, h = size
    sx, sy = w / (east - west), h / (north - south)
    img = Image.new("L", size, 0)
    draw = ImageDraw.Draw(img)
    for geom in geoms:
        for poly in getattr(geom, "geoms", [geom]):
            if poly.geom_type != "Polygon":
                continue
            draw.polygon(
                [((x - west) * sx, (north - y) * sy) for x, y in poly.exterior.coords], fill=255
            )
            for hole in poly.interiors:
                draw.polygon([((x - west) * sx, (north - y) * sy) for x, y in hole.coords], fill=0)
    return np.asarray(img) > 0


def fill(rgb: np.ndarray, mask: np.ndarray, seed: int = 7) -> np.ndarray:
    """Fills the masked pixels from their surroundings (push-pull: averages of the
    known pixels on ever coarser levels, brought back down), then adds the grain
    of the surroundings."""
    img = rgb.astype(np.float32)
    known = (~mask).astype(np.float32)
    levels = [(img * known[..., None], known)]
    while min(levels[-1][1].shape) > 2:
        c, k = levels[-1]
        h, w = k.shape
        h2, w2 = (h + 1) // 2, (w + 1) // 2
        cp = np.zeros((h2 * 2, w2 * 2, 3), np.float32)
        kp = np.zeros((h2 * 2, w2 * 2), np.float32)
        cp[:h, :w], kp[:h, :w] = c, k
        c2 = cp.reshape(h2, 2, w2, 2, 3).sum(axis=(1, 3))
        k2 = kp.reshape(h2, 2, w2, 2).sum(axis=(1, 3))
        levels.append((c2, k2))
    c, k = levels[-1]
    est = c / np.maximum(k, 1e-6)[..., None]
    for c, k in reversed(levels[:-1]):
        h, w = k.shape
        up = np.repeat(np.repeat(est, 2, axis=0), 2, axis=1)[:h, :w]
        up = np.asarray(
            Image.fromarray(np.clip(up, 0, 255).astype(np.uint8)).filter(ImageFilter.BoxBlur(1)),
            dtype=np.float32,
        )
        here = c / np.maximum(k, 1e-6)[..., None]
        weight = np.clip(k, 0, 1)[..., None]
        est = here * weight + up * (1 - weight)
    out = img.copy()
    # The grain of the ring around the hole, so the fill is not a smooth blot.
    ring = _ring(mask)
    if ring.any():
        blurred = np.asarray(Image.fromarray(rgb).filter(ImageFilter.GaussianBlur(2)), np.float32)
        grain = (img - blurred)[ring].std(axis=0)
        noise = np.random.default_rng(seed).normal(0, 1, mask.shape + (3,)).astype(np.float32)
        noise = np.asarray(
            Image.fromarray(np.clip(noise * 40 + 128, 0, 255).astype(np.uint8)).filter(
                ImageFilter.GaussianBlur(0.8)
            ),
            np.float32,
        )
        noise = (noise - 128) / 40 * 1.6
        est = est + noise * grain[None, None, :]
    # Soft edge: two pixels of blending into the untouched image.
    soft = (
        np.asarray(
            Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.5)),
            np.float32,
        )[..., None]
        / 255.0
    )
    soft = np.maximum(soft, mask[..., None])
    out = out * (1 - soft) + est * soft
    return np.clip(out, 0, 255).astype(np.uint8)


def _ring(mask: np.ndarray, width: int = 6) -> np.ndarray:
    grown = Image.fromarray((mask * 255).astype(np.uint8)).filter(
        ImageFilter.MaxFilter(2 * width + 1)
    )
    return (np.asarray(grown) > 0) & ~mask


def apply(image: Image.Image, bounds, geoms: list[BaseGeometry]) -> Image.Image:
    """The image with the places in `geoms` (lon/lat) painted over."""
    if not geoms:
        return image
    rgb = np.asarray(image.convert("RGB"))
    mask = _mask(image.size, bounds, [unary_union(geoms)])
    if not mask.any():
        return image
    return Image.fromarray(fill(rgb, mask))
