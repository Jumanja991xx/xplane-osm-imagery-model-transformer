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
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import LineString, Polygon
from shapely.strtree import STRtree

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
    yaw_span: float = 360.0  # panoramas: degrees across the image (a cut-out: less)
    pitch_span: float = 180.0


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
    span = cam.yaw_span if cam.pano else cam.fov
    if span < 360.0:
        bearing = math.degrees(math.atan2(mx - cam.x, my - cam.y))
        off = (bearing - cam.heading + 180.0) % 360.0 - 180.0
        if abs(off) > span / 2:
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


def look_up(image: np.ndarray, cam: Camera, d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The photo's colours along the rays `d` (..., 3) from the camera: (RGB float,
    mask of the rays inside the picture). Panoramas are equirectangular with the
    camera's heading in the middle column and the horizon in the middle row."""
    ih, iw = image.shape[:2]
    if cam.pano:
        yaw = np.degrees(np.arctan2(d[..., 0], d[..., 1]))
        pitch = np.degrees(np.arctan2(d[..., 2], np.hypot(d[..., 0], d[..., 1])))
        rel = (yaw - cam.heading + 180.0) % 360.0 - 180.0
        px = (rel / cam.yaw_span + 0.5) * iw - 0.5
        py = (0.5 - pitch / cam.pitch_span) * ih - 0.5
        if cam.yaw_span >= 360.0:
            mask = np.abs(pitch) <= cam.pitch_span / 2
        else:
            mask = (np.abs(rel) <= cam.yaw_span / 2) & (np.abs(pitch) <= cam.pitch_span / 2)
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
    rgb = _sample(image.astype(np.float32), px, py)
    rgb[~mask] = 0
    return rgb, mask


def project(
    image: np.ndarray, wall: Wall, cam: Camera, px_per_m: float = PX_PER_M
) -> tuple[np.ndarray, np.ndarray]:
    """The wall as the photo shows it: (RGB texture, mask of texels it covers).
    `image` is (h, w, 3) uint8."""
    w_px = max(8, int(wall.length * px_per_m))
    h_px = max(8, int((wall.z1 - wall.z0) * px_per_m))
    rgb, mask = look_up(image, cam, _directions(wall, cam, w_px, h_px))
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


# ---- painting the object's texture ---------------------------------------------------


@dataclass
class Shot:
    """A street-level photo ready for painting: camera in the object's metres."""

    photo_id: str
    cam: Camera
    image: np.ndarray  # (h, w, 3) uint8


@dataclass
class Street:
    """The street photos of an object and the buildings around it (they hide walls)."""

    shots: list[Shot]
    others: list[Polygon]
    used: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self._tree = STRtree(self.others) if self.others else None

    def blocked(self, wall: Wall, cam: Camera) -> float:
        """Share of the wall hidden from the camera (by the other buildings)."""
        if self._tree is None:
            return 0.0
        box = LineString([(cam.x, cam.y), wall.a, wall.b]).envelope
        near = [self.others[i] for i in self._tree.query(box)]
        return 1.0 - unobstructed(wall, cam, near)


MAX_FIT_M = 0.15  # curved patches (domes, columns) are not painted from photos


def _surface(patch) -> np.ndarray | None:
    """Affine map from the patch's texture metres (u, v, 1) to its 3D points; None
    if the patch is not flat."""
    uv = np.array(patch.uv_m, dtype=np.float64)
    xyz = np.array(patch.positions, dtype=np.float64)
    if len(uv) < 3:
        return None
    a = np.column_stack([uv, np.ones(len(uv))])
    m, *_ = np.linalg.lstsq(a, xyz, rcond=None)
    if np.abs(a @ m - xyz).max() > MAX_FIT_M:
        return None
    return m


def _wall_of(points: np.ndarray, normal: tuple[float, float]) -> Wall:
    """The wall a flat upright patch lies in, ordered so its normal points outwards."""
    nx, ny = normal
    tx, ty = -ny, nx
    along = points[..., 0] * tx + points[..., 1] * ty
    off = float((points[..., 0] * nx + points[..., 1] * ny).mean())
    lo, hi = float(along.min()), float(along.max())
    a = (nx * off + tx * lo, ny * off + ty * lo)
    b = (nx * off + tx * hi, ny * off + ty * hi)
    return Wall(a, b, float(points[..., 2].min()), float(points[..., 2].max()))


def paint_patch(patch, uu: np.ndarray, vv: np.ndarray, painted: np.ndarray, street: Street):
    """Paints the part of an upright, flat patch a street photo shows over `painted`
    (h, w, 3, at texture metres uu/vv); returns the mask of painted texels or None."""
    if patch.shared_uv or not patch.normals:
        return None
    n = patch.normals[0]
    horiz = math.hypot(n[0], n[1])
    if horiz < 0.95:  # roofs, ledges, slopes
        return None
    m = _surface(patch)
    if m is None:
        return None
    pts = np.stack([uu, vv, np.ones_like(uu)], axis=-1) @ m
    wall = _wall_of(pts, (n[0] / horiz, n[1] / horiz))
    if wall.length < 0.3 or wall.z1 - wall.z0 < 0.3:
        return None
    best = None
    for shot in street.shots:
        s = view_score(wall, shot.cam)
        if s is None or (best is not None and s <= best[0]):
            continue
        if wall.length >= 1.0:  # small pieces (pilasters, reveals): no ray test
            s *= 1.0 - street.blocked(wall, shot.cam)
        if s > 0.15 and (best is None or s > best[0]):
            best = (s, shot)
    if best is None:
        return None
    shot = best[1]
    cam = shot.cam
    d = pts - np.array([cam.x, cam.y, cam.z])
    rgb, mask = look_up(shot.image, cam, d)
    if not mask.any():
        return None
    painted[mask] = rgb[mask]
    street.used.add(shot.photo_id)
    return mask


def load(photos, photo_dir, local, ground_z: float = 0.0) -> list[Shot]:
    """The item's street-level photos that may be shared, with their camera in the
    object's metres (`local`: lon, lat -> [x, y])."""
    from PIL import Image

    from xpsb.photos import redistributable

    out = []
    if photo_dir is None:
        return out
    for p in photos:
        c = p.camera
        path = photo_dir / f"{p.id}.jpg"
        if c is None or not redistributable(p) or not path.is_file():
            continue
        try:
            with Image.open(path) as img:
                image = np.asarray(img.convert("RGB"))
        except OSError:
            continue
        x, y = local(c.lon, c.lat)[:2]
        cam = Camera(
            x,
            y,
            c.heading,
            pano=c.pano,
            fov=c.fov or DEFAULT_FOV_DEG,
            z=ground_z + EYE_M,
            yaw_span=c.yaw_span,
            pitch_span=c.pitch_span,
        )
        out.append(Shot(p.id, cam, image))
    return out
