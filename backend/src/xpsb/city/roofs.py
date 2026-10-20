"""The roof image of a block, seen from above (the roof object maps it by x/y).

With an aerial photo that may go into the package (open government imagery) the
roofs show the real thing - solar panels, skylights, weathering. Otherwise each
roof face is painted in its material, with the courses running across the
slope, in the colour measured on the aerial image.
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw

from xpsb.build.materials import _noise
from xpsb.city.mesh import RoofFace

M_PER_PX = 0.15
MAX_PX = 2048


def size_px(bounds: tuple[float, float, float, float]) -> tuple[int, int]:
    x0, y0, x1, y1 = bounds

    def pot(m: float) -> int:
        n = 64
        while n < m / M_PER_PX and n < MAX_PX:
            n *= 2
        return n

    return pot(x1 - x0), pot(y1 - y0)


def _material(face: RoofFace, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """u along the eaves, v up the slope (metres)."""
    base = np.array(face.color, dtype=np.float32)
    img = np.empty(u.shape + (3,), dtype=np.float32)
    img[...] = base
    if face.material in ("tiles", "slate"):
        row_h = 0.34 if face.material == "tiles" else 0.22
        tile_w = 0.30 if face.material == "tiles" else 0.25
        row = np.floor(v / row_h)
        col = np.floor((u + (row % 2) * tile_w / 2) / tile_w)
        img *= 1.0 + 0.08 * _noise(col, row, 1.0, 3)[..., None]
        img *= 1.0 + 0.04 * _noise(u, v, 3.0, 7)[..., None]  # weathering patches
        edge = (v % row_h) / row_h
        img *= (0.78 + 0.22 * np.clip(edge * 3, 0, 1))[..., None]  # shadow under each course
    elif face.material in ("metal", "copper"):
        seam = (u % 0.55) < 0.05
        img *= 1.0 + 0.03 * _noise(u, v, 2.0, 5)[..., None]
        img[seam] *= 1.12
    elif face.material == "green":
        img *= 1.0 + 0.15 * _noise(u, v, 0.3, 11)[..., None]
    else:  # gravel / membrane
        img *= (
            1.0 + 0.06 * _noise(u, v, 0.08, 13)[..., None] + 0.05 * _noise(u, v, 2.5, 17)[..., None]
        )
    return img


def paint(
    bounds: tuple[float, float, float, float],
    faces: list[RoofFace],
    ortho: Image.Image | None = None,
) -> Image.Image:
    """`ortho`: aerial photo already cropped to `bounds` (north up)."""
    w, h = size_px(bounds)
    x0, y0, x1, y1 = bounds
    if ortho is not None:
        return ortho.convert("RGB").resize((w, h), Image.Resampling.LANCZOS)
    canvas = np.zeros((h, w, 3), dtype=np.float32)
    canvas[...] = (120, 120, 116)
    sx, sy = w / (x1 - x0), h / (y1 - y0)
    for face in faces:
        pts = [((x - x0) * sx, (y1 - y) * sy) for x, y in face.outline]
        px0 = max(0, int(min(p[0] for p in pts)) - 1)
        py0 = max(0, int(min(p[1] for p in pts)) - 1)
        px1 = min(w, int(max(p[0] for p in pts)) + 2)
        py1 = min(h, int(max(p[1] for p in pts)) + 2)
        if px1 <= px0 or py1 <= py0:
            continue
        mask_img = Image.new("L", (px1 - px0, py1 - py0), 0)
        ImageDraw.Draw(mask_img).polygon([(x - px0, y - py0) for x, y in pts], fill=255)
        mask = np.asarray(mask_img) > 127
        if not mask.any():
            continue
        gx, gy = np.meshgrid(np.arange(px0, px1) + 0.5, np.arange(py0, py1) + 0.5)
        X = x0 + gx / sx
        Y = y1 - gy / sy
        nx, ny, nz = face.normal
        hl = math.hypot(nx, ny)
        if hl < 1e-3:  # flat: courses along x
            u, v = X, Y
        else:
            dx, dy = nx / hl, ny / hl  # downhill
            u = X * -dy + Y * dx
            # distance up the slope (horizontal distance / cos of the pitch)
            v = -(X * dx + Y * dy) / max(nz, 0.2)
        img = _material(face, u, v)
        canvas[py0:py1, px0:px1][mask] = img[mask]
    return Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8), "RGB")
