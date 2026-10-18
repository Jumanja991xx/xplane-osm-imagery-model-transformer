"""Meshes made of *patches*: groups of triangles that share one material and one
flat 2D surface parameterisation in metres (used to paint their texture).

Local frame: metres, x east, y north, z up; triangles counter-clockwise seen
from the front. Conversion to X-Plane's axes and winding happens on export.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

Vec3 = tuple[float, float, float]
Vec2 = tuple[float, float]


class GeometryError(ValueError):
    """A build script asked for something impossible (shown to the AI to fix)."""


def sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def norm(a: Vec3) -> Vec3:
    length = math.sqrt(dot(a, a))
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return (a[0] / length, a[1] / length, a[2] / length)


@dataclass
class Patch:
    material: str
    color: tuple[int, int, int] | None
    positions: list[Vec3] = field(default_factory=list)
    normals: list[Vec3] = field(default_factory=list)
    uv_m: list[Vec2] = field(default_factory=list)
    tris: list[tuple[int, int, int]] = field(default_factory=list)
    two_sided: bool = False

    def add_vertex(self, p: Vec3, n: Vec3, uv: Vec2) -> int:
        self.positions.append(p)
        self.normals.append(n)
        self.uv_m.append(uv)
        return len(self.positions) - 1

    @property
    def triangle_count(self) -> int:
        return len(self.tris) * (2 if self.two_sided else 1)

    def uv_bounds(self) -> tuple[float, float, float, float]:
        us = [u for u, _ in self.uv_m]
        vs = [v for _, v in self.uv_m]
        return min(us), min(vs), max(us), max(vs)


@dataclass
class Mesh:
    max_triangles: int
    patches: list[Patch] = field(default_factory=list)

    @property
    def triangle_count(self) -> int:
        return sum(p.triangle_count for p in self.patches)

    def add(self, patch: Patch) -> None:
        if not patch.tris:
            return
        for p in patch.positions:
            if not all(math.isfinite(c) for c in p):
                raise GeometryError("geometry contains NaN/inf coordinates")
        if self.triangle_count + patch.triangle_count > self.max_triangles:
            raise GeometryError(
                f"triangle budget exceeded ({self.max_triangles}); use fewer segments or parts"
            )
        self.patches.append(patch)

    def bounds(self) -> tuple[Vec3, Vec3]:
        pts = [p for patch in self.patches for p in patch.positions]
        if not pts:
            raise GeometryError("the script produced no geometry")
        return (
            (min(p[0] for p in pts), min(p[1] for p in pts), min(p[2] for p in pts)),
            (max(p[0] for p in pts), max(p[1] for p in pts), max(p[2] for p in pts)),
        )


# ---- planar faces --------------------------------------------------------------


def plane_basis(normal: Vec3) -> tuple[Vec3, Vec3]:
    """Two in-plane axes (u, v) with u × v = normal; v points 'up' where possible."""
    n = norm(normal)
    if abs(n[2]) > 0.9:  # roughly horizontal face: u east
        u = norm((1.0 - n[0] * n[0], -n[0] * n[1], -n[0] * n[2]))
    else:  # wall: u horizontal along the face, v up
        u = norm(cross((0.0, 0.0, 1.0), n))
    v = cross(n, u)
    return u, v


def _triangulate_2d(pts: list[Vec2]) -> list[tuple[int, int, int]]:
    """Constrained triangulation of a simple polygon; triangles counter-clockwise."""
    from shapely import constrained_delaunay_triangles
    from shapely.geometry import Polygon

    poly = Polygon(pts)
    if not poly.is_valid or poly.area <= 1e-9:
        raise GeometryError("face polygon is self-intersecting or has no area")
    index = {(round(x, 6), round(y, 6)): i for i, (x, y) in enumerate(pts)}
    tris = []
    for tri in constrained_delaunay_triangles(poly).geoms:
        coords = list(tri.exterior.coords)[:3]
        try:
            ids = [index[(round(x, 6), round(y, 6))] for x, y in coords]
        except KeyError:
            continue
        a, b, c = (pts[i] for i in ids)
        area2 = (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])
        if abs(area2) < 1e-12:
            continue
        tris.append((ids[0], ids[1], ids[2]) if area2 > 0 else (ids[0], ids[2], ids[1]))
    return tris


def planar_face(
    points: list[Vec3], material: str, color, normal: Vec3 | None = None, two_sided: bool = False
) -> Patch:
    """Polygon face. Without `normal` the winding of `points` decides the front."""
    if len(points) >= 2 and points[0] == points[-1]:
        points = points[:-1]
    if len(points) < 3:
        raise GeometryError("a face needs at least 3 points")
    if normal is None:
        # Newell's method: robust normal of a planar polygon from its winding.
        nx = ny = nz = 0.0
        for (x1, y1, z1), (x2, y2, z2) in zip(points, points[1:] + points[:1], strict=True):
            nx += (y1 - y2) * (z1 + z2)
            ny += (z1 - z2) * (x1 + x2)
            nz += (x1 - x2) * (y1 + y2)
        normal = (nx, ny, nz)
    n = norm(normal)
    u_ax, v_ax = plane_basis(n)
    o = points[0]
    pts2 = [(dot(sub(p, o), u_ax), dot(sub(p, o), v_ax)) for p in points]
    patch = Patch(material, color, two_sided=two_sided)
    for p, uv in zip(points, pts2, strict=True):
        patch.add_vertex(p, n, uv)
    patch.tris = _triangulate_2d(pts2)
    return patch


def quad_face(corners: list[Vec3], material: str, color, two_sided: bool = False) -> Patch:
    """Four corners counter-clockwise seen from the front (need not be exactly planar)."""
    a, b, c, d = corners
    n = norm(cross(sub(b, a), sub(d, a)))
    u_ax = norm(sub(b, a))
    v_ax = cross(n, u_ax)
    patch = Patch(material, color, two_sided=two_sided)
    for p in corners:
        patch.add_vertex(p, n, (dot(sub(p, a), u_ax), dot(sub(p, a), v_ax)))
    patch.tris = [(0, 1, 2), (0, 2, 3)]
    return patch
