"""The building tools a build script can call. Everything is in local metres
(x east, y north, z up, origin at the object's anchor on the ground).

These functions are the whole "API" of the sandbox: scripts cannot do anything
else than add geometry through them.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

from xpsb.build import details
from xpsb.build import figure as figure_mod
from xpsb.build.materials import MATERIALS, parse_color
from xpsb.build.mesh import (
    MAX_PLACEMENTS,
    Anim,
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
from xpsb.xplane import lights

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


def _intensity(value: Any) -> float:
    """Light intensity in candela (not a size: its own range)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise GeometryError("intensity must be a number (candela)")
    if not 10 <= value <= 2_000_000:
        raise GeometryError("intensity must be between 10 and 2000000 cd")
    return float(value)


def _segments(value: Any) -> int:
    n = int(_num(value, "segments"))
    if not 3 <= n <= MAX_SEGMENTS:
        raise GeometryError(f"segments must be between 3 and {MAX_SEGMENTS}")
    return n


class Tools:
    """Bound to one mesh; the methods below are exposed to the script."""

    def __init__(
        self,
        mesh: Mesh,
        library: frozenset[str] | None = None,
        photos: frozenset[str] = frozenset(),
        scan: str | None = None,
    ):
        self.mesh = mesh
        self.scan_file = scan
        self.library = library or frozenset()
        self.photos = photos  # names of the item's photo textures ("photo:<name>")

    def _mat(self, mat: Any) -> str:
        if isinstance(mat, str) and mat.startswith("photo:"):
            if mat[6:] not in self.photos:
                have = ", ".join(sorted(self.photos)) or "none"
                raise GeometryError(f"unknown photo texture {mat!r}; this object has: {have}")
            return mat
        return _material(mat)

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
        holes=None,
    ):
        """Vertical walls along `polygon` from z0 to z1 with a flat top (top=None: open).
        `holes`: courtyards (list of polygons) get inward-facing walls and stay open."""
        pts = _pts2(polygon, "polygon")
        z0, z1 = _num(z0, "z0"), _num(z1, "z1")
        if z1 <= z0:
            raise GeometryError("extrude: z1 must be above z0")
        rings = details.rings_of(pts, _holes(holes))
        wall_mat, col = self._mat(mat), self._color(color)
        # One patch per wall keeps texture patterns straight on every side.
        for ring in rings:
            for (ax, ay), (bx, by) in zip(ring, ring[1:] + ring[:1], strict=True):
                if math.hypot(bx - ax, by - ay) < 1e-6:
                    continue
                self.mesh.add(
                    quad_face(
                        [(ax, ay, z0), (bx, by, z0), (bx, by, z1), (ax, ay, z1)], wall_mat, col
                    )
                )
        if top is not None:
            details.flat_top(self.mesh, rings, z1, self._mat(top), self._color(top_color))
        if bottom:
            self.mesh.add(
                planar_face(
                    [(x, y, z0) for x, y in reversed(rings[0])],
                    wall_mat,
                    col,
                    normal=(0.0, 0.0, -1.0),
                    holes=[[(x, y, z0) for x, y in reversed(h)] for h in rings[1:]],
                )
            )

    # -- building parts with real depth (details.py) -----------------------------

    def facade(
        self,
        polygon,
        z0,
        z1,
        style="classic",
        storeys=None,
        bay=None,
        mat=None,
        color=None,
        trim_color=None,
        holes=None,
        top="roof_flat",
        top_color=None,
        entrance=True,
    ):
        """Walls with REAL windows from z0 to z1 along `polygon` (and its courtyard
        `holes`): windows set back into the wall with reveals and panes, a door in
        the middle of the longest side (entrance), and per style pilasters,
        string courses, a main cornice and a rusticated ground storey.
        style: "modern" (window every 2.7 m, 3.1 m storeys), "house" (3.6 m, 2.8 m),
        "classic" (tall windows, 4.2 m, 4.8 m), "arched" (round-arched, 4.6 m, 5.4 m),
        "church" (tall narrow arched windows between buttresses, 5.5 m, 16 m).
        storeys/bay override the style (count the window axes on photos!); mat/color
        the wall, trim_color mouldings and pilasters; top a flat roof (None: open).
        Falls back to painted windows by itself when the triangle budget is short.
        Returns "detailed" or "flat"."""
        pts = _pts2(polygon, "polygon")
        n = None if storeys is None else int(_num(storeys, "storeys"))
        return details.facade(
            self.mesh,
            pts,
            _holes(holes),
            _num(z0, "z0"),
            _num(z1, "z1"),
            style,
            n,
            None if bay is None else _num(bay, "bay"),
            None if mat is None else self._mat(mat),
            self._color(color),
            self._color(trim_color),
            None if top is None else self._mat(top),
            self._color(top_color),
            bool(entrance),
        )

    def parapet(self, polygon, z, height=1.0, mat="stone", color=None, holes=None, statues=0):
        """Low wall along the roof edge at height z (mat="balustrade" for a
        balustrade), along the courtyards too; `statues` simple figures on top,
        evenly spread along the outline."""
        h = _num(height, "height")
        if not 0.2 <= h <= 4:
            raise GeometryError("parapet: height must be between 0.2 and 4 m")
        count = int(_num(statues, "statues"))
        if not 0 <= count <= 80:
            raise GeometryError("parapet: statues must be between 0 and 80")
        rings = details.rings_of(_pts2(polygon, "polygon"), _holes(holes))
        details.parapet(
            self.mesh, rings, _num(z, "z"), h, self._mat(mat), self._color(color), count
        )

    def chimneys(self, polygon, z0, z1, count=None, size=0.9, mat="brick", color=None, holes=None):
        """Chimney stacks from z0 (below the roof surface) to z1, spread evenly over
        the roof area of `polygon`, away from its edges; count defaults to one per
        ~250 m2 (at most 12). Returns how many were placed."""
        rings = details.rings_of(_pts2(polygon, "polygon"), _holes(holes))
        area = Polygon(rings[0], rings[1:]).area
        n = int(_num(count, "count")) if count is not None else max(1, min(12, round(area / 250)))
        if not 0 <= n <= 40:
            raise GeometryError("chimneys: count must be between 0 and 40")
        z0, z1 = _num(z0, "z0"), _num(z1, "z1")
        if z1 <= z0:
            raise GeometryError("chimneys: z1 must be above z0")
        return details.chimneys(
            self.mesh, rings, z0, z1, n, _num(size, "size"), self._mat(mat), self._color(color)
        )

    def mansard(
        self,
        polygon,
        z,
        rise=3.0,
        depth=3.5,
        mat="roof_metal",
        color=None,
        holes=None,
        top="roof_flat",
        top_color=None,
    ):
        """Roof of a big block or palace wing: slopes rising `rise` m over `depth` m
        inwards from the outline (and outwards from courtyards), flat on top."""
        rings = details.rings_of(_pts2(polygon, "polygon"), _holes(holes))
        rise_, depth_ = _num(rise, "rise"), _num(depth, "depth")
        if rise_ <= 0 or depth_ <= 0:
            raise GeometryError("mansard: rise and depth must be positive")
        details.mansard(
            self.mesh,
            rings,
            _num(z, "z"),
            rise_,
            depth_,
            self._mat(mat),
            self._color(color),
            None if top is None else self._mat(top),
            self._color(top_color),
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
        mat_, col = self._mat(mat), self._color(color)
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
        patch = Patch(self._mat(mat), self._color(color))
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
            emat = self._mat(mats[e] if mats else mat)
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
            # every profile edge is its own patch: its texture starts at v = 0
            self.mesh.add(patch)
        if caps and not closed_path and len(prof) >= 3:
            first, last = rings[0], rings[-1]
            cap_mat = self._mat(mats[0] if mats else mat)
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
        mat_, col = self._mat(mat), self._color(color)
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

    # -- night ----------------------------------------------------------------------

    def _light_color(self, color) -> tuple[float, float, float]:
        if color is None or color == "warm":
            return lights.WARM
        if color == "white":
            return lights.WHITE
        r, g, b = self._color(color)
        return (r / 255, g / 255, b / 255)

    def light(
        self,
        x,
        y,
        z,
        dx=0.0,
        dy=0.0,
        dz=-1.0,
        color="warm",
        intensity=6000,
        beam=60,
        glow=True,
    ):
        """A spot light at (x, y, z) shining along (dx, dy, dz) at night: lights up
        what it points at (X-Plane 12 spill light) and, with glow, shows the lamp.
        color "warm" (sodium/halogen), "white" (LED floodlight) or a colour;
        intensity in candela (facade spot ~3000-15000, stadium floodlight
        ~50000-200000); beam = full beam angle in degrees."""
        for li in lights.spot(
            _num(x, "x"),
            _num(y, "y"),
            _num(z, "z"),
            (_num(dx, "dx"), _num(dy, "dy"), _num(dz, "dz")),
            self._light_color(color),
            _intensity(intensity),
            _num(beam, "beam"),
            bool(glow),
        ):
            self.mesh.add_light(li)

    def floodlight(self, polygon, z0, z1, spacing=12.0, color="warm", intensity=None, holes=None):
        """Lights the walls of `polygon` from below at night (monuments, palaces,
        churches, bridges): spots on the ground every `spacing` metres along the
        outline, aimed up the wall from z0 to z1. Returns how many were placed."""
        step = _num(spacing, "spacing")
        if not 3.0 <= step <= 100.0:
            raise GeometryError("floodlight: spacing must be between 3 and 100 m")
        z0_, z1_ = _num(z0, "z0"), _num(z1, "z1")
        if z1_ <= z0_:
            raise GeometryError("floodlight: z1 must be above z0")
        cd = (
            _intensity(intensity)
            if intensity is not None
            else min(30000.0, max(2500.0, 450.0 * (z1_ - z0_)))
        )
        col = self._light_color(color)
        rings = details.rings_of(_pts2(polygon, "polygon"), _holes(holes))
        count = 0
        for ring in rings[:1]:  # courtyards stay dark
            for origin, d, n, length in details._edges(ring):
                k = max(1, round(length / step))
                for j in range(k):
                    s = (j + 0.5) * length / k
                    x = origin[0] + d[0] * s + n[0] * 1.2
                    y = origin[1] + d[1] * s + n[1] * 1.2
                    # aimed up, slightly towards the wall
                    for li in lights.spot(
                        x, y, z0_ + 0.3, (-n[0] * 0.35, -n[1] * 0.35, 1.0), col, cd, 50.0, False
                    ):
                        self.mesh.add_light(li)
                    count += 1
        return count

    def lamp_post(self, x, y, height=6.0, heading=0.0, color=None):
        """A street lamp: pole, arm and lamp head facing `heading` (degrees from
        north), with a real street light at night."""
        cx, cy = _num(x, "x"), _num(y, "y")
        h = _num(height, "height")
        if not 2.5 <= h <= 20:
            raise GeometryError("lamp_post: height must be between 2.5 and 20 m")
        ux, uy = _dir(heading)
        self.cylinder(cx, cy, 0.0, 0.08, h, mat="steel", segments=6, radius_top=0.06, color=color)
        arm = 0.9
        hx, hy = cx + ux * arm, cy + uy * arm
        self.tube([cx, cy, h - 0.1], [hx, hy, h], 0.04, mat="steel", segments=4, color=color)
        head = Patch("lamp", None, glow="lamp")
        corners = [
            (hx - 0.25, hy - 0.12),
            (hx + 0.25, hy - 0.12),
            (hx + 0.25, hy + 0.12),
            (hx - 0.25, hy + 0.12),
        ]
        details._shared_quad(head, [(px, py, h - 0.08) for px, py in reversed(corners)], 0.5, 0.24)
        self.mesh.add(head)
        self.mesh.add_light(lights.street_lamp(hx, hy, h - 0.12))

    def floodlight_mast(self, x, y, height, aim_x, aim_y, lamps=6, color="white", intensity=80000):
        """Stadium/sports floodlight mast at (x, y), its lamp head facing the point
        (aim_x, aim_y) on the ground: steel mast, lamp panel lit at night and
        `lamps` strong spot lights aimed at that point."""
        cx, cy, h = _num(x, "x"), _num(y, "y"), _num(height, "height")
        if not 8 <= h <= 80:
            raise GeometryError("floodlight_mast: height must be between 8 and 80 m")
        n = int(_num(lamps, "lamps"))
        if not 1 <= n <= 24:
            raise GeometryError("floodlight_mast: lamps must be between 1 and 24")
        ax, ay = _num(aim_x, "aim_x") - cx, _num(aim_y, "aim_y") - cy
        dist = math.hypot(ax, ay)
        if dist < 1:
            raise GeometryError("floodlight_mast: aim point must differ from the mast")
        ux, uy = ax / dist, ay / dist
        self.cylinder(cx, cy, 0.0, 0.6, h, mat="steel", segments=8, radius_top=0.35)
        # Lamp panel: a frame across the aim direction, tilted down towards the pitch.
        width = 1.2 * max(2, n // 2 + 1)
        vx, vy = -uy, ux
        tilt = math.atan2(h, dist)
        panel_h = 3.0
        bx, by = cx + ux * 0.6, cy + uy * 0.6
        top_z, bot_z = h + panel_h / 2, h - panel_h / 2
        back = math.sin(tilt) * panel_h / 2
        front = Patch("lamp", None, glow="lamp")
        corners = [
            (bx - vx * width / 2 + ux * back, by - vy * width / 2 + uy * back, bot_z),
            (bx + vx * width / 2 + ux * back, by + vy * width / 2 + uy * back, bot_z),
            (bx + vx * width / 2 - ux * back, by + vy * width / 2 - uy * back, top_z),
            (bx - vx * width / 2 - ux * back, by - vy * width / 2 - uy * back, top_z),
        ]
        # facing the aim point (counter-clockwise seen from there)
        details._shared_quad(front, list(reversed(corners)), width, panel_h)
        self.mesh.add(front)
        rear = Patch("steel", None)
        details._shared_quad(rear, corners, width, panel_h)
        self.mesh.add(rear)
        col = self._light_color(color)
        cd = _intensity(intensity)
        for k in range(n):
            t = (k + 0.5) / n - 0.5
            px, py = bx + vx * width * t, by + vy * width * t
            # each lamp aims at a slightly different spot: an even pool of light
            tx = ax + vx * dist * 0.6 * t
            ty = ay + vy * dist * 0.6 * t
            for li in lights.spot(px, py, h, (tx, ty, -h), col, cd, 40.0, True):
                self.mesh.add_light(li)

    def flag(
        self,
        x,
        y,
        height=10.0,
        length=3.0,
        width=2.0,
        colors=None,
        stripes="horizontal",
        pole=True,
        wind=True,
        heading=0.0,
        z=0.0,
    ):
        """A flag on a pole at (x, y) that waves (animated) and turns with the wind
        in X-Plane. length = from the pole to the free end, width = top to bottom,
        colors = the stripes from top to bottom (stripes="horizontal") or from the
        pole outwards ("vertical"), e.g. ["#CE2939", "#FFFFFF", "#477050"] for
        Hungary. pole=False hangs it on something else at `height` (top edge);
        wind=False keeps it pointing to `heading`. z = foot of the pole."""
        cx, cy, z0 = _num(x, "x"), _num(y, "y"), _num(z, "z")
        h, ln, w = _num(height, "height"), _num(length, "length"), _num(width, "width")
        if not (1 <= h <= 120 and 0.3 <= ln <= 30 and 0.2 <= w <= 20):
            raise GeometryError("flag: height 1-120 m, length 0.3-30 m, width 0.2-20 m")
        cols = colors if colors is not None else ["#FFFFFF"]
        if not isinstance(cols, list) or not 1 <= len(cols) <= 9:
            raise GeometryError("flag: colors must be a list of 1 to 9 colours")
        cols = [self._color(c) for c in cols]
        if stripes not in ("horizontal", "vertical"):
            raise GeometryError('flag: stripes must be "horizontal" or "vertical"')
        if pole:
            self.cylinder(
                cx,
                cy,
                z0,
                0.06 + h * 0.004,
                h + 0.3,
                mat="paint",
                color="#E8E8E8",
                segments=6,
                radius_top=0.04 + h * 0.002,
            )
            self.cylinder(
                cx,
                cy,
                z0 + h + 0.3,
                0.09,
                0.15,
                mat="paint",
                color="#C9A642",
                segments=6,
                radius_top=0.0,
                cap=False,
            )
        frames, period = 8, 1.6
        anim = Anim(frames, period, (cx, cy, z0), bool(wind))
        if not wind:  # a fixed flag: no rotation, shape still waves
            anim.wind = False
        a_idx = len(self.mesh.anims)
        self.mesh.anims.append(anim)
        hd = math.radians(_num(heading, "heading")) if not wind else 0.0
        top = z0 + h
        nu, nv = 8, max(2, 2 * len(cols)) if stripes == "horizontal" else 3
        for f in range(frames):
            phase = 2 * math.pi * f / frames

            def at(u: float, v: float, phase: float = phase) -> Vec3:
                # u from the pole (0) to the free end (1), v from the top (0) down
                along = u * ln
                side = 0.12 * ln * u * math.sin(2 * math.pi * (u * 1.1) - phase)
                droop = 0.06 * ln * u * u
                lx, ly = side, along  # streaming north before turning
                rx = lx * math.cos(hd) + ly * math.sin(hd)
                ry = -lx * math.sin(hd) + ly * math.cos(hd)
                return (cx + rx, cy + ry, top - v * w - droop)

            for k, col in enumerate(cols):
                patch = Patch("paint", col, two_sided=True, anim=(a_idx, f))
                if stripes == "horizontal":
                    u_rng, v_rng = (0.0, 1.0), (k / len(cols), (k + 1) / len(cols))
                    cu, cv = nu, max(1, nv // len(cols))
                else:
                    u_rng, v_rng = (k / len(cols), (k + 1) / len(cols)), (0.0, 1.0)
                    cu, cv = max(1, nu // len(cols)), nv
                grid = []
                for i in range(cu + 1):
                    row = []
                    for j in range(cv + 1):
                        u = u_rng[0] + (u_rng[1] - u_rng[0]) * i / cu
                        v = v_rng[0] + (v_rng[1] - v_rng[0]) * j / cv
                        p0 = at(u, v)
                        du = sub(at(min(1.0, u + 0.01), v), at(max(0.0, u - 0.01), v))
                        dv = sub(at(u, min(1.0, v + 0.01)), at(u, max(0.0, v - 0.01)))
                        row.append(patch.add_vertex(p0, norm(cross(dv, du)), (u * ln, (1 - v) * w)))
                    grid.append(row)
                for i in range(cu):
                    for j in range(cv):
                        a0, a1 = grid[i][j], grid[i + 1][j]
                        b0, b1 = grid[i][j + 1], grid[i + 1][j + 1]
                        patch.tris += [(a0, b0, b1), (a0, b1, a1)]
                self.mesh.add(patch)

    # -- statues ---------------------------------------------------------------------

    def figure(
        self,
        x,
        y,
        z=0.0,
        height=2.5,
        pose="standing",
        heading=0.0,
        mat="bronze",
        color=None,
        robe=False,
        holding="none",
        horse_mat=None,
    ):
        """A statue: a human figure standing on z, facing `heading` (degrees from
        north, clockwise), `height` = its whole height (raised arms included).
        pose: "standing", "arm_raised" (right arm up), "arms_raised" (both hands hold
        something high above the head), "pointing" (right arm forward), "seated"
        (on a block), "rider" (on a horse; height = rider's head above the horse's
        hooves), "bust" (head and shoulders). robe=True: a long robe instead of legs
        (women, saints, allegories). holding: "none", "palm", "wreath", "sword",
        "torch", "book", "flag". Not a portrait - a figure with the right pose and
        size; about 1 000-2 000 triangles near, a cheap stand-in further away.
        Returns the triangles used."""
        return figure_mod.add_figure(
            self.mesh,
            _num(x, "x"),
            _num(y, "y"),
            _num(z, "z"),
            _num(height, "height"),
            str(pose),
            _num(heading, "heading"),
            self._mat(mat),
            self._color(color),
            bool(robe),
            str(holding),
            None if horse_mat is None else self._mat(horse_mat),
        )

    def pedestal(
        self, x, y, height, width, depth=None, heading=0.0, mat="stone", color=None, round=False
    ):
        """Plinth of a statue at (x, y): a step, the block and a cornice on top
        (round=True: a drum). Returns the height of its top (where the figure stands)."""
        h, w = _num(height, "height"), _num(width, "width")
        d = w if depth is None else _num(depth, "depth")
        if h <= 0 or w <= 0 or d <= 0:
            raise GeometryError("pedestal: height, width and depth must be positive")
        cx, cy = _num(x, "x"), _num(y, "y")
        step = min(0.12 * h, 0.5)
        cap = min(0.1 * h, 0.4)
        if round:
            n = 16
            r = w / 2
            self.revolve(
                cx,
                cy,
                [
                    [r * 1.15, 0],
                    [r * 1.15, step],
                    [r, step],
                    [r, h - cap],
                    [r * 1.1, h - cap],
                    [r * 1.1, h],
                    [0, h],
                ],
                mat=mat,
                segments=n,
                color=color,
            )
            return h
        for z0, z1, grow in ((0.0, step, 1.2), (step, h - cap, 1.0), (h - cap, h, 1.1)):
            self.box(
                cx,
                cy,
                z0,
                d * grow,
                w * grow,
                z1 - z0,
                heading=heading,
                mat=mat,
                top=mat,
                color=color,
                top_color=color,
            )
        return h

    def scan(self, x, y, z=0.0, height=5.0, heading=0.0, mat="bronze", color=None):
        """Places the object's real 3D scan (only if `has_scan` is True: a free scan
        from Wikimedia Commons, reduced to a few thousand triangles) with its foot at
        (x, y, z), `height` metres tall, turned by `heading` degrees (clockwise; turn
        it until it faces where the real statue faces). Usually includes the base the
        scanned statue stands on. Returns the triangles used."""
        if self.scan_file is None:
            raise GeometryError("scan: this object has no 3D scan (check has_scan)")
        from xpsb import scans

        data = scans.load(Path(self.scan_file))
        h = _num(height, "height")
        if not 0.2 <= h <= 200:
            raise GeometryError("scan: height must be between 0.2 and 200 m")
        cx, cy, cz = _num(x, "x"), _num(y, "y"), _num(z, "z")
        t = math.radians(_num(heading, "heading"))
        cos, sin = math.cos(t), math.sin(t)
        mat_, col = self._mat(mat), self._color(color)
        before = self.mesh.triangle_count
        for lod in ("near", "far"):
            v = data[f"{lod}_v"].astype(float) * h
            tris = data[f"{lod}_t"]
            # clockwise turn seen from above: x' = x cos + y sin, y' = -x sin + y cos
            px = cx + v[:, 0] * cos + v[:, 1] * sin
            py = cy - v[:, 0] * sin + v[:, 1] * cos
            pz = cz + v[:, 2]
            pts = list(zip(px.tolist(), py.tolist(), pz.tolist(), strict=True))
            normals = [[0.0, 0.0, 0.0] for _ in pts]
            for a, b, c in tris.tolist():
                n = cross(sub(pts[b], pts[a]), sub(pts[c], pts[a]))
                for i in (a, b, c):
                    normals[i][0] += n[0]
                    normals[i][1] += n[1]
                    normals[i][2] += n[2]
            # The wrap overlaps itself (arms in front of the body): a shared texture,
            # no baked shading painted into it.
            patch = Patch(mat_, col, lod=lod, shared_uv=True)
            for p, n in zip(pts, normals, strict=True):
                # wrapped around the vertical axis: u along the girth, v up
                u = math.atan2(p[1] - cy, p[0] - cx) * h * 0.15
                patch.add_vertex(p, norm((n[0], n[1], n[2])), (u, p[2] - cz))
            patch.tris = [tuple(t_) for t_ in tris.tolist()]
            self.mesh.add(patch)
        self.mesh.used_scan = True
        return self.mesh.triangle_count - before

    def quad(self, a, b, c, d, mat="concrete", color=None, two_sided=True):
        """Free quadrilateral (roof planes, panels, screens)."""
        pts = _pts3([a, b, c, d], "quad", 4)
        self.mesh.add(quad_face(pts, self._mat(mat), self._color(color), two_sided=two_sided))

    def face(self, points, mat="concrete", color=None, two_sided=True):
        """Free flat polygon of [x, y, z] points (gables, irregular roofs)."""
        pts = _pts3(points, "points", 3)
        self.mesh.add(planar_face(pts, self._mat(mat), self._color(color), two_sided=two_sided))


# ---- geometry helpers for scripts (pure functions) ------------------------------


def _holes(value) -> list[list[Vec2]]:
    """Courtyards: None or a list of polygons."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise GeometryError("holes must be a list of polygons")
    return [_pts2(h, "holes") for h in value]


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
