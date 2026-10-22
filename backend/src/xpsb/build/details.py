"""Building parts with real depth: the script says what a facade or roof is like,
the details come from here - windows set back into the wall with reveals and
panes, cornices and string courses, pilasters, a rusticated base storey, parapets,
chimneys, mansard roofs. One call instead of hundreds of faces: cheap for the AI,
consistent for every building.

Rings: the outline counter-clockwise, courtyards clockwise (as a shapely polygon
oriented with orient(.., 1.0) has them). Walking along a ring, the open air is
always on the right-hand side.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely import constrained_delaunay_triangles
from shapely.geometry import Point, Polygon
from shapely.geometry.polygon import orient

from xpsb.build import ornaments
from xpsb.build.materials import lit_window
from xpsb.build.mesh import (
    GeometryError,
    Mesh,
    Patch,
    Vec2,
    Vec3,
    _triangulate_2d,
    planar_face,
    quad_face,
)

# Texture space given to every pane: they all share one painted window (like a
# trim sheet), so a hundred windows cost one small piece of the texture.
PANE_UV = 2.0
TRIM_UV = 4.0  # the same for long mouldings (patterned ones repeat every 4 m)
SMOOTH = {"moulding", "plaster", "concrete", "paint", "roof_flat", "membrane", "steel"}


@dataclass(frozen=True)
class FacadeStyle:
    bay: float  # a window every `bay` metres
    storey: float  # storey height
    win_w: float
    win_h: float
    sill: float  # from the storey floor to the window
    depth: float  # how far windows sit back in the wall
    arched: bool = False
    pilasters: bool = False
    cornices: bool = False  # string courses between storeys and a main cornice
    base: str | None = None  # material of the ground storey (rustication)
    wall: str = "plaster"
    flat: str = "windows"  # painted stand-in when there is no triangle budget left


STYLES = {
    "modern": FacadeStyle(2.7, 3.1, 1.6, 1.6, 0.9, 0.12, flat="windows"),
    "house": FacadeStyle(3.6, 2.8, 1.15, 1.3, 0.9, 0.15, flat="windows_house"),
    "classic": FacadeStyle(
        4.2,
        4.8,
        1.5,
        2.7,
        1.1,
        0.3,
        pilasters=True,
        cornices=True,
        base="rustication",
        flat="windows_classic",
    ),
    "arched": FacadeStyle(
        4.6,
        5.4,
        1.7,
        3.3,
        1.1,
        0.35,
        arched=True,
        pilasters=True,
        cornices=True,
        base="rustication",
        flat="windows_arched",
    ),
    # Churches and their towers: one tall storey of narrow round-arched windows
    # between buttresses (towers: one tier of openings above the other).
    "church": FacadeStyle(
        5.5,
        16.0,
        1.9,
        8.5,
        3.0,
        0.45,
        arched=True,
        pilasters=True,
        flat="windows_arched",
    ),
}
ARCH_SEGMENTS = 6
PILASTER_W = 0.55
PILASTER_OUT = 0.12
STRING_COURSE = [(0.0, 0.0), (-0.12, 0.06), (-0.12, 0.22), (0.0, 0.28)]
MAIN_CORNICE = [
    (0.0, -0.05),
    (-0.15, 0.05),
    (-0.15, 0.25),
    (-0.5, 0.45),
    (-0.55, 0.65),
    (0.0, 0.65),
]


def rings_of(polygon: list[Vec2], holes: list[list[Vec2]] | None) -> list[list[Vec2]]:
    """Outline counter-clockwise, courtyards clockwise; validated."""
    shell = Polygon(polygon, holes or [])
    if not shell.is_valid or shell.area <= 1e-6:
        raise GeometryError("polygon (with its courtyards) is self-intersecting or has no area")
    shell = orient(shell, 1.0)
    rings = [list(shell.exterior.coords)[:-1]]
    rings += [list(h.coords)[:-1] for h in shell.interiors]
    return rings


def _edges(ring: list[Vec2]):
    """(start, unit direction, outward normal, length) of every edge."""
    for (ax, ay), (bx, by) in zip(ring, ring[1:] + ring[:1], strict=True):
        length = math.hypot(bx - ax, by - ay)
        if length < 0.05:
            continue
        dx, dy = (bx - ax) / length, (by - ay) / length
        yield (ax, ay), (dx, dy), (dy, -dx), length


def _outline(s0: float, z0: float, w: float, h: float, arched: bool) -> list[Vec2]:
    """Window/door outline counter-clockwise in wall coordinates (s along, z up)."""
    if not arched or h < w * 0.6:
        return [(s0, z0), (s0 + w, z0), (s0 + w, z0 + h), (s0, z0 + h)]
    r = w / 2
    spring = z0 + h - r
    pts = [(s0, z0), (s0 + w, z0), (s0 + w, spring)]
    for k in range(1, ARCH_SEGMENTS):
        a = math.pi * k / ARCH_SEGMENTS
        pts.append((s0 + r + r * math.cos(a), spring + r * math.sin(a)))
    pts.append((s0, spring))
    return pts


class Facade:
    """Builds one facade call into the mesh (wall bands, reveals, panes, trim)."""

    def __init__(self, mesh: Mesh, wall_mat: str, wall_color, trim_color, pane_color):
        self.mesh = mesh
        self.wall_mat, self.wall_color = wall_mat, wall_color
        # Everything with real depth is the close-up level; from further away a
        # painted stand-in takes over (see facade()).
        self.reveals = Patch(wall_mat, wall_color, lod="near")
        self.panes = Patch("glass_pane", pane_color, lod="near")
        self.panes_lit = Patch("glass_pane", pane_color, lod="near", glow="window")
        self.doors = Patch("door", None, lod="near")
        self.trim = Patch("stone", trim_color, lod="near")
        self.trim_sides = Patch("stone", trim_color, lod="near")
        for p in (self.reveals, self.panes, self.panes_lit, self.doors, self.trim, self.trim_sides):
            p.shared_uv = True
        # Fine detail for Blender to bake into the walls (never drawn itself).
        self.ornaments = Patch("stone", trim_color, lod="bake")

    def wall_band(self, origin, d, n, length, zb, zt, holes, mat, u_off):
        """Wall from zb to zt along one edge, with window holes (s, z outlines)."""
        shell = [(0.0, zb), (length, zb), (length, zt), (0.0, zt)]
        poly = Polygon(shell, holes)
        if not poly.is_valid:
            poly = Polygon(shell)  # never lose the wall over a bad opening
            holes = []
        patch = Patch(mat, self.wall_color, lod="near")
        index: dict[tuple[float, float], int] = {}

        def vertex(s: float, z: float) -> int:
            key = (round(s, 5), round(z, 5))
            if key not in index:
                p = (origin[0] + d[0] * s, origin[1] + d[1] * s, z)
                index[key] = patch.add_vertex(p, (n[0], n[1], 0.0), (u_off + s, z))
            return index[key]

        for tri in constrained_delaunay_triangles(poly).geoms:
            (a, b, c) = list(tri.exterior.coords)[:3]
            area2 = (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])
            if abs(area2) < 1e-9:
                continue
            ids = [vertex(*a), vertex(*b), vertex(*c)]
            patch.tris.append((ids[0], ids[1], ids[2]) if area2 > 0 else (ids[0], ids[2], ids[1]))
        self.mesh.add(patch)

    def opening(
        self,
        origin,
        d,
        n,
        outline: list[Vec2],
        depth: float,
        door: bool = False,
        ornament: str | None = None,
    ):
        """Reveals into the wall and the pane (or door) at the back of one opening;
        `ornament` adds fine surround detail for the bake (see ornaments.window)."""
        if ornament and not door:
            ornaments.window(self.ornaments, origin, d, n, outline, ornament)

        def at(s: float, z: float, back: float) -> Vec3:
            return (origin[0] + d[0] * s - n[0] * back, origin[1] + d[1] * s - n[1] * back, z)

        for (s1, z1), (s2, z2) in zip(outline, outline[1:] + outline[:1], strict=True):
            seg = math.hypot(s2 - s1, z2 - z1)
            if seg < 1e-6:
                continue
            base = len(self.reveals.positions)
            corners = [at(s1, z1, 0), at(s2, z2, 0), at(s2, z2, depth), at(s1, z1, depth)]
            q = quad_face(corners, self.reveals.material, None)
            for p, (u, v) in zip(
                q.positions, [(0, 0), (1, 0), (1, depth), (0, depth)], strict=True
            ):
                self.reveals.add_vertex(p, q.normals[0], (u, v))
            self.reveals.tris += [(base, base + 1, base + 2), (base, base + 2, base + 3)]
        s0 = min(s for s, _ in outline)
        z0 = min(z for _, z in outline)
        w = max(s for s, _ in outline) - s0
        h = max(z for _, z in outline) - z0
        if door:
            back = self.doors
        else:  # some rooms have the light on at night (the same ones every build)
            cx, cy = origin[0] + d[0] * s0, origin[1] + d[1] * s0
            seed = int(round(cx * 10)) * 73856093 ^ int(round(cy * 10)) * 19349663 ^ int(z0 * 10)
            back = self.panes_lit if lit_window(seed) else self.panes
        base = len(back.positions)
        for s, z in outline:
            back.add_vertex(
                at(s, z, depth),
                (n[0], n[1], 0.0),
                ((s - s0) / w * PANE_UV, (z - z0) / h * PANE_UV),
            )
        back.tris += [(base + a, base + b, base + c) for a, b, c in _triangulate_2d(list(outline))]

    def pilaster(self, origin, d, n, s: float, zb: float, zt: float):
        """Flat pilaster strip standing out of the wall."""
        if zt - zb < 1.0:
            return
        w2 = PILASTER_W / 2

        def at(ss: float, z: float, out: float) -> Vec3:
            return (origin[0] + d[0] * ss + n[0] * out, origin[1] + d[1] * ss + n[1] * out, z)

        front = [
            at(s - w2, zb, PILASTER_OUT),
            at(s + w2, zb, PILASTER_OUT),
            at(s + w2, zt, PILASTER_OUT),
            at(s - w2, zt, PILASTER_OUT),
        ]
        sides = [
            [
                at(s + w2, zb, PILASTER_OUT),
                at(s + w2, zb, 0),
                at(s + w2, zt, 0),
                at(s + w2, zt, PILASTER_OUT),
            ],
            [
                at(s - w2, zb, 0),
                at(s - w2, zb, PILASTER_OUT),
                at(s - w2, zt, PILASTER_OUT),
                at(s - w2, zt, 0),
            ],
        ]
        for patch, quad, size in [(self.trim, front, PILASTER_W)] + [
            (self.trim_sides, q, PILASTER_OUT) for q in sides
        ]:
            q = quad_face(quad, "stone", None)
            base = len(patch.positions)
            for p, uv in zip(
                q.positions, [(0, 0), (size, 0), (size, zt - zb), (0, zt - zb)], strict=True
            ):
                patch.add_vertex(p, q.normals[0], uv)
            patch.tris += [(base, base + 1, base + 2), (base, base + 2, base + 3)]

    def finish(self):
        for patch in (
            self.reveals,
            self.panes,
            self.panes_lit,
            self.doors,
            self.trim,
            self.trim_sides,
            self.ornaments,
        ):
            self.mesh.add(patch)


def band(
    mesh: Mesh,
    ring: list[Vec2],
    z: float,
    profile: list[Vec2],
    mat: str,
    color,
    lod: str = "all",
) -> None:
    """A moulding along a closed ring: profile points (out, up) with out < 0 meaning
    into the open air (the right-hand side of the ring), running from the wall at
    the bottom round to the wall at the top, so every face looks outwards. Mitred
    corners; every piece uses the same small piece of texture."""
    from xpsb.build.tools import _frames

    frames = _frames([(x, y, z) for x, y in ring], True)
    count = len(frames)
    patch = Patch(mat, color, lod=lod, shared_uv=True)
    for e in range(len(profile) - 1):
        (a0, b0), (a1, b1) = profile[e], profile[e + 1]
        seg = math.hypot(a1 - a0, b1 - b0)
        for i in range(count):
            (p, (lx, ly), _), (q, (mx, my), _) = frames[i], frames[(i + 1) % count]
            # Smooth materials may stretch over a long side; patterns (balusters,
            # joints) repeat piece by piece so they keep their size.
            side = math.dist(p, q)
            pieces = 1 if mat in SMOOTH else max(1, math.ceil(side / TRIM_UV))
            for k in range(pieces):
                t0, t1 = k / pieces, (k + 1) / pieces

                def at(t: float, a: float, b: float, p=p, q=q, lx=lx, ly=ly, mx=mx, my=my):
                    return (
                        p[0] + (q[0] - p[0]) * t + (lx + (mx - lx) * t) * a,
                        p[1] + (q[1] - p[1]) * t + (ly + (my - ly) * t) * a,
                        z + b,
                    )

                pts = [at(t0, a0, b0), at(t1, a0, b0), at(t1, a1, b1), at(t0, a1, b1)]
                length = min(TRIM_UV, side / pieces)
                q_ = quad_face(pts, mat, None)
                base = len(patch.positions)
                for pt, uv in zip(
                    q_.positions, [(0, 0), (length, 0), (length, seg), (0, seg)], strict=True
                ):
                    patch.add_vertex(pt, q_.normals[0], uv)
                patch.tris += [(base, base + 1, base + 2), (base, base + 2, base + 3)]
    mesh.add(patch)


def flat_top(mesh: Mesh, rings: list[list[Vec2]], z: float, mat: str, color) -> None:
    outline, holes = rings[0], rings[1:]
    mesh.add(
        planar_face(
            [(x, y, z) for x, y in outline],
            mat,
            color,
            normal=(0.0, 0.0, 1.0),
            holes=[[(x, y, z) for x, y in h] for h in holes],
        )
    )


def estimate_triangles(rings, height: float, style: FacadeStyle) -> int:
    storeys = max(1, round(height / style.storey))
    windows = 0
    walls = 0
    for ring in rings:
        for _, _, _, length in _edges(ring):
            walls += 4
            if length >= style.bay * 0.7:
                windows += max(1, round(length / style.bay)) * storeys
    per = (14 + 2 * (ARCH_SEGMENTS + 2) + ARCH_SEGMENTS) if style.arched else 20
    trim = windows * 6 // storeys if style.pilasters else 0
    return windows * per + walls * 2 + trim + (storeys + 1) * 12 * len(rings) * 4


def chimney_spots(rings: list[list[Vec2]], count: int, inset: float) -> list[Vec2]:
    """Evenly spread, repeatable spots on the roof (away from the edges)."""
    area = Polygon(rings[0], rings[1:]).buffer(-inset)
    if area.is_empty:
        return []
    x0, y0, x1, y1 = area.bounds
    step = max(2.0, math.sqrt(area.area / max(count * 3, 1)))
    cands = []
    y = y0 + step / 2
    while y < y1:
        x = x0 + step / 2
        while x < x1:
            if area.contains(Point(x, y)):
                cands.append((x, y))
            x += step
        y += step
    if not cands:
        c = area.representative_point()
        return [(c.x, c.y)]
    pick = max(1, len(cands) // max(count, 1))
    return cands[pick // 2 :: pick][:count]


# ---- whole parts ------------------------------------------------------------------


def _shared_quad(patch: Patch, corners: list[Vec3], w: float, h: float) -> None:
    """Adds a quad whose texture is the shared (0..w, 0..h) piece of its patch."""
    q = quad_face(corners, patch.material, None)
    patch.shared_uv = True
    base = len(patch.positions)
    for p, uv in zip(q.positions, [(0, 0), (w, 0), (w, h), (0, h)], strict=True):
        patch.add_vertex(p, q.normals[0], uv)
    patch.tris += [(base, base + 1, base + 2), (base, base + 2, base + 3)]


def facade(
    mesh: Mesh,
    polygon: list[Vec2],
    holes: list[list[Vec2]] | None,
    z0: float,
    z1: float,
    style: str,
    storeys: int | None,
    bay: float | None,
    wall_mat: str | None,
    color,
    trim_color,
    top: str | None,
    top_color,
    entrance: int,
) -> str:
    """Builds the walls of a building with real windows; returns "detailed" or, when
    the triangle budget is too small, "flat" (painted windows instead)."""
    if style not in STYLES:
        raise GeometryError(f"facade: style must be one of {', '.join(STYLES)}")
    st = STYLES[style]
    if bay is not None:
        if not 1.5 <= bay <= 20:
            raise GeometryError("facade: bay must be between 1.5 and 20 m")
        st = FacadeStyle(**{**st.__dict__, "bay": bay})
    if z1 - z0 < 1.0:
        raise GeometryError("facade: z1 must be at least 1 m above z0")
    rings = rings_of(polygon, holes)
    height = z1 - z0
    n_st = int(storeys) if storeys else max(1, round(height / st.storey))
    n_st = max(1, min(n_st, 60))
    sh = height / n_st
    margin = 0.75 if st.cornices else 0.45
    win_h = min(st.win_h, sh - st.sill - margin)
    sill = st.sill
    if win_h < st.win_h * 0.6:  # low storeys: smaller windows, lower sill
        sill = max(0.5, 0.3 * sh)
        win_h = min(st.win_h, sh - sill - margin)
    wall = wall_mat or st.wall
    # painted stand-in: the style's windows on this wall material
    if wall.startswith("photo:"):
        flat = wall  # the photo already shows the windows
    else:
        flat = st.flat if wall == "plaster" else f"{st.flat}+{wall}"
    budget = mesh.max_triangles - mesh.triangle_count
    if win_h < 0.6 or estimate_triangles(rings, height, st) > budget * 0.85:
        _painted_walls(mesh, rings, z0, z1, flat, color, "all")
        if top:
            flat_top(mesh, rings, z1, top, top_color)
        return "flat"

    f = Facade(mesh, wall, color, trim_color, None)
    base_top = z0 + sh if st.base and n_st >= 2 else z0
    longest = max(length for _, _, _, length in _edges(rings[0]))
    u_off = 0.0
    for ring_no, ring in enumerate(rings):
        for origin, d, n, length in _edges(ring):
            nb = max(1, round(length / st.bay)) if length >= st.bay * 0.7 else 0
            bw = length / nb if nb else length
            ww = min(st.win_w, bw - (PILASTER_W + 0.35 if st.pilasters else 0.5))
            lower: list[list[Vec2]] = []
            upper: list[list[Vec2]] = []
            if nb and ww >= 0.5:
                doors: set[int] = set()
                if entrance and ring_no == 0 and length == longest:
                    # one door, or one per house of a row (evenly along the front)
                    count = max(1, min(int(entrance), nb))
                    doors = {int((i + 0.5) * nb / count) for i in range(count)}
                    if count == 1:
                        doors = {nb // 2}
                for k in range(n_st):
                    zf = z0 + k * sh
                    for j in range(nb):
                        sc = (j + 0.5) * bw
                        is_door = k == 0 and j in doors
                        if is_door:
                            dw = min(ww * 1.5, bw - 0.6)
                            dh = min(sh - margin, max(2.6, sill + win_h))
                            out = _outline(sc - dw / 2, zf + 0.15, dw, dh, st.arched)
                        else:
                            out = _outline(sc - ww / 2, zf + sill, ww, win_h, st.arched)
                        (lower if zf < base_top - 1e-6 else upper).append(out)
                        f.opening(origin, d, n, out, st.depth, is_door, _ornament(st, k, n_st))
            if base_top > z0:
                f.wall_band(origin, d, n, length, z0, base_top, lower, st.base, u_off)
                f.wall_band(origin, d, n, length, base_top, z1, upper, wall, u_off)
            else:
                f.wall_band(origin, d, n, length, z0, z1, lower + upper, wall, u_off)
            if st.pilasters and nb >= 2:
                for j in range(1, nb):
                    f.pilaster(origin, d, n, j * bw, base_top, z1 - (0.65 if st.cornices else 0))
            u_off += length
    if st.cornices:  # dentils under the main cornice (fine detail for the bake)
        for ring in rings:
            for origin, d, n, length in _edges(ring):
                ornaments.dentils(f.ornaments, origin, d, n, length, z1 - 0.65)
    f.finish()
    if st.cornices:
        for ring in rings:
            for k in range(1, n_st):
                band(mesh, ring, z0 + k * sh - 0.14, STRING_COURSE, "moulding", trim_color, "near")
            band(mesh, ring, z1 - 0.65, MAIN_CORNICE, "moulding", trim_color)
    # From further away: the same walls with painted windows (a few triangles).
    _painted_walls(mesh, rings, z0, z1, flat, color, "far")
    if top:
        flat_top(mesh, rings, z1, top, top_color)
    return "detailed"


# Window grid of the painted facade materials (bay, storey), see materials.py.
PAINTED_GRID = {
    "windows": (2.6, 3.0),
    "windows_classic": (4.2, 4.8),
    "windows_arched": (4.6, 5.5),
    "windows_house": (3.6, 2.8),
}


def _ornament(st: FacadeStyle, storey: int, storeys: int) -> str:
    """Which window surround a style shows on which storey (piano nobile = the
    first floor above a rusticated base gets pediments in classic facades)."""
    if not st.cornices:
        return "frame" if st.depth >= 0.15 else "plain"
    if storey == 1 and storeys >= 3 and not st.arched:
        return "pediment"
    return "keystone"


def _painted_walls(mesh: Mesh, rings, z0: float, z1: float, mat: str, color, lod: str) -> None:
    """Walls with painted windows, built from tiles that all share one piece of the
    texture (a few bays by a few storeys): a big building costs no more texture
    than a small one."""
    bay, storey = PAINTED_GRID.get(mat.split("+", 1)[0], (4.0, 4.0))
    tw = bay * max(1, round(12.0 / bay))
    th = storey * max(1, round(10.0 / storey))
    if mat.startswith("photo:"):  # a photo is fitted onto each whole wall
        for ring in rings:
            for origin, d, _n, length in _edges(ring):
                a, b = origin, (origin[0] + d[0] * length, origin[1] + d[1] * length)
                q = quad_face(
                    [(a[0], a[1], z0), (b[0], b[1], z0), (b[0], b[1], z1), (a[0], a[1], z1)],
                    mat,
                    color,
                )
                q.lod = lod
                mesh.add(q)
        return
    patch = Patch(mat, color, lod=lod)
    for ring in rings:
        for origin, d, _n, length in _edges(ring):
            s = 0.0
            while s < length - 1e-6:
                w = min(tw, length - s)
                z = z0
                while z < z1 - 1e-6:
                    h = min(th, z1 - z)
                    a = (origin[0] + d[0] * s, origin[1] + d[1] * s)
                    b = (origin[0] + d[0] * (s + w), origin[1] + d[1] * (s + w))
                    _shared_quad(
                        patch,
                        [
                            (a[0], a[1], z),
                            (b[0], b[1], z),
                            (b[0], b[1], z + h),
                            (a[0], a[1], z + h),
                        ],
                        w,
                        h,
                    )
                    z += h
                s += w
    mesh.add(patch)


def parapet(
    mesh: Mesh, rings: list[list[Vec2]], z: float, height: float, mat: str, color, statues: int
) -> None:
    """Low wall along the roof edge (balustrade with mat="balustrade"), optionally
    with simple statues standing on it at even spacing."""
    profile = [(-0.05, 0.0), (-0.05, height), (0.35, height), (0.35, 0.0)]
    for ring in rings:
        band(mesh, ring, z, profile, mat, color)
    if statues <= 0:
        return
    outer = rings[0]
    perimeter = sum(length for _, _, _, length in _edges(outer))
    step = perimeter / statues
    figures = Patch("stone", color, lod="near")
    walked, k = 0.0, 0
    for origin, d, _n, length in _edges(outer):
        while k < statues and k * step + step / 2 < walked + length:
            s = k * step + step / 2 - walked
            x, y = origin[0] + d[0] * s, origin[1] + d[1] * s
            zb = z + height
            # body and head as small four-sided blocks: a figure against the sky
            for w, z_a, z_b in ((0.5, zb, zb + 1.3), (0.28, zb + 1.3, zb + 1.65)):
                h2 = w / 2
                c = [(x - h2, y - h2), (x + h2, y - h2), (x + h2, y + h2), (x - h2, y + h2)]
                for (ax, ay), (bx, by) in zip(c, c[1:] + c[:1], strict=True):
                    _shared_quad(
                        figures,
                        [(ax, ay, z_a), (bx, by, z_a), (bx, by, z_b), (ax, ay, z_b)],
                        w,
                        z_b - z_a,
                    )
            k += 1
        walked += length
    mesh.add(figures)


def chimneys(
    mesh: Mesh,
    rings: list[list[Vec2]],
    z0: float,
    z1: float,
    count: int,
    size: float,
    mat: str,
    color,
) -> int:
    spots = chimney_spots(rings, count, inset=max(1.5, size * 2))
    body = Patch(mat, color)
    caps = Patch("concrete", None)
    h = z1 - z0
    for x, y in spots:
        h2 = size / 2
        c = [(x - h2, y - h2), (x + h2, y - h2), (x + h2, y + h2), (x - h2, y + h2)]
        for (ax, ay), (bx, by) in zip(c, c[1:] + c[:1], strict=True):
            _shared_quad(body, [(ax, ay, z0), (bx, by, z0), (bx, by, z1), (ax, ay, z1)], size, h)
        g = h2 + 0.08  # a cap slab a little wider than the stack
        cap = [
            (x - g, y - g, z1 + 0.12),
            (x + g, y - g, z1 + 0.12),
            (x + g, y + g, z1 + 0.12),
            (x - g, y + g, z1 + 0.12),
        ]
        _shared_quad(caps, cap, 2 * g, 2 * g)
        lip = [(x - g, y - g), (x + g, y - g), (x + g, y + g), (x - g, y + g)]
        for (ax, ay), (bx, by) in zip(lip, lip[1:] + lip[:1], strict=True):
            _shared_quad(
                caps,
                [(ax, ay, z1), (bx, by, z1), (bx, by, z1 + 0.12), (ax, ay, z1 + 0.12)],
                2 * g,
                0.12,
            )
    mesh.add(body)
    mesh.add(caps)
    return len(spots)


def mansard(
    mesh: Mesh,
    rings: list[list[Vec2]],
    z: float,
    rise: float,
    depth: float,
    mat: str,
    color,
    top: str | None,
    top_color,
) -> None:
    """Sloped roof band along the outline and the courtyards, flat on top: the look of
    the pitched roofs of a big block from every side."""
    from xpsb.build.tools import _frames

    for _attempt in range(4):
        inner = []
        for ring in rings:
            frames = _frames([(x, y, z) for x, y in ring], True)
            inner.append([(p[0] + lx * depth, p[1] + ly * depth) for p, (lx, ly), _ in frames])
        top_poly = Polygon(inner[0], inner[1:])
        if top_poly.is_valid and top_poly.area > 1.0:
            break
        depth *= 0.5
    else:
        raise GeometryError("mansard: the building is too narrow for this roof depth")
    slope = Patch(mat, color)
    for ring, ins in zip(rings, inner, strict=True):
        count = len(ring)
        for i in range(count):
            a, b = ring[i], ring[(i + 1) % count]
            ai, bi = ins[i], ins[(i + 1) % count]
            length = math.dist(a, b)
            if length < 0.05:
                continue
            _shared_quad(
                slope,
                [
                    (a[0], a[1], z),
                    (b[0], b[1], z),
                    (bi[0], bi[1], z + rise),
                    (ai[0], ai[1], z + rise),
                ],
                min(length, 30.0),
                math.hypot(depth, rise),
            )
    mesh.add(slope)
    if top:
        flat_top(mesh, inner, z + rise, top, top_color)
