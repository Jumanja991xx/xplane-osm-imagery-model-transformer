"""Packs all patches of a mesh into one texture atlas, paints their materials at
real-world scale and computes the texture coordinates.

One texture per object (an OBJ8 has exactly one). Later the same atlas regions
take projected photos instead of procedural paint.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image

from xpsb.build.materials import MATERIALS
from xpsb.build.mesh import Mesh, Patch

PAD_PX = 6  # room for mipmaps: neighbours must not bleed into each other
MIN_PX_PER_M = 3.0


@dataclass
class BakedMesh:
    image: Image.Image
    px_per_m: float
    # Per patch: list of (s, t) for every vertex, t=0 at the bottom of the image.
    uvs: list[list[tuple[float, float]]]


def _shelf_pack(sizes: list[tuple[int, int]], atlas: int) -> list[tuple[int, int]] | None:
    """Simple shelf packing, tallest first. Returns positions or None if it does not fit."""
    order = sorted(range(len(sizes)), key=lambda i: -sizes[i][1])
    pos: list[tuple[int, int] | None] = [None] * len(sizes)
    x = y = shelf_h = 0
    for i in order:
        w, h = sizes[i]
        if w > atlas or h > atlas:
            return None
        if x + w > atlas:
            x, y = 0, y + shelf_h
            shelf_h = 0
        if y + h > atlas:
            return None
        pos[i] = (x, y)
        x += w
        shelf_h = max(shelf_h, h)
    return pos  # type: ignore[return-value]


def bake(mesh: Mesh, atlas_px: int = 2048, max_px_per_m: float = 24.0) -> BakedMesh:
    bounds = [p.uv_bounds() for p in mesh.patches]
    extents = [(max(0.05, u1 - u0), max(0.05, v1 - v0)) for u0, v0, u1, v1 in bounds]
    area = sum(w * h for w, h in extents)
    longest = max(max(w, h) for w, h in extents)
    density = min(
        max_px_per_m, math.sqrt(0.7 * atlas_px * atlas_px / area), (atlas_px - 2 * PAD_PX) / longest
    )
    while True:
        sizes = [
            (math.ceil(w * density) + 2 * PAD_PX, math.ceil(h * density) + 2 * PAD_PX)
            for w, h in extents
        ]
        positions = _shelf_pack(sizes, atlas_px)
        if positions is not None:
            break
        density *= 0.9
        if density < MIN_PX_PER_M:
            raise ValueError("object too large for its texture; split it into parts")

    canvas = np.zeros((atlas_px, atlas_px, 3), dtype=np.float32)
    canvas[...] = (128, 128, 128)
    uvs: list[list[tuple[float, float]]] = []
    for patch, (u0, _v0, _u1, v1), (x, y), (w, h) in zip(
        mesh.patches, bounds, positions, sizes, strict=True
    ):
        # Paint the whole padded rectangle so texture filtering never bleeds in grey.
        px = np.arange(w, dtype=np.float32) - PAD_PX + 0.5
        py = np.arange(h, dtype=np.float32) - PAD_PX + 0.5
        uu = u0 + px[None, :] / density + np.zeros((h, 1), dtype=np.float32)
        vv = v1 - py[:, None] / density + np.zeros((1, w), dtype=np.float32)
        painted = MATERIALS[patch.material](uu, vv, patch.color)
        canvas[y : y + h, x : x + w] = painted
        uvs.append(_patch_uvs(patch, u0, v1, x, y, density, atlas_px))

    image = Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8), "RGB")
    return BakedMesh(image=image, px_per_m=density, uvs=uvs)


def _patch_uvs(
    patch: Patch, u0: float, v1: float, x: int, y: int, density: float, atlas: int
) -> list[tuple[float, float]]:
    out = []
    for u, v in patch.uv_m:
        px = x + PAD_PX + (u - u0) * density
        py = y + PAD_PX + (v1 - v) * density
        out.append((px / atlas, 1.0 - py / atlas))
    return out
