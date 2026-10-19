"""The building tools a build script can call. Everything is in local metres
(x east, y north, z up, origin at the object's anchor on the ground).

These functions are the whole "API" of the sandbox: scripts cannot do anything
else than add geometry through them.
"""

from __future__ import annotations

import math
from typing import Any

from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

from xpsb.build.materials import MATERIALS, parse_color
from xpsb.build.mesh import (
    MAX_PLACEMENTS,
    GeometryError,
    Mesh,
    Patch,
    Placement,
    Vec2,
    Vec3,
    cross,
    norm,
    planar_face,
    quad_face,
    sub,
)

MAX_SEGMENTS = 64
MAX_PATH_POINTS = 2000
MAX_SIZE_M = 2000.0


def _num(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeometryError(f"{name} must be a number")
    if not math.isfinite(value) or abs(value) > MAX_SIZE_M:
        raise GeometryError(f"{name} out of range: {value}")
    return float(value)


def _pts2(value: Any, name: str, min_len: int = 3) -> list[Vec2]:
    if not isinstance(value, list) or len(value) < min_len:
        raise GeometryError(f"{name} must be a list of at least {min_len} [x, y] points")
    pts = []
    for p in value:
        if not isinstance(p, list) or len(p) < 2:
            raise GeometryError(f"{name}: every point must be [x, y]")
        pts.append((_num(p[0], name), _num(p[1], name)))
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return pts


def _pts3(value: Any, name: str, min_len: int = 2) -> list[Vec3]:
    if not isinstance(value, list) or len(value) < min_len:
        raise GeometryError(f"{name} must be a list of at least {min_len} [x, y, z] points")
    if len(value) > MAX_PATH_POINTS:
        raise GeometryError(f"{name} has too many points (max {MAX_PATH_POINTS})")
    pts = []
    for p in value:
        if not isinstance(p, list) or len(p) < 3:
            raise GeometryError(f"{name}: every point must be [x, y, z]")
        pts.append((_num(p[0], name), _num(p[1], name), _num(p[2], name)))
    return pts


def _material(mat: Any) -> str:
    if mat not in MATERIALS:
        raise GeometryError(f"unknown material {mat!r}; use one of: {', '.join(sorted(MATERIALS))}")
    return mat


def _segments(value: Any) -> int:
    n = int(_num(value, "segments"))
    if not 3 <= n <= MAX_SEGMENTS:
        raise GeometryError(f"segments must be between 3 and {MAX_SEGMENTS}")
    return n


class Tools:
    """Bound to one mesh; the methods below are exposed to the script."""

    def __init__(self, mesh: Mesh, library: frozenset[str] | None = None):
        self.mesh = mesh
        self.library = library or frozenset()

    # -- helpers ------------------------------------------------------------------

    def _color(self, color: Any):
        if color is None:
            return None
        try:
            return parse_color(color)
        except ValueError as exc:
            raise GeometryError(str(exc)) from None

    # -- X-Plane library ----------------------------------------------------------

    def place(self, path, x, y, heading=0.0):
        """Places an object of the X-Plane library (virtual path from the list) with
        its origin at (x, y) on the ground; heading in degrees from north, clockwise.
        Costs no triangles of the budget; at most 300 per object."""
        if not isinstance(path, str) or path not in self.library:
            raise GeometryError(f"place: {path!r} is not in the X-Plane library list")
        if len(self.mesh.placements) >= MAX_PLACEMENTS:
            raise GeometryError(f"place: more than {MAX_PLACEMENTS} library objects")
        self.mesh.placements.append(
            Placement(path, _num(x, "x"), _num(y, "y"), _num(heading, "heading") % 360.0)
        )

    # -- primitives -------------------------------------------------------------

    def extrude(
        self,
        polygon,
        z0,
        z1,
        mat="concrete",
        top="roof_flat",
        color=None,
        top_color=None,
        bottom=False,
    ):
        """Vertical walls along `polygon` from z0 to z1 with a flat top (top=None: open)."""
        pts = _pts2(polygon, "polygon")
        z0, z1 = _num(z0, "z0"), _num(z1, "z1")
        if z1 <= z0:
            raise GeometryError("extrude: z1 must be above z0")
        shape = Polygon(pts)
        if not shape.is_valid or shape.area <= 1e-6:
            raise GeometryError("extrude: polygon is self-intersecting or has no area")
        ring = list(orient(shape, 1.0).exterior.coords)[:-1]  # counter-clockwise
        wall_mat, col = _material(mat), self._color(color)
        # One patch per wall keeps texture patterns straight on every side.
        for (ax, ay), (bx, by) in zip(ring, ring[1:] + ring[:1], strict=True):
            if math.hypot(bx - ax, by - ay) < 1e-6:
                continue
            self.mesh.add(
                quad_face([(ax, ay, z0), (bx, by, z0), (bx, by, z1), (ax, ay, z1)], wall_mat, col)
            )
        if top is not None:
            self.mesh.add(
                planar_face(
                    [(x, y, z1) for x, y in ring],
                    _material(top),
                    self._color(top_color),
                    normal=(0.0, 0.0, 1.0),
                )
            )
        if bottom:
            self.mesh.add(
                planar_face(
                    [(x, y, z0) for x, y in reversed(ring)], wall_mat, col, normal=(0.0, 0.0, -1.0)
                )
            )

    def box(
        self,
        x,
        y,
        z0,
        length,
        width,
        height,
        heading=0.0,
        mat="concrete",
        top=None,
        color=None,
        top_color=None,
    ):
        """Box centred on (x, y); `length` along `heading` (degrees from north, clockwise)."""
        length, width = _num(length, "length"), _num(width, "width")
        if length <= 0 or width <= 0:
            raise GeometryError("box: length and width must be positive")
        ux, uy = _dir(heading)
        vx, vy = -uy, ux
        cx, cy, hl, hw = _num(x, "x"), _num(y, "y"), length / 2, width / 2
        rect = [
            [cx - ux * hl - vx * hw, cy - uy * hl - vy * hw],
            [cx + ux * hl - vx * hw, cy + uy * hl - vy * hw],
            [cx + ux * hl + vx * hw, cy + uy * hl + vy * hw],
            [cx - ux * hl + vx * hw, cy - uy * hl + vy * hw],
        ]
        z0 = _num(z0, "z0")
        self.extrude(
            rect,
            z0,
            z0 + _num(height, "height"),
            mat,
            top or mat,
            color,
            top_color if top_color is not None else color,
        )

    def cylinder(
        self,
        x,
        y,
        z0,
        radius,
        height,
        mat="steel",
        segments=12,
        radius_top=None,
        color=None,
        cap=True,
    ):
        """Upright cylinder or cone (radius_top) standing on (x, y, z0)."""
        cx, cy, z0 = _num(x, "x"), _num(y, "y"), _num(z0, "z0")
        r0 = _num(radius, "radius")
        r1 = r0 if radius_top is None else _num(radius_top, "radius_top")
        h = _num(height, "height")
        n = _segments(segments)
        if r0 <= 0 or h <= 0 or r1 < 0:
            raise GeometryError("cylinder: radius and height must be positive")
        mat_, col = _material(mat), self._color(color)
        side = Patch(mat_, col)
        slope = (r0 - r1) / h
        circumference = 2 * math.pi * r0
        for i in range(n + 1):
            a = 2 * math.pi * i / n
            ca, sa = math.cos(a), math.sin(a)
            nrm = norm((ca, sa, slope))
            u = circumference * i / n
            side.add_vertex((cx + r0 * ca, cy + r0 * sa, z0), nrm, (u, 0.0))
            side.add_vertex((cx + r1 * ca, cy + r1 * sa, z0 + h), nrm, (u, math.hypot(h, r0 - r1)))
        for i in range(n):
            b0, t0, b1, t1 = 2 * i, 2 * i + 1, 2 * i + 2, 2 * i + 3
            side.tris += [(b0, b1, t1), (b0, t1, t0)]
        self.mesh.add(side)
        if cap and r1 > 0:
            ring = [
                (
                    cx + r1 * math.cos(2 * math.pi * i / n),
                    cy + r1 * math.sin(2 * math.pi * i / n),
                    z0 + h,
                )
                for i in range(n)
            ]
            self.mesh.add(planar_face(ring, mat_, col, normal=(0.0, 0.0, 1.0)))

    def tube(self, a, b, radius, mat="steel", segments=6, color=None):
        """Straight tube from point a to point b (beams, braces, poles)."""
        (p,), (q,) = _pts3([a], "a", 1), _pts3([b], "b", 1)
        r = _num(radius, "radius")
        n = _segments(segments)
        axis = sub(q, p)
        length = math.sqrt(axis[0] ** 2 + axis[1] ** 2 + axis[2] ** 2)
        if length < 1e-6 or r <= 0:
            raise GeometryError("tube: points must differ and radius be positive")
        d = norm(axis)
        e1 = norm(cross(d, (0.0, 0.0, 1.0) if abs(d[2]) < 0.9 else (1.0, 0.0, 0.0)))
        e2 = cross(d, e1)
        patch = Patch(_material(mat), self._color(color))
        for i in range(n + 1):
            a_ = 2 * math.pi * i / n
            off = tuple(math.cos(a_) * e1[k] + math.sin(a_) * e2[k] for k in range(3))
            u = 2 * math.pi * r * i / n
            patch.add_vertex(tuple(p[k] + r * off[k] for k in range(3)), off, (u, 0.0))
            patch.add_vertex(tuple(q[k] + r * off[k] for k in range(3)), off, (u, length))
        for i in range(n):
            b0, t0, b1, t1 = 2 * i, 2 * i + 1, 2 * i + 2, 2 * i + 3
            patch.tris += [(b0, b1, t1), (b0, t1, t0)]
        self.mesh.add(patch)

    def sweep(
        self,
        path,
        profile,
        mat="concrete",
        mats=None,
        color=None,
        colors=None,
        closed_path=False,
        caps=False,
        two_sided=False,
    ):
        """Moves a 2D `profile` along a 3D `path`.

        Profile points are [across, up] in metres: `across` is to the LEFT of the
        walking direction, `up` is vertical. Use it for stand tiers, roof rings,
        walls along curves, rails. `mats`/`colors` give one value per profile edge.
        """
        path_pts = _pts3(path, "path", 2)
        prof = _pts2(profile, "profile", 2)
        # Closed profiles (caps) are solids: faces must point outwards whatever the
        # winding the script used. Seen along the path, +across is to the left, so
        # a counter-clockwise profile would face inwards.
        area2 = sum(
            a0 * b1 - a1 * b0 for (a0, b0), (a1, b1) in zip(prof, prof[1:] + prof[:1], strict=True)
        )
        flip = caps and area2 > 0
        edge_count = len(prof) if caps else len(prof) - 1
        if mats is not None and (not isinstance(mats, list) or len(mats) != edge_count):
            raise GeometryError(f"mats must have one entry per profile edge ({edge_count})")
        if colors is not None and (not isinstance(colors, list) or len(colors) != edge_count):
            raise GeometryError(f"colors must have one entry per profile edge ({edge_count})")
        frames = _frames(path_pts, closed_path)
        rings = [
            [(p[0] + lx * a, p[1] + ly * a, p[2] + b) for a, b in prof] for p, (lx, ly), _ in frames
        ]
        dists = [0.0]
        for (p, _, _), (q, _, _) in zip(frames, frames[1:], strict=False):
            dists.append(dists[-1] + math.dist(p, q))
        if closed_path:
            rings.append(rings[0])
            dists.append(dists[-1] + math.dist(frames[-1][0], frames[0][0]))
        prof_edges = list(range(len(prof) - 1)) + ([len(prof) - 1] if caps else [])
        v_off = 0.0
        for e in prof_edges:
            j0, j1 = e, (e + 1) % len(prof)
            emat = _material(mats[e] if mats else mat)
            ecol = self._color(colors[e] if colors else color)
            seg_len = math.dist(prof[j0], prof[j1])
            patch = Patch(emat, ecol, two_sided=two_sided)
            for i in range(len(rings) - 1):
                a0, a1 = rings[i][j0], rings[i][j1]
                b0, b1 = rings[i + 1][j0], rings[i + 1][j1]
                n = norm(cross(sub(b0, a0), sub(a1, a0)))
                if flip:
                    n = (-n[0], -n[1], -n[2])
                base = len(patch.positions)
                for pt, uv in (
                    (a0, (dists[i], v_off)),
                    (b0, (dists[i + 1], v_off)),
                    (b1, (dists[i + 1], v_off + seg_len)),
                    (a1, (dists[i], v_off + seg_len)),
                ):
                    patch.add_vertex(pt, n, uv)
                if flip:
                    patch.tris += [(base, base + 2, base + 1), (base, base + 3, base + 2)]
                else:
                    patch.tris += [(base, base + 1, base + 2), (base, base + 2, base + 3)]
            v_off += seg_len
            self.mesh.add(patch)
        if caps and not closed_path and len(prof) >= 3:
            first, last = rings[0], rings[-1]
            cap_mat = _material(mats[0] if mats else mat)
            cap_col = self._color(colors[0] if colors else color)
            self.mesh.add(planar_face(list(first), cap_mat, cap_col, two_sided=True))
            self.mesh.add(planar_face(list(last), cap_mat, cap_col, two_sided=True))

    def revolve(
        self,
        x,
        y,
        profile,
        mat="concrete",
        segments=24,
        start=0.0,
        end=360.0,
        color=None,
        two_sided=False,
    ):
        """Rotates a profile of [radius, z] points around the vertical axis at (x, y):
        domes, towers, round stands, bowls. Angles in degrees from north, clockwise."""
        cx, cy = _num(x, "x"), _num(y, "y")
        prof = _pts2(profile, "profile", 2)
        n = _segments(segments)
        a0, a1 = _num(start, "start"), _num(end, "end")
        full = abs((a1 - a0) % 360) < 1e-6 and a1 != a0
        mat_, col = _material(mat), self._color(color)
        angles = [math.radians(a0 + (a1 - a0) * i / n) for i in range(n + 1)]
        for j in range(len(prof) - 1):
            (r0, z0), (r1, z1) = prof[j], prof[j + 1]
            patch = Patch(mat_, col, two_sided=two_sided or not full)
            rm = max(0.1, (r0 + r1) / 2)
            seg = math.hypot(r1 - r0, z1 - z0)
            for i in range(n):
                t0, t1 = angles[i], angles[i + 1]
                pts = [
                    (cx + r0 * math.sin(t0), cy + r0 * math.cos(t0), z0),
                    (cx + r0 * math.sin(t1), cy + r0 * math.cos(t1), z0),
                    (cx + r1 * math.sin(t1), cy + r1 * math.cos(t1), z1),
                    (cx + r1 * math.sin(t0), cy + r1 * math.cos(t0), z1),
                ]
                if r0 < 1e-6 and r1 < 1e-6:
                    continue
                # Outward for profiles that run from bottom to top.
                nrm = norm(cross(sub(pts[3], pts[0]), sub(pts[1], pts[0])))
                if r0 < 1e-6:  # apex at the bottom: use the upper edge
                    nrm = norm(cross(sub(pts[3], pts[0]), sub(pts[2], pts[3])))
                base = len(patch.positions)
                for pt, uv in (
                    (pts[0], (rm * t0, 0.0)),
                    (pts[1], (rm * t1, 0.0)),
                    (pts[2], (rm * t1, seg)),
                    (pts[3], (rm * t0, seg)),
                ):
                    patch.add_vertex(pt, nrm, uv)
                if r1 < 1e-6:  # cone tip: the upper edge is a single point
                    patch.tris += [(base, base + 2, base + 1)]
                elif r0 < 1e-6:
                    patch.tris += [(base, base + 3, base + 2)]
                else:
                    patch.tris += [(base, base + 2, base + 1), (base, base + 3, base + 2)]
            self.mesh.add(patch)

    def quad(self, a, b, c, d, mat="concrete", color=None, two_sided=True):
        """Free quadrilateral (roof planes, panels, screens)."""
        pts = _pts3([a, b, c, d], "quad", 4)
        self.mesh.add(quad_face(pts, _material(mat), self._color(color), two_sided=two_sided))

    def face(self, points, mat="concrete", color=None, two_sided=True):
        """Free flat polygon of [x, y, z] points (gables, irregular roofs)."""
        pts = _pts3(points, "points", 3)
        self.mesh.add(planar_face(pts, _material(mat), self._color(color), two_sided=two_sided))


# ---- geometry helpers for scripts (pure functions) ------------------------------


def _dir(heading_deg) -> tuple[float, float]:
    h = math.radians(_num(heading_deg, "heading"))
    return math.sin(h), math.cos(h)


def _frames(path: list[Vec3], closed: bool):
    """Per path point: (point, left unit vector, miter scale)."""
    n = len(path)
    out = []
    for i, p in enumerate(path):
        prev_p = path[i - 1] if (i > 0 or closed) else None
        next_p = path[(i + 1) % n] if (i < n - 1 or closed) else None
        dirs = []
        for a, b in ((prev_p, p), (p, next_p)):
            if a is None or b is None:
                continue
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy)
            if length > 1e-9:
                dirs.append((dx / length, dy / length))
        if not dirs:
            raise GeometryError("path points must differ horizontally")
        tx, ty = sum(d[0] for d in dirs), sum(d[1] for d in dirs)
        length = math.hypot(tx, ty)
        if length < 1e-9:
            tx, ty = dirs[-1]
        else:
            tx, ty = tx / length, ty / length
        # Miter: keep the profile width constant around corners.
        scale = 1.0
        if len(dirs) == 2:
            cos_half = dirs[0][0] * tx + dirs[0][1] * ty
            scale = 1.0 / max(cos_half, 0.35)
        out.append((p, (-ty * scale, tx * scale), scale))
    return out


def oriented_rect(polygon) -> dict:
    """Smallest rotated rectangle around a polygon: centre, length (long side),
    width, heading of the long side (degrees from north) and unit vectors."""
    pts = _pts2(polygon, "polygon")
    rect = Polygon(pts).minimum_rotated_rectangle
    c = list(rect.exterior.coords)[:4]
    e1 = (c[1][0] - c[0][0], c[1][1] - c[0][1])
    e2 = (c[2][0] - c[1][0], c[2][1] - c[1][1])
    l1, l2 = math.hypot(*e1), math.hypot(*e2)
    (ax, ay), length, width = (e1, l1, l2) if l1 >= l2 else (e2, l2, l1)
    ux, uy = (ax / length, ay / length) if length else (0.0, 1.0)
    heading = math.degrees(math.atan2(ux, uy)) % 360
    center = rect.centroid
    return {
        "x": center.x,
        "y": center.y,
        "length": length,
        "width": width,
        "heading": heading,
        "ux": ux,
        "uy": uy,
        "vx": -uy,
        "vy": ux,
    }


def offset_polygon(polygon, distance) -> list[list[float]]:
    """Grows (positive) or shrinks (negative) a polygon by `distance` metres."""
    pts = _pts2(polygon, "polygon")
    out = Polygon(pts).buffer(_num(distance, "distance"), join_style="mitre")
    if out.is_empty or out.geom_type != "Polygon":
        raise GeometryError("offset_polygon: result is empty or split")
    return [[x, y] for x, y in list(orient(out, 1.0).exterior.coords)[:-1]]


def polygon_area(polygon) -> float:
    """Area in square metres."""
    return Polygon(_pts2(polygon, "polygon")).area


def polygon_centroid(polygon) -> list[float]:
    """Centre of mass [x, y] of the polygon."""
    c = Polygon(_pts2(polygon, "polygon")).centroid
    return [c.x, c.y]


def resample(path, spacing) -> list[list[float]]:
    """Points every `spacing` metres along a 2D or 3D polyline (for posts, seats, columns)."""
    step = _num(spacing, "spacing")
    if step <= 0.05:
        raise GeometryError("spacing must be > 0.05 m")
    if not isinstance(path, list) or len(path) < 2:
        raise GeometryError("path needs at least 2 points")
    pts = [[_num(c, "path") for c in p] for p in path]
    out = [pts[0]]
    carry = 0.0
    for a, b in zip(pts, pts[1:], strict=False):
        seg = math.dist(a, b)
        d = step - carry
        while d <= seg + 1e-9:
            t = d / seg if seg else 0
            out.append([a[k] + (b[k] - a[k]) * t for k in range(len(a))])
            d += step
            if len(out) > MAX_PATH_POINTS:
                raise GeometryError("resample: too many points")
        carry = seg - (d - step)
    return out


MATH = {
    "sin": lambda deg: math.sin(math.radians(_num(deg, "angle"))),
    "cos": lambda deg: math.cos(math.radians(_num(deg, "angle"))),
    "tan": lambda deg: math.tan(math.radians(_num(deg, "angle"))),
    "atan2": lambda y, x: math.degrees(math.atan2(_num(y, "y"), _num(x, "x"))),
    "sqrt": lambda v: math.sqrt(max(0.0, _num(v, "value"))),
    "hypot": lambda x, y: math.hypot(_num(x, "x"), _num(y, "y")),
    "floor": lambda v: math.floor(_num(v, "value")),
    "ceil": lambda v: math.ceil(_num(v, "value")),
    "lerp": lambda a, b, t: _num(a, "a") + (_num(b, "b") - _num(a, "a")) * _num(t, "t"),
    "clamp": lambda v, lo, hi: max(_num(lo, "lo"), min(_num(hi, "hi"), _num(v, "value"))),
}
