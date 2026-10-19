"""Small software renderer: shaded views of a mesh as PNG, so the AI architect can
check its own result. Faces are culled like in X-Plane (one-sided unless marked
two-sided), so a face turned the wrong way is missing here too.
"""

from __future__ import annotations

import io
import math
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw

from xpsb.build.materials import MATERIALS
from xpsb.build.mesh import Mesh

SKY = (205, 222, 238)
GROUND = (150, 162, 128)
OUTLINE = (220, 30, 30)

# (name, azimuth the camera looks from in degrees clockwise from north, elevation)
VIEWS = (
    ("view from south-west", 225.0, 28.0),
    ("view from north-east", 45.0, 28.0),
    ("top view, north up", 0.0, 90.0),
)


@lru_cache(maxsize=256)
def _base_color(material: str, color: tuple[int, int, int] | None) -> np.ndarray:
    u, v = np.meshgrid(np.linspace(0, 4, 24), np.linspace(0, 4, 24))
    img = MATERIALS.get(material, MATERIALS["concrete"])(u, v, color)
    return np.clip(img.reshape(-1, 3).mean(axis=0), 0, 255)


def _camera(azimuth: float, elevation: float):
    """Orthographic camera axes: right, up, forward (into the scene)."""
    az, el = math.radians(azimuth), math.radians(elevation)
    # direction from the scene towards the camera
    back = np.array([math.sin(az) * math.cos(el), math.cos(az) * math.cos(el), math.sin(el)])
    forward = -back
    if elevation >= 89.9:
        right, up = np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])
    else:
        right = np.cross(forward, [0.0, 0.0, 1.0])
        right /= np.linalg.norm(right)
        up = np.cross(right, forward)
    return right, up, forward


LIBRARY_COLOR = np.array((235, 140, 40), dtype=np.float32)


def _placement_boxes(
    mesh: Mesh, sizes: dict
) -> list[tuple[np.ndarray, list[tuple[int, int, int]]]]:
    """X-Plane library objects as simple boxes (their real look is X-Plane's)."""
    out = []
    for p in mesh.placements:
        w, d, h = (sizes.get(p.path) or (1.0, 1.0, 2.0))[:3]
        a = math.radians(p.heading)
        ux, uy = math.sin(a), math.cos(a)  # forward
        vx, vy = uy, -ux  # right
        corners = []
        for z in (0.0, max(h, 0.2)):
            for s, t in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                corners.append(
                    [
                        p.x + vx * s * w / 2 + ux * t * d / 2,
                        p.y + vy * s * w / 2 + uy * t * d / 2,
                        z,
                    ]
                )
        quads = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
        tris = [t for q in quads for t in ((q[0], q[1], q[2]), (q[0], q[2], q[3]))]
        out.append((np.array(corners, dtype=np.float64), tris))
    return out


def render(
    mesh: Mesh,
    azimuth: float,
    elevation: float,
    size: int = 512,
    footprint: list[list[float]] | None = None,
    sizes: dict | None = None,
) -> Image.Image:
    right, up, forward = _camera(azimuth, elevation)
    light = np.array([-0.45, -0.35, 0.82])
    light /= np.linalg.norm(light)

    boxes = _placement_boxes(mesh, sizes or {})
    pts = np.array(
        [p for patch in mesh.patches for p in patch.positions]
        + [c for corners, _ in boxes for c in corners.tolist()],
        dtype=np.float64,
    )
    extra = np.array([[x, y, 0.0] for x, y in footprint]) if footprint else np.zeros((0, 3))
    allp = np.vstack([pts, extra]) if len(extra) else pts
    sx, sy = allp @ right, allp @ up
    span = max(sx.max() - sx.min(), sy.max() - sy.min(), 1.0) * 1.12
    cx, cy = (sx.max() + sx.min()) / 2, (sy.max() + sy.min()) / 2
    scale = size / span

    def project(p: np.ndarray) -> np.ndarray:
        return np.stack(
            [(p @ right - cx) * scale + size / 2, size / 2 - (p @ up - cy) * scale, p @ forward],
            axis=-1,
        )

    img = np.empty((size, size, 3), dtype=np.float32)
    img[...] = SKY
    depth = np.full((size, size), np.inf)

    # Ground plane under the object (helps to read heights in oblique views).
    if elevation < 89.9:
        g = 0.5 * span / max(abs(right[0]) + abs(right[1]), 0.3)
        mx, my = pts[:, 0].mean(), pts[:, 1].mean()
        quad = np.array(
            [[mx - g, my - g, 0], [mx + g, my - g, 0], [mx + g, my + g, 0], [mx - g, my + g, 0]]
        )
        q = project(quad)
        for tri in ((0, 1, 2), (0, 2, 3)):
            _raster(img, depth, q[list(tri)], np.array(GROUND, dtype=np.float32), bias=1e-3)

    for patch in mesh.patches:
        base = _base_color(patch.material, patch.color)
        p = project(np.array(patch.positions, dtype=np.float64))
        normals = np.array(patch.normals, dtype=np.float64)
        for a, b, c in patch.tris:
            n = normals[a] + normals[b] + normals[c]
            n /= np.linalg.norm(n) or 1.0
            facing = n @ forward
            if facing > 0:  # seen from behind
                if not patch.two_sided:
                    continue
                n = -n
            shade = 0.38 + 0.62 * max(0.0, float(n @ light))
            _raster(img, depth, p[[a, b, c]], base * shade)

    for corners, tris in boxes:  # two-sided, flat orange: "library object here"
        q = project(corners)
        for t in tris:
            _raster(img, depth, q[list(t)], LIBRARY_COLOR)

    out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")
    if footprint:
        draw = ImageDraw.Draw(out)
        ring = project(np.array([[x, y, 0.05] for x, y in footprint + footprint[:1]]))
        draw.line([(float(x), float(y)) for x, y, _ in ring], fill=OUTLINE, width=2)
    return out


def _raster(img, depth, tri: np.ndarray, color: np.ndarray, bias: float = 0.0) -> None:
    (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = tri
    h, w = depth.shape
    xmin, xmax = max(int(min(x0, x1, x2)), 0), min(int(max(x0, x1, x2)) + 1, w)
    ymin, ymax = max(int(min(y0, y1, y2)), 0), min(int(max(y0, y1, y2)) + 1, h)
    if xmin >= xmax or ymin >= ymax:
        return
    area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    if abs(area) < 1e-9:
        return
    xs, ys = np.meshgrid(np.arange(xmin, xmax) + 0.5, np.arange(ymin, ymax) + 0.5)
    w0 = ((x1 - xs) * (y2 - ys) - (x2 - xs) * (y1 - ys)) / area
    w1 = ((x2 - xs) * (y0 - ys) - (x0 - xs) * (y2 - ys)) / area
    w2 = 1.0 - w0 - w1
    inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
    if not inside.any():
        return
    z = w0 * z0 + w1 * z1 + w2 * z2 + bias
    region = depth[ymin:ymax, xmin:xmax]
    closer = inside & (z < region)
    region[closer] = z[closer]
    img[ymin:ymax, xmin:xmax][closer] = color


def views_png(
    mesh: Mesh, footprint: list[list[float]] | None, size: int = 512, sizes: dict | None = None
) -> list[tuple[str, bytes]]:
    """The standard views as PNG bytes with their names (library objects: orange boxes)."""
    out = []
    for name, az, el in VIEWS:
        buf = io.BytesIO()
        render(mesh, az, el, size, footprint, sizes).save(buf, format="PNG")
        out.append((name, buf.getvalue()))
    return out
