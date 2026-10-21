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
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from shapely.geometry import LineString, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from xpsb.geo import METERS_PER_DEG_LAT, meters_per_deg_lon
from xpsb.scene import SceneItem

DEFAULT_BRIDGE_WIDTH_M = 10.0
MARGIN_M = 3.0  # around the object itself
SHADOW_SHARE = 1.0  # shadows reach about the height sideways (sun at 45 degrees; bridges)
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


def across(it: SceneItem) -> float | None:
    """For long objects (bridges): the direction across them, in degrees from east
    counter-clockwise (in metres) - filled from what lies on both sides (the water),
    not from what lies at their ends (the banks, the roads onto them)."""
    try:
        geom = shape(it.geometry)
    except (ValueError, TypeError, AttributeError):
        return None
    if geom.geom_type != "LineString":
        return None
    mx, my = _metres(geom.centroid.y)
    (x0, y0), (x1, y1) = geom.coords[0], geom.coords[-1]
    dx, dy = (x1 - x0) * mx, (y1 - y0) * my
    if math.hypot(dx, dy) < 1e-6:
        return None
    return math.degrees(math.atan2(dy, dx)) + 90.0


def areas(items: list[SceneItem]) -> list[tuple[BaseGeometry, float | None]]:
    """The places to paint over, each with the direction to fill it along (or None)."""
    out = []
    for it in items:
        if hidden(it):
            a = area(it)
            if a is not None and not a.is_empty:
                out.append((a, across(it)))
    return out


def key(places: list[tuple[BaseGeometry, float | None]]) -> str:
    """Short fingerprint of the painted-over places (to cache the result)."""
    text = json.dumps([[round(v, 7) for v in g.bounds] + [d] for g, d in places])
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
        diff = (img - blurred)[ring]
        grain = 1.4826 * np.median(np.abs(diff - np.median(diff, axis=0)), axis=0)
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


def fill_across(rgb: np.ndarray, mask: np.ndarray, angle_deg: float, seed: int = 11) -> np.ndarray:
    """Fills a long hole from its two sides: every pixel gets the colours found
    where a line through it, along `angle_deg` (image: degrees from east,
    counter-clockwise, y up), leaves the hole on either side, blended by distance;
    then the grain of the surroundings. The hole's ends are never used."""
    h, w = mask.shape
    ys, xs = np.nonzero(mask)
    if not len(ys):
        return rgb
    dx, dy = math.cos(math.radians(angle_deg)), -math.sin(math.radians(angle_deg))
    soft = np.asarray(Image.fromarray(rgb).filter(ImageFilter.GaussianBlur(2)), np.float32)
    limit = int(math.hypot(h, w))
    found = []
    for sgn in (1, -1):
        dist = np.full(len(ys), -1.0)
        colour = np.zeros((len(ys), 3), np.float32)
        todo = np.ones(len(ys), bool)
        for k in range(1, limit):
            if not todo.any():
                break
            px = np.round(xs + sgn * dx * k).astype(int)
            py = np.round(ys + sgn * dy * k).astype(int)
            inside = (px >= 0) & (px < w) & (py >= 0) & (py < h)
            gone = todo & ~inside
            todo &= inside
            out = todo.copy()
            out[todo] = ~mask[py[todo], px[todo]]
            if out.any():
                # a few pixels further out: clear of the hole's soft edge
                qx = np.clip(np.round(xs[out] + sgn * dx * (k + 3)).astype(int), 0, w - 1)
                qy = np.clip(np.round(ys[out] + sgn * dy * (k + 3)).astype(int), 0, h - 1)
                colour[out] = soft[qy, qx]
                dist[out] = k
                todo &= ~out
            todo &= ~gone
        found.append((dist, colour))
    (d1, c1), (d2, c2) = found
    est = np.zeros((len(ys), 3), np.float32)
    both = (d1 > 0) & (d2 > 0)
    t = (d1[both] / (d1[both] + d2[both]))[:, None]
    est[both] = c1[both] * (1 - t) + c2[both] * t
    one = (d1 > 0) & ~both
    est[one] = c1[one]
    two = (d2 > 0) & ~both
    est[two] = c2[two]
    out = rgb.astype(np.float32)
    ring = _ring(mask)
    grain = np.zeros(3, np.float32)
    if ring.any():  # robust: the hole's own edges (deck ends, shadow) do not count
        diff = (out - soft)[ring]
        grain = 1.4826 * np.median(np.abs(diff - np.median(diff, axis=0)), axis=0)
    noise = np.random.default_rng(seed).normal(0, 1, (len(ys), 3)).astype(np.float32)
    out[ys, xs] = est + noise * grain[None, :]
    edge = (
        np.asarray(
            Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2)),
            np.float32,
        )[..., None]
        / 255.0
    )
    edge = np.maximum(edge, mask[..., None])
    return np.clip(rgb * (1 - edge) + out * edge, 0, 255).astype(np.uint8)


def apply(
    image: Image.Image, bounds, places: list[tuple[BaseGeometry, float | None]]
) -> Image.Image:
    """The image with `places` (lon/lat, each with its fill direction or None)
    painted over."""
    if not places:
        return image
    rgb = np.asarray(image.convert("RGB"))
    # Long objects first, each from its own sides; the rest from all around.
    for geom, angle in places:
        if angle is None:
            continue
        mask = _mask(image.size, bounds, [geom])
        if mask.any():
            rgb = fill_across(rgb, mask, angle)
    rest = [g for g, a in places if a is None]
    if rest:
        mask = _mask(image.size, bounds, [unary_union(rest)])
        if mask.any():
            rgb = fill(rgb, mask)
    return Image.fromarray(rgb)
