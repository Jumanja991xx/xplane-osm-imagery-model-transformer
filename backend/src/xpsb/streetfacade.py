"""Facades from street-level photos: for each wall of a building the photo that looks
at it best (in front of it, not too far, not too slanted, nothing in between), then
the wall painted from it - every texel of the wall looks up its camera ray in the
photo. Works for 360° panoramas (equirectangular, most of Panoramax) and ordinary
photos with a known direction and field of view.

Everything here is in the object's local metres (x east, y north, z up), with the
camera's position, height and compass heading. Where the photo does not show the
wall (outside it, behind the camera) the mask is off and the drawn facade stays.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from shapely.geometry import LineString, Polygon

EYE_M = 2.5  # camera height above the street (car roof or a person)
MIN_WALL_M = 4.0  # shorter walls are not worth a photo of their own
MIN_DIST_M = 6.0
MAX_DIST_M = 40.0
MAX_ANGLE_DEG = 55.0  # between the wall's normal and the line of sight
DEFAULT_FOV_DEG = 70.0  # ordinary photos without a field of view
PX_PER_M = 24.0  # texture resolution on the wall


@dataclass
class Wall:
    a: tuple[float, float]
    b: tuple[float, float]
    z0: float
    z1: float

    @property
    def length(self) -> float:
        return math.dist(self.a, self.b)

    @property
    def normal(self) -> tuple[float, float]:
        """Outward normal of a counter-clockwise outline."""
        dx, dy = self.b[0] - self.a[0], self.b[1] - self.a[1]
        n = math.hypot(dx, dy) or 1.0
        return (dy / n, -dx / n)

    @property
    def middle(self) -> tuple[float, float, float]:
        return ((self.a[0] + self.b[0]) / 2, (self.a[1] + self.b[1]) / 2, (self.z0 + self.z1) / 2)


@dataclass
class Camera:
    x: float
    y: float
    heading: float  # compass degrees the image centre looks at
    pano: bool = False
    fov: float = DEFAULT_FOV_DEG  # horizontal, ordinary photos
    z: float = EYE_M


def walls_of(footprint: list[list[float]], z0: float, z1: float) -> list[Wall]:
    """The walls of a counter-clockwise outline long enough for a photo."""
    out = []
    n = len(footprint)
    for i in range(n):
        a, b = footprint[i], footprint[(i + 1) % n]
        w = Wall((a[0], a[1]), (b[0], b[1]), z0, z1)
        if w.length >= MIN_WALL_M:
            out.append(w)
    return out


def view_score(wall: Wall, cam: Camera) -> float | None:
    """How well the camera sees the wall (higher is better), None if not at all:
    in front of it, at a sensible distance, not too slanted, and - for ordinary
    photos - the wall in the picture."""
    mx, my, _ = wall.middle
    nx, ny = wall.normal
    dx, dy = cam.x - mx, cam.y - my
    dist = math.hypot(dx, dy)
    front = dx * nx + dy * ny  # how far in front of the wall's plane
    if front < MIN_DIST_M * 0.5 or not MIN_DIST_M <= dist <= MAX_DIST_M:
        return None
    angle = math.degrees(math.acos(max(-1.0, min(1.0, front / dist))))
    if angle > MAX_ANGLE_DEG:
        return None
    if not cam.pano:
        bearing = math.degrees(math.atan2(mx - cam.x, my - cam.y))
        off = (bearing - cam.heading + 180.0) % 360.0 - 180.0
        if abs(off) > cam.fov / 2:
            return None
    # straight on and close is best; far and slanted photos smear the facade
    return math.cos(math.radians(angle)) * (1.0 - dist / (MAX_DIST_M * 1.5))


def unobstructed(wall: Wall, cam: Camera, others: list[Polygon], samples: int = 5) -> float:
    """Share of the wall the camera sees past the other buildings (on the ground plan)."""
    if not others:
        return 1.0
    seen = 0
    for k in range(samples):
        t = (k + 0.5) / samples
        px = wall.a[0] + (wall.b[0] - wall.a[0]) * t
        py = wall.a[1] + (wall.b[1] - wall.a[1]) * t
        nx, ny = wall.normal
        ray = LineString([(cam.x, cam.y), (px + nx * 0.3, py + ny * 0.3)])
        if not any(ray.intersects(o) for o in others):
            seen += 1
    return seen / samples


def _directions(wall: Wall, cam: Camera, w_px: int, h_px: int) -> np.ndarray:
    """Rays from the camera to the centre of each texel of the wall (h, w, 3)."""
    u = (np.arange(w_px) + 0.5) / w_px
    v = (np.arange(h_px) + 0.5) / h_px
    uu, vv = np.meshgrid(u, v)  # row 0 = top of the wall
    x = wall.a[0] + (wall.b[0] - wall.a[0]) * uu
    y = wall.a[1] + (wall.b[1] - wall.a[1]) * uu
    z = wall.z1 - (wall.z1 - wall.z0) * vv
    return np.stack([x - cam.x, y - cam.y, z - cam.z], axis=-1)


def _sample(img: np.ndarray, px: np.ndarray, py: np.ndarray) -> np.ndarray:
    """Bilinear lookup; px may wrap around (panoramas), py is clamped."""
    h, w = img.shape[:2]
    x0 = np.floor(px).astype(np.int64)
    y0 = np.clip(np.floor(py).astype(np.int64), 0, h - 1)
    fx = (px - np.floor(px))[..., None]
    fy = (py - np.floor(py))[..., None]
    x1 = (x0 + 1) % w
    x0 = x0 % w
    y1 = np.clip(y0 + 1, 0, h - 1)
    top = img[y0, x0] * (1 - fx) + img[y0, x1] * fx
    bottom = img[y1, x0] * (1 - fx) + img[y1, x1] * fx
    return top * (1 - fy) + bottom * fy


def project(
    image: np.ndarray, wall: Wall, cam: Camera, px_per_m: float = PX_PER_M
) -> tuple[np.ndarray, np.ndarray]:
    """The wall as the photo shows it: (RGB texture, mask of texels it covers).
    `image` is (h, w, 3) uint8; panoramas are equirectangular with the camera's
    heading in the middle column and the horizon in the middle row."""
    w_px = max(8, int(wall.length * px_per_m))
    h_px = max(8, int((wall.z1 - wall.z0) * px_per_m))
    d = _directions(wall, cam, w_px, h_px)
    ih, iw = image.shape[:2]
    img = image.astype(np.float32)
    if cam.pano:
        yaw = np.degrees(np.arctan2(d[..., 0], d[..., 1]))
        pitch = np.degrees(np.arctan2(d[..., 2], np.hypot(d[..., 0], d[..., 1])))
        rel = (yaw - cam.heading + 180.0) % 360.0 - 180.0
        px = (rel / 360.0 + 0.5) * iw - 0.5
        py = (0.5 - pitch / 180.0) * ih - 0.5
        mask = np.ones(px.shape, dtype=bool)
    else:
        h = math.radians(cam.heading)
        fwd = np.array([math.sin(h), math.cos(h), 0.0])
        right = np.array([math.cos(h), -math.sin(h), 0.0])
        up = np.array([0.0, 0.0, 1.0])
        zc = d @ fwd
        xc = d @ right
        yc = d @ up
        f = (iw / 2) / math.tan(math.radians(cam.fov) / 2)
        with np.errstate(divide="ignore", invalid="ignore"):
            px = iw / 2 + f * xc / zc - 0.5
            py = ih / 2 - f * yc / zc - 0.5
        mask = (zc > 0.1) & (px >= 0) & (px <= iw - 1) & (py >= 0) & (py <= ih - 1)
        px = np.where(mask, px, 0.0)
        py = np.where(mask, py, 0.0)
    rgb = _sample(img, px, py)
    rgb[~mask] = 0
    return np.clip(rgb, 0, 255).astype(np.uint8), mask


@dataclass
class Choice:
    wall: int  # index into the walls
    photo: int  # index into the cameras
    score: float


def choose(walls: list[Wall], cams: list[Camera], others: list[Polygon]) -> list[Choice]:
    """For each wall the photo that shows it best (walls nothing sees are left out)."""
    out = []
    for i, wall in enumerate(walls):
        best: Choice | None = None
        for j, cam in enumerate(cams):
            s = view_score(wall, cam)
            if s is None:
                continue
            s *= unobstructed(wall, cam, others)
            if s > 0.15 and (best is None or s > best.score):
                best = Choice(i, j, s)
        if best is not None:
            out.append(best)
    return out
