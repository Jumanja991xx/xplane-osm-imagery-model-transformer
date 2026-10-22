"""Photo textures: a facade (or a sample of a repeating facade) cut out of a photo
by its four corners, straightened ("rectified") to a flat rectangle of known size
in metres, and painted into the object's texture atlas like any material.

This is how hand-made sceneries get their look: simple shapes, real photos.
Photos go into the package only when their licence allows it (own photos with
confirmed rights, CC0/public domain, CC BY, CC BY-SA, Etalab/open licence); the
authors are named in the package's README.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from xpsb.photos import Photo, redistributable  # noqa: F401 (re-exported)
from xpsb.scene import PhotoTexture

MAX_TEXTURE_PX = 2048
MAX_PX_PER_M = 48.0


def attribution(photo: Photo) -> str:
    """One README line for a photo used as texture."""
    if photo.source == "user":
        return f"- {photo.title or photo.id}: own photo of the package author"
    parts = [photo.title or photo.id]
    if photo.author:
        parts.append(f"by {photo.author}")
    if photo.license:
        parts.append(f"licence {photo.license}")
    if photo.page_url:
        parts.append(photo.page_url)
    return "- " + ", ".join(parts)


def _homography(src: list[tuple[float, float]], dst: list[tuple[float, float]]) -> np.ndarray:
    """3x3 matrix mapping the four dst points onto the four src points."""
    a, b = [], []
    for (x, y), (u, v) in zip(dst, src, strict=True):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b += [u, v]
    try:
        h = np.linalg.solve(np.array(a, dtype=np.float64), np.array(b, dtype=np.float64))
    except np.linalg.LinAlgError as exc:
        raise ValueError("the four corners do not form a usable quadrilateral") from exc
    return np.append(h, 1.0).reshape(3, 3)


def _valid_quad(pts: list[tuple[float, float]]) -> bool:
    """Convex, non-degenerate, corners in order (TL, TR, BR, BL in image coordinates)."""
    signs = []
    for i in range(4):
        (x0, y0), (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % 4], pts[(i + 2) % 4]
        signs.append((x1 - x0) * (y2 - y1) - (y1 - y0) * (x2 - x1))
    return all(s > 1e-6 for s in signs) or all(s < -1e-6 for s in signs)


def rectify(photo_path: Path, tex: PhotoTexture) -> Image.Image:
    """The marked quadrilateral of the photo as a straight rectangle, sized
    width_m x height_m at up to MAX_PX_PER_M (never more than the photo holds)."""
    with Image.open(photo_path) as src:
        img = src.convert("RGB")
    w, h = img.size
    corners = [(fx * w, fy * h) for fx, fy in tex.corners]
    if not _valid_quad(corners):
        raise ValueError("the four corners must be in order and form a convex shape")
    # Resolution: what the photo offers along the marked edges, capped.
    top = np.hypot(*np.subtract(corners[1], corners[0]))
    left = np.hypot(*np.subtract(corners[3], corners[0]))
    ppm = min(MAX_PX_PER_M, max(top / tex.width_m, left / tex.height_m))
    ow = int(max(8, min(MAX_TEXTURE_PX, round(tex.width_m * ppm))))
    oh = int(max(8, min(MAX_TEXTURE_PX, round(tex.height_m * ppm))))
    target = [(0.0, 0.0), (ow, 0.0), (ow, oh), (0.0, oh)]
    m = _homography(corners, target)
    coeffs = tuple((m / m[2, 2]).flatten()[:8])
    return img.transform((ow, oh), Image.Transform.PERSPECTIVE, coeffs, Image.Resampling.BICUBIC)


@dataclass
class PhotoMaterial:
    """A rectified photo ready for painting: pixels and their size in metres."""

    pixels: np.ndarray  # float32 H x W x 3, row 0 = top
    width_m: float
    height_m: float
    repeat: bool


def load(
    textures: list[PhotoTexture], photos: list[Photo], photo_dir: Path | None
) -> dict[str, PhotoMaterial]:
    """Rectified photo materials by name ("photo:<name>" in scripts); photos that
    are missing or may not be shared are left out."""
    out: dict[str, PhotoMaterial] = {}
    if photo_dir is None:
        return out
    by_id = {p.id: p for p in photos}
    for tex in textures:
        photo = by_id.get(tex.photo_id)
        path = photo_dir / f"{tex.photo_id}.jpg"
        if photo is None or not redistributable(photo) or not path.is_file():
            continue
        try:
            img = rectify(path, tex)
        except (ValueError, OSError):
            continue
        out[tex.name] = PhotoMaterial(
            np.asarray(img, dtype=np.float32), tex.width_m, tex.height_m, tex.repeat
        )
    return out


def paint(mat: PhotoMaterial, u: np.ndarray, v: np.ndarray, bounds) -> np.ndarray:
    """Samples the photo at surface coordinates in metres. Repeating samples tile at
    their real size; single facades are fitted onto the whole face (`bounds` =
    u0, v0, u1, v1 of the patch)."""
    h, w = mat.pixels.shape[:2]
    if mat.repeat:
        fu = (u / mat.width_m) % 1.0
        fv = (v / mat.height_m) % 1.0
    else:
        u0, v0, u1, v1 = bounds
        fu = np.clip((u - u0) / max(1e-6, u1 - u0), 0, 1)
        fv = np.clip((v - v0) / max(1e-6, v1 - v0), 0, 1)
    px = np.clip((fu * w).astype(np.int64), 0, w - 1)
    py = np.clip(((1.0 - fv) * h).astype(np.int64), 0, h - 1)  # v up, rows down
    return mat.pixels[py, px]


def night(rgb: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Lit windows guessed from the photo: dark, colourless spots (window glass in
    daylight) light up in some 'rooms' (cells of 4 m x 3.5 m)."""
    from xpsb.build.materials import ROOM_LIGHT, _lit_cells

    lum = rgb[..., 0] * 0.3 + rgb[..., 1] * 0.59 + rgb[..., 2] * 0.11
    sat = rgb.max(axis=-1) - rgb.min(axis=-1)
    dark = (lum < max(20.0, np.percentile(lum, 22))) & (sat < 60)
    cells = np.floor(u / 4.0) * 7919 + np.floor(v / 3.5)
    on = dark & _lit_cells(cells, 11, 0.3)
    out = np.zeros(rgb.shape, dtype=np.float32)
    out[on] = ROOM_LIGHT * 0.8
    return out
