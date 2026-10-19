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

from xpsb.build.materials import DEFAULT_GLOSS, MATERIALS, normal_map
from xpsb.build.mesh import Mesh, Patch

PAD_PX = 6  # room for mipmaps: neighbours must not bleed into each other
MIN_PX_PER_M = 3.0


@dataclass
class BakedMesh:
    image: Image.Image
    px_per_m: float
    # Per patch: list of (s, t) for every vertex, t=0 at the bottom of the image.
    uvs: list[list[tuple[float, float]]]
    # Same layout: normals (RGB) and gloss (alpha) for X-Plane 12's lighting.
    normal: Image.Image | None = None


def _shelf_pack(
    sizes: list[tuple[int, int]], atlas: int, width: int | None = None
) -> list[tuple[int, int]] | None:
    """Simple shelf packing, tallest first, into `width` x `atlas` pixels. Returns
    positions or None if it does not fit."""
    width = width or atlas
    order = sorted(range(len(sizes)), key=lambda i: -sizes[i][1])
    pos: list[tuple[int, int] | None] = [None] * len(sizes)
    x = y = shelf_h = 0
    for i in order:
        w, h = sizes[i]
        if w > width or h > atlas:
            return None
        if x + w > width:
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
        positions = _compact_pack(sizes, atlas_px)
        if positions is not None:
            break
        density *= 0.9
        if density < MIN_PX_PER_M:
            raise ValueError("object too large for its texture; split it into parts")

    # Only as big as needed (powers of two): small objects get small textures.
    aw = _pot(max(x + w for (x, _), (w, _) in zip(positions, sizes, strict=True)))
    ah = _pot(max(y + h for (_, y), (_, h) in zip(positions, sizes, strict=True)))
    canvas = np.zeros((ah, aw, 3), dtype=np.float32)
    canvas[...] = (128, 128, 128)
    normals = np.zeros((ah, aw, 4), dtype=np.float32)
    normals[...] = (127.5, 127.5, 255.0, DEFAULT_GLOSS * 255.0)
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
        normals[y : y + h, x : x + w] = normal_map(
            np.clip(painted, 0, 255), patch.material, density, uu, vv
        )
        uvs.append(_patch_uvs(patch, u0, v1, x, y, density, aw, ah))

    image = Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8), "RGB")
    normal = Image.fromarray(np.clip(normals, 0, 255).astype(np.uint8), "RGBA")
    return BakedMesh(image=image, px_per_m=density, uvs=uvs, normal=normal)


def _compact_pack(sizes: list[tuple[int, int]], atlas: int) -> list[tuple[int, int]] | None:
    """Narrowest power-of-two width whose packing is not taller than wide: a small,
    roughly square texture instead of a long strip."""
    width = 64
    while width < atlas:
        pos = _shelf_pack(sizes, atlas, width)
        if (
            pos is not None
            and max(y + h for (_, y), (_, h) in zip(pos, sizes, strict=True)) <= width
        ):
            return pos
        width *= 2
    return _shelf_pack(sizes, atlas)


def _pot(n: int) -> int:
    size = 64
    while size < n:
        size *= 2
    return size


def _patch_uvs(
    patch: Patch, u0: float, v1: float, x: int, y: int, density: float, aw: int, ah: int
) -> list[tuple[float, float]]:
    out = []
    for u, v in patch.uv_m:
        px = x + PAD_PX + (u - u0) * density
        py = y + PAD_PX + (v1 - v) * density
        out.append((px / aw, 1.0 - py / ah))
    return out
