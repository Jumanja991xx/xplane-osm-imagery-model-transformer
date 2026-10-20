"""Turns the buildings of a block of the city into two X-Plane objects:

- walls, textured from the shared facade atlas: every wall is cut into window
  axes and storeys, each piece mapped onto its cell (so a facade of any length
  and height shows whole windows and a ground floor at the bottom);
- roofs, the real roof faces (official 3D data) or generated ones, textured
  from above with the block's own roof image (aerial photo or painted).

All buildings of a block share one origin; each keeps its real ground height
relative to the others (official data), and walls reach a little below the
ground so they do not float on sloping terrain.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.strtree import STRtree

from xpsb.build.mesh import GeometryError, _triangulate_2d
from xpsb.city import life as life_mod
from xpsb.city import utm
from xpsb.city.atlas import ATTIC_M, BAYS, FLOOR_ROWS, Atlas, Cell
from xpsb.city.model import CityBuilding
from xpsb.geo import METERS_PER_DEG_LAT, meters_per_deg_lon
from xpsb.xplane.obj8 import Obj8, Vertex

BASEMENT_M = 2.5
MAX_STEP_M = 25.0  # ground height differences inside a block beyond this are clamped
ROOF_PITCH_DEG = 35.0
LOD_M = 8000

Vec3 = tuple[float, float, float]


@dataclass
class RoofFace:
    """A roof face for painting the roof image: outline from above and slope."""

    outline: list[tuple[float, float]]
    normal: Vec3
    material: str
    color: tuple[int, int, int]


@dataclass
class BlockMesh:
    walls: Obj8
    roofs: Obj8
    roof_faces: list[RoofFace]
    roof_bounds: tuple[float, float, float, float]  # x0, y0, x1, y1 (metres, local)
    details: Obj8 | None = None  # shop fronts, signs, awnings (sign atlas)
    buildings: int = 0
    triangles: int = 0
    skipped: list[str] = field(default_factory=list)


class Frame:
    """Local metres (x east, y north) around a block's origin."""

    def __init__(self, lon0: float, lat0: float):
        self.lon0, self.lat0 = lon0, lat0
        self.mx = meters_per_deg_lon(lat0)
        self._utm: dict[int, tuple[float, float, np.ndarray]] = {}

    def from_lonlat(self, ring: list[tuple[float, float]]) -> list[tuple[float, float]]:
        return [
            ((lon - self.lon0) * self.mx, (lat - self.lat0) * METERS_PER_DEG_LAT)
            for lon, lat in ring
        ]

    def utm_matrix(self, zone: int) -> tuple[float, float, np.ndarray]:
        """UTM -> local as an affine map (exact to millimetres within a block; it
        includes the grid's rotation against true north)."""
        if zone not in self._utm:
            e0, n0 = utm.from_lonlat(self.lon0, self.lat0, zone)
            cols = []
            for de, dn in ((100.0, 0.0), (0.0, 100.0)):
                lon, lat = utm.to_lonlat(e0 + de, n0 + dn, zone)
                x, y = self.from_lonlat([(lon, lat)])[0]
                cols.append((x / 100.0, y / 100.0))
            self._utm[zone] = (e0, n0, np.array(cols).T)
        return self._utm[zone]

    def from_utm(self, pts: list[Vec3], zone: int, z0: float) -> list[Vec3]:
        e0, n0, m = self.utm_matrix(zone)
        a = np.asarray(pts, dtype=np.float64)
        xy = (a[:, :2] - (e0, n0)) @ m.T
        return [(float(x), float(y), float(z - z0)) for (x, y), z in zip(xy, a[:, 2], strict=True)]


def _newell(pts: list[Vec3]) -> Vec3:
    nx = ny = nz = 0.0
    for (x1, y1, z1), (x2, y2, z2) in zip(pts, pts[1:] + pts[:1], strict=True):
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length < 1e-9:
        return (0.0, 0.0, 0.0)
    return (nx / length, ny / length, nz / length)


def _bands(eave: float, cell: Cell) -> list[tuple[float, float, str]]:
    """Height bands of a wall (bottom, top, atlas row) from the ground to 60 m above
    the eaves: ground floor, upper floors fitted to the eave height, attic."""
    g, f = cell.ground_m, cell.storey_m
    bands: list[tuple[float, float, str]] = []
    if eave <= g * 1.3:
        bands.append((0.0, max(eave, 0.5), "ground"))
    else:
        k = max(1, round((eave - g) / f))
        step = (eave - g) / k
        bands.append((0.0, g, "ground"))
        bands += [(g + i * step, g + (i + 1) * step, f"floor{i % FLOOR_ROWS}") for i in range(k)]
    top = bands[-1][1]
    bands += [(top + j * ATTIC_M, top + (j + 1) * ATTIC_M, "attic") for j in range(30)]
    return bands


class _Builder:
    def __init__(self, atlas: Atlas):
        self.atlas = atlas
        self.walls = Obj8(texture="", comment="xpsb city walls")
        self.roofs = Obj8(texture="", comment="xpsb city roofs")
        self.roof_tris: list[tuple[Vec3, Vec3, Vec3, Vec3]] = []  # a, b, c, normal
        self.roof_faces: list[RoofFace] = []
        self.details = Obj8(texture="", comment="xpsb city shops")

    def quad(self, obj: Obj8, corners: list[Vec3], rect, two_sided: bool = False) -> None:
        """A textured quad (local metres, corners counter-clockwise seen from the
        front, starting bottom left) showing the uv rect (s0, t0, s1, t1)."""
        a, b, c, d = corners
        u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        v = (d[0] - a[0], d[1] - a[1], d[2] - a[2])
        n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        length = math.sqrt(sum(x * x for x in n)) or 1.0
        n = (n[0] / length, n[1] / length, n[2] / length)
        s0, t0, s1, t1 = rect
        uv = [(s0, t0), (s1, t0), (s1, t1), (s0, t1)]
        pts = [(p[0], p[2], -p[1]) for p in corners]
        obj.add_quad(pts, (n[0], n[2], -n[1]), uv)
        if two_sided:
            obj.add_quad(pts[::-1], (-n[0], -n[2], n[1]), uv[::-1])

    def tri(self, obj: Obj8, pts: list[Vec3], rect) -> None:
        """A textured triangle, counter-clockwise seen from the front."""
        a, b, c = pts
        u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
        n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        length = math.sqrt(sum(x * x for x in n)) or 1.0
        s0, t0, s1, t1 = rect
        base = len(obj.vertices)
        for p, (s_, t_) in zip(pts, ((s0, t0), (s1, t0), (s1, t1)), strict=True):
            obj.vertices.append(
                Vertex(p[0], p[2], -p[1], n[0] / length, n[2] / length, -n[1] / length, s_, t_)
            )
        obj.indices += [base, base + 2, base + 1]

    def box(
        self,
        x: float,
        y: float,
        z: float,
        w: float,
        d: float,
        h: float,
        ang: float,
        rect,
        top_rect=None,
    ) -> None:
        """An upright box (chimney, roof unit) centred on x, y, from z up, turned by ang."""
        c, s_ = math.cos(ang), math.sin(ang)

        def pt(dx, dy, dz):
            return (x + dx * c - dy * s_, y + dx * s_ + dy * c, z + dz)

        corners = [(-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2)]
        for i in range(4):
            (x1, y1), (x2, y2) = corners[i], corners[(i + 1) % 4]
            self.quad(
                self.walls, [pt(x1, y1, 0), pt(x2, y2, 0), pt(x2, y2, h), pt(x1, y1, h)], rect
            )
        top = [pt(cx, cy, h) for cx, cy in corners]
        self.quad(self.walls, top, top_rect or rect)

    # -- walls ---------------------------------------------------------------------

    def wall(self, pts: list[Vec3], base_z: float, eave: float, cell: Cell) -> None:
        """A planar wall polygon (local metres, z above the block origin)."""
        n = _newell(pts)
        if abs(n[2]) > 0.7 or n == (0.0, 0.0, 0.0):
            return  # not a wall
        h = math.hypot(n[0], n[1])
        ux, uy = -n[1] / h, n[0] / h  # along the wall, left to right seen from outside
        p0 = pts[0]
        uz = [((p[0] - p0[0]) * ux + (p[1] - p0[1]) * uy, p[2] - base_z) for p in pts]
        poly = Polygon(uz)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.area < 0.05:
            return
        u0, z0, u1, z1 = poly.bounds
        length = u1 - u0
        bays = max(1, round(length / cell.bay_m))
        bay = length / bays
        bands = [b for b in _bands(eave, cell) if b[1] > z0 and b[0] < z1]
        if z0 < 0:
            bands.insert(0, (z0, 0.0, "plinth"))
        boxes, info = [], []
        for i in range(bays):
            for lo, hi, row in bands:
                boxes.append(shapely.box(u0 + i * bay, lo, u0 + (i + 1) * bay, hi))
                info.append((u0 + i * bay, bay, lo, hi, row, i % BAYS))
        pieces = shapely.intersection(poly, np.array(boxes))
        convex = abs(poly.convex_hull.area - poly.area) < 1e-3
        cw = cell.s1 - cell.s0
        normal = (n[0], n[2], -n[1])  # OBJ axes
        for piece, (bu, bw, lo, hi, row, sub) in zip(pieces, info, strict=True):
            if piece.is_empty or piece.area < 1e-4:
                continue
            t0, t1 = cell.rows[row]
            s0 = cell.s0 + cw * sub / BAYS
            s1 = s0 + cw / BAYS

            def uv(u, z, bu=bu, bw=bw, lo=lo, hi=hi, s0=s0, s1=s1, t0=t0, t1=t1):
                return (
                    s0 + (s1 - s0) * min(1.0, max(0.0, (u - bu) / bw)),
                    t0 + (t1 - t0) * min(1.0, max(0.0, (z - lo) / (hi - lo))),
                )

            for part in getattr(piece, "geoms", [piece]):
                if part.geom_type != "Polygon" or part.area < 1e-4:
                    continue
                ring = list(orient(part, 1.0).exterior.coords)[:-1]
                if convex and not part.interiors:
                    tris = [(0, k, k + 1) for k in range(1, len(ring) - 1)]
                else:
                    try:
                        tris = _triangulate_2d(ring)
                    except GeometryError:
                        continue
                base = len(self.walls.vertices)
                for u, z in ring:
                    s, t = uv(u, z)
                    self.walls.vertices.append(
                        Vertex(p0[0] + u * ux, z + base_z, -(p0[1] + u * uy), *normal, s, t)
                    )
                for a, b, c in tris:  # counter-clockwise from the front -> X-Plane order
                    self.walls.indices += [base + a, base + c, base + b]

    def plinth(self, ring: list[tuple[float, float]], base_z: float, cell: Cell, top: float):
        """Walls from below the ground up to `top` along a footprint ring (counter-
        clockwise outer ring, clockwise courtyards: outward normals)."""
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1], strict=True):
            if math.hypot(x2 - x1, y2 - y1) < 0.05:
                continue
            quad = [
                (x1, y1, base_z - BASEMENT_M),
                (x2, y2, base_z - BASEMENT_M),
                (x2, y2, base_z + top),
                (x1, y1, base_z + top),
            ]
            self.wall(quad, base_z, 1e9 if top <= 0.01 else top, cell)

    # -- roofs ---------------------------------------------------------------------

    def roof(self, pts: list[Vec3], material: str, color) -> None:
        n = _newell(pts)
        if n == (0.0, 0.0, 0.0):
            return
        if n[2] < 0:  # always face up
            pts = pts[::-1]
            n = (-n[0], -n[1], -n[2])
        if n[2] < 0.08:
            return  # vertical: a wall, not a roof
        # plane basis: e1 horizontal along the face, e2 = n x e1
        if abs(n[2]) > 0.999:
            e1 = (1.0, 0.0, 0.0)
        else:
            hl = math.hypot(n[0], n[1])
            e1 = (-n[1] / hl, n[0] / hl, 0.0)
        e2 = (
            n[1] * e1[2] - n[2] * e1[1],
            n[2] * e1[0] - n[0] * e1[2],
            n[0] * e1[1] - n[1] * e1[0],
        )
        p0 = pts[0]
        flat = [
            (
                (p[0] - p0[0]) * e1[0] + (p[1] - p0[1]) * e1[1] + (p[2] - p0[2]) * e1[2],
                (p[0] - p0[0]) * e2[0] + (p[1] - p0[1]) * e2[1] + (p[2] - p0[2]) * e2[2],
            )
            for p in pts
        ]
        try:
            tris = _triangulate_2d(flat)
        except GeometryError:
            poly = Polygon(flat).buffer(0)
            if poly.is_empty or poly.geom_type != "Polygon":
                return
            # rebuild from the repaired outline (rare: self-touching official faces)
            ring = list(orient(poly, 1.0).exterior.coords)[:-1]
            pts = [
                (
                    p0[0] + a * e1[0] + b * e2[0],
                    p0[1] + a * e1[1] + b * e2[1],
                    p0[2] + a * e1[2] + b * e2[2],
                )
                for a, b in ring
            ]
            try:
                tris = _triangulate_2d(ring)
            except GeometryError:
                return
        for a, b, c in tris:
            self.roof_tris.append((pts[a], pts[b], pts[c], n))
        self.roof_faces.append(RoofFace([(p[0], p[1]) for p in pts], n, material, color))

    def finish_roofs(self) -> tuple[float, float, float, float]:
        if not self.roof_tris:
            return (0.0, 0.0, 1.0, 1.0)
        xs = [p[0] for tri in self.roof_tris for p in tri[:3]]
        ys = [p[1] for tri in self.roof_tris for p in tri[:3]]
        x0, y0, x1, y1 = min(xs) - 1, min(ys) - 1, max(xs) + 1, max(ys) + 1
        for a, b, c, n in self.roof_tris:
            base = len(self.roofs.vertices)
            for p in (a, b, c):
                s = (p[0] - x0) / (x1 - x0)
                t = (p[1] - y0) / (y1 - y0)
                self.roofs.vertices.append(Vertex(p[0], p[2], -p[1], n[0], n[2], -n[1], s, t))
            self.roofs.indices += [base, base + 2, base + 1]
        return (x0, y0, x1, y1)


# ---- generated roofs (no official 3D data) --------------------------------------------


PITCHED = ("gabled", "hipped", "mansard", "mixed", "pyramid")


@dataclass
class Slope:
    """A roof slope rising from the wall edge p -> q towards its left (inside)."""

    p: tuple[float, float]
    q: tuple[float, float]
    depth: float  # horizontal reach from the wall
    eave: float
    rise_per_m: float


@dataclass
class RoofInfo:
    """What the details (dormers, chimneys, roof equipment) need to know."""

    kind: str  # "ring", "gabled", "hipped", "flat"
    eave: float
    top_z: float
    slopes: list[Slope] = field(default_factory=list)
    top: Polygon | None = None  # flat part (flat roofs, top of ring roofs)
    ridge: list[tuple[float, float]] = field(default_factory=list)


RING_ROOF_DEPTH_M = 6.0  # how far the slopes reach in from the walls


def _inset_ring(ring: list[tuple[float, float]], d: float) -> list[tuple[float, float]] | None:
    """Every edge moved `d` to its left (into the building for an outer ring that runs
    counter-clockwise and for courtyards that run clockwise), corners mitred."""
    n = len(ring)
    lines = []
    for i in range(n):
        (x1, y1), (x2, y2) = ring[i], ring[(i + 1) % n]
        length = math.hypot(x2 - x1, y2 - y1)
        if length < 1e-6:
            return None
        nx, ny = -(y2 - y1) / length, (x2 - x1) / length
        lines.append(((x1 + nx * d, y1 + ny * d), ((x2 - x1) / length, (y2 - y1) / length)))
    out = []
    for i in range(n):
        (p, u), (q, v) = lines[i - 1], lines[i]
        den = u[0] * v[1] - u[1] * v[0]
        if abs(den) < 1e-6:  # straight on: just shift the corner
            out.append(q)
            continue
        t = ((q[0] - p[0]) * v[1] - (q[1] - p[1]) * v[0]) / den
        x, y = p[0] + u[0] * t, p[1] + u[1] * t
        if math.hypot(x - ring[i][0], y - ring[i][1]) > 3 * d:  # spike at a sharp corner
            return None
        out.append((x, y))
    return out


def sloped_ring_roof(outline: Polygon, eave: float) -> tuple[list[list[Vec3]], RoofInfo] | None:
    """A pitched roof that follows any footprint, also around courtyards (the city
    block of the 19th century): slopes rise from every wall inwards, a flat top
    (or the ridge) in between. None if the shape does not allow it."""
    poly = orient(outline.simplify(0.3), 1.0)
    if poly.geom_type != "Polygon" or poly.is_empty:
        return None
    rings = [list(poly.exterior.coords)[:-1]] + [list(r.coords)[:-1] for r in poly.interiors]
    rise_per_m = math.tan(math.radians(ROOF_PITCH_DEG + 5))
    for d in (RING_ROOF_DEPTH_M, RING_ROOF_DEPTH_M * 0.65, RING_ROOF_DEPTH_M * 0.4):
        insets = [_inset_ring(r, d) for r in rings]
        if any(i is None or len(i) < 3 for i in insets):
            continue
        top = Polygon(insets[0], insets[1:])
        if not top.is_valid or top.area < 1.0 or not poly.buffer(0.01).contains(top):
            continue
        # every inset ring must keep its direction (else it turned inside out)
        if any(
            (Polygon(i).exterior.is_ccw) != (Polygon(r).exterior.is_ccw)
            for i, r in zip(insets, rings, strict=True)
        ):
            continue
        z1 = eave + d * rise_per_m
        faces: list[list[Vec3]] = []
        info = RoofInfo("ring", eave, z1, top=top)
        for ring, inset in zip(rings, insets, strict=True):
            n = len(ring)
            for i in range(n):
                j = (i + 1) % n
                info.slopes.append(Slope(ring[i], ring[j], d, eave, rise_per_m))
                faces.append(
                    [
                        (*ring[i], eave),
                        (*ring[j], eave),
                        (*inset[j], z1),
                        (*inset[i], z1),
                    ]
                )
        for tri in shapely.constrained_delaunay_triangles(top).geoms:
            faces.append([(x, y, z1) for x, y in list(tri.exterior.coords)[:3]])
        return faces, info
    return None


def generated_faces(
    outline: Polygon, eave: float, roof_type: str
) -> tuple[list[list[Vec3]], list[list[Vec3]], RoofInfo]:
    """(walls above the eaves, roof faces) for a footprint in local metres. Gabled
    and hipped roofs on (nearly) rectangular houses, flat roofs otherwise."""
    rect = outline.minimum_rotated_rectangle
    simple = (
        roof_type in ("gabled", "hipped", "pyramid", "mansard", "mixed")
        and rect.geom_type == "Polygon"
        and outline.area / max(rect.area, 1e-6) > 0.85
        and len(outline.interiors) == 0
    )
    if not simple and roof_type in PITCHED:
        ring_roof = sloped_ring_roof(outline, eave)
        if ring_roof:
            return [], ring_roof[0], ring_roof[1]
    if not simple:
        rings = [list(orient(outline, 1.0).exterior.coords)[:-1]]
        rings += [list(r.coords)[:-1] for r in orient(outline, 1.0).interiors]
        roof = [(x, y, eave) for x, y in rings[0]]
        info = RoofInfo("flat", eave, eave, top=outline)
        if len(rings) > 1:  # courtyards: flat roof as one face per triangle
            flat = Polygon(rings[0], rings[1:])
            faces = []
            for tri in shapely.constrained_delaunay_triangles(flat).geoms:
                faces.append([(x, y, eave) for x, y in list(tri.exterior.coords)[:3]])
            return [], faces, info
        return [], [roof], info
    c = list(rect.exterior.coords)[:4]
    e01 = math.dist(c[0], c[1])
    e12 = math.dist(c[1], c[2])
    if e01 < e12:  # c0-c1 is a long side
        c = c[1:] + c[:1]
    a, b, cc, d = c  # a-b long side, b-c short side
    width = math.dist(b, cc)
    rise = width / 2 * math.tan(math.radians(ROOF_PITCH_DEG))
    mid_ad = ((a[0] + d[0]) / 2, (a[1] + d[1]) / 2)
    mid_bc = ((b[0] + cc[0]) / 2, (b[1] + cc[1]) / 2)
    top = eave + rise
    rpm = math.tan(math.radians(ROOF_PITCH_DEG))
    centre = outline.centroid

    def slope(p, q) -> Slope:  # inside must be on the left
        left = (-(q[1] - p[1]), q[0] - p[0])
        if left[0] * (centre.x - p[0]) + left[1] * (centre.y - p[1]) < 0:
            p, q = q, p
        return Slope(p, q, width / 2, eave, rpm)

    info = RoofInfo(
        "gabled" if roof_type == "gabled" else "hipped",
        eave,
        top,
        slopes=[slope(a, b), slope(cc, d)],
    )
    if roof_type == "gabled":
        info.ridge = [mid_ad, mid_bc]
        r1, r2 = (*mid_ad, top), (*mid_bc, top)
        roofs = [
            [(*a, eave), (*b, eave), r2, r1],
            [(*cc, eave), (*d, eave), r1, r2],
        ]
        walls = [[(*b, eave), (*cc, eave), r2], [(*d, eave), (*a, eave), r1]]
        return walls, roofs, info
    # hipped / pyramid: ridge shortened by half the width at both ends
    length = math.dist(a, b)
    inset = min(width / 2, length / 2) / max(length, 1e-6)
    r1 = (mid_ad[0] + (mid_bc[0] - mid_ad[0]) * inset, mid_ad[1] + (mid_bc[1] - mid_ad[1]) * inset)
    r2 = (mid_bc[0] + (mid_ad[0] - mid_bc[0]) * inset, mid_bc[1] + (mid_ad[1] - mid_bc[1]) * inset)
    t1, t2 = (*r1, top), (*r2, top)
    info.ridge = [r1, r2]
    roofs = [
        [(*a, eave), (*b, eave), t2, t1],
        [(*b, eave), (*cc, eave), t2],
        [(*cc, eave), (*d, eave), t1, t2],
        [(*d, eave), (*a, eave), t1],
    ]
    roofs = [r if len(set(r)) == len(r) else list(dict.fromkeys(r)) for r in roofs]
    return [], [r for r in roofs if len(r) >= 3], info


# ---- a block ----------------------------------------------------------------------


def build_block(
    buildings: list[CityBuilding],
    frame: Frame,
    atlas: Atlas,
    roof_material: dict[str, str],
    life: Life | None = None,
) -> BlockMesh:
    """`roof_material`: style id -> roof material (tiles, metal, gravel ...).
    `life`: shops and details (see life.py); None: plain buildings."""
    grounds = sorted(b.ground_z for b in buildings if b.ground_z is not None)
    z_origin = grounds[len(grounds) // 2] if grounds else 0.0
    bld = _Builder(atlas)
    skipped = []
    neighbours: list[Polygon] = []
    if life is not None:
        for b in buildings:
            try:
                poly = Polygon(frame.from_lonlat(b.footprint[0]))
            except ValueError:
                continue
            if poly.is_valid:
                neighbours.append(poly)
        life.tree = STRtree(neighbours) if neighbours else None
    for b in buildings:
        cell = atlas.cells.get((b.style, b.variant))
        if cell is None:
            skipped.append(b.id)
            continue
        dz = 0.0 if b.ground_z is None else max(-MAX_STEP_M, min(MAX_STEP_M, b.ground_z - z_origin))
        rings = [frame.from_lonlat(r) for r in b.footprint]
        try:
            outline = orient(Polygon(rings[0], rings[1:]), 1.0)
        except ValueError:
            skipped.append(b.id)
            continue
        if not outline.is_valid:
            outline = outline.buffer(0)
            if outline.geom_type != "Polygon" or outline.is_empty:
                skipped.append(b.id)
                continue
        outer = list(outline.exterior.coords)[:-1]
        inner = [list(r.coords)[:-1] for r in outline.interiors]
        color = b.roof_color or (150, 70, 52)
        material = roof_material.get(b.style, "tiles")
        if b.walls or b.roofs:
            for ring in [outer, *inner]:
                bld.plinth(ring, dz, cell, 0.0)
            zone = b.zone or 32
            assert b.ground_z is not None
            for w in b.walls:
                bld.wall(frame.from_utm(w, zone, z_origin), dz, b.eave_m, cell)
            for r in b.roofs:
                bld.roof(frame.from_utm(r, zone, z_origin), material, color)
        else:
            eave = max(2.5, b.eave_m)
            for ring in [outer, *inner]:
                bld.plinth(ring, dz, cell, eave)
            if life is not None and b.roof_type == "flat":
                if life_mod.chance(b.id + "green", 0.12):
                    material, color = "green", (84, 118, 58)
                elif life_mod.chance(b.id + "deck", 0.08):
                    material, color = "deck", (150, 112, 76)
            walls, roofs, info = generated_faces(outline, eave, b.roof_type)
            for w in walls:
                bld.wall([(x, y, z + dz) for x, y, z in w], dz, eave, cell)
            for r in roofs:
                bld.roof([(x, y, z + dz) for x, y, z in r], material, color)
            if life is not None:
                add_life(bld, b, outline, info, cell, dz, life, neighbours, material, color)
    bounds = bld.finish_roofs()
    for obj in (bld.walls, bld.roofs, bld.details):
        obj.attributes = [f"ATTR_LOD 0 {LOD_M}"]
    triangles = (len(bld.walls.indices) + len(bld.roofs.indices) + len(bld.details.indices)) // 3
    return BlockMesh(
        details=bld.details,
        walls=bld.walls,
        roofs=bld.roofs,
        roof_faces=bld.roof_faces,
        roof_bounds=bounds,
        buildings=len(buildings) - len(skipped),
        triangles=triangles,
        skipped=skipped,
    )


# ---- life: shops, cornices, oriels, dormers, chimneys, roof units ---------------------


@dataclass
class Life:
    shops: dict[str, list[life_mod.Shop]]  # building id -> shops in it
    signs: life_mod.SignAtlas
    frame_to_local: object = None  # (lon, lat) -> local, set per block
    tree: object = None
    styles_with_cornice: tuple[str, ...] = life_mod.HISTORIC


def _row_rect(cell: Cell, row: str, sub: int = 0, frac: tuple[float, float] = (0.0, 1.0)):
    """uv rect of a row of a cell, one window axis wide (part of it with frac)."""
    cw = (cell.s1 - cell.s0) / BAYS
    s0 = cell.s0 + cw * sub
    t0, t1 = cell.rows[row]
    return (s0 + cw * frac[0], t0, s0 + cw * frac[1], t1)


def _outer_edges(outline: Polygon):
    ring = list(outline.exterior.coords)[:-1]
    return [(ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring))]


def add_life(
    bld,
    b,
    outline: Polygon,
    info: RoofInfo,
    cell: Cell,
    dz: float,
    life: Life,
    neighbours: list[Polygon],
    material: str,
    color,
) -> None:
    others = []
    if life.tree is not None:
        others = [neighbours[i] for i in life.tree.query(outline.buffer(1.5))]
    own = next((o for o in others if o.equals_exact(Polygon(outline.exterior), 0.01)), None)
    others = [o for o in others if o is not own]
    eave = info.eave
    edges = []
    for p, q in _outer_edges(outline):
        if math.dist(p, q) < 1.0:
            continue
        mid = life_mod.along(p, q, math.dist(p, q) / 2, -0.8)
        party = any(o.contains(Point(mid)) for o in others)
        edges.append((p, q, party))
    street = [(p, q) for p, q, party in edges if not party]

    # cornice under the eaves of historic houses (street sides only)
    if b.style in life.styles_with_cornice and eave > 6:
        out, hgt = 0.45, 0.75
        for p, q in street:
            length = math.dist(p, q)
            n = max(1, round(length / cell.bay_m))
            for k in range(n):
                a = life_mod.along(p, q, length * k / n, 0.0)
                c = life_mod.along(p, q, length * (k + 1) / n, 0.0)
                ao = life_mod.along(p, q, length * k / n, -out)
                co = life_mod.along(p, q, length * (k + 1) / n, -out)
                z0, z1 = dz + eave - hgt, dz + eave + 0.08
                rect = _row_rect(cell, "cornice", k % BAYS)
                bld.quad(bld.walls, [(*ao, z0), (*co, z0), (*co, z1), (*ao, z1)], rect)
                bld.quad(bld.walls, [(*a, z0), (*c, z0), (*co, z0), (*ao, z0)], rect)  # soffit
                bld.quad(bld.walls, [(*ao, z1), (*co, z1), (*c, z1), (*a, z1)], rect)  # top
            # close the ends: at p facing back along the wall, at q facing forward
            z0, z1 = dz + eave - hgt, dz + eave + 0.08
            rect = _row_rect(cell, "cornice", 0, (0, 0.1))
            ip, op = life_mod.along(p, q, 0.0, 0.0), life_mod.along(p, q, 0.0, -out)
            iq, oq = life_mod.along(p, q, length, 0.0), life_mod.along(p, q, length, -out)
            bld.quad(bld.walls, [(*ip, z0), (*op, z0), (*op, z1), (*ip, z1)], rect)
            bld.quad(bld.walls, [(*oq, z0), (*iq, z0), (*iq, z1), (*oq, z1)], rect)

    # oriel (Erker) in the middle of the longest street front
    levels = b.levels or 1
    if (
        b.style in life_mod.ERKER_STYLES
        and levels >= 4
        and street
        and life_mod.chance(b.id + "erker", 0.55)
    ):
        p, q = max(street, key=lambda e: math.dist(*e))
        length = math.dist(p, q)
        if length >= 12:
            w, dep = cell.bay_m, 0.9
            t0 = length / 2 - w / 2
            z0, z1 = dz + cell.ground_m + 0.3, dz + eave - 0.9
            a0, a1 = life_mod.along(p, q, t0, 0), life_mod.along(p, q, t0 + w, 0)
            f0, f1 = life_mod.along(p, q, t0, -dep), life_mod.along(p, q, t0 + w, -dep)
            # front: windows like the facade (wall() fits axes and storeys)
            bld.wall([(*f0, z0), (*f1, z0), (*f1, z1), (*f0, z1)], dz, eave, cell)
            side = _row_rect(cell, "attic", 0, (0, 0.3))
            bld.quad(bld.walls, [(*a0, z0), (*f0, z0), (*f0, z1), (*a0, z1)], side)
            bld.quad(bld.walls, [(*f1, z0), (*a1, z0), (*a1, z1), (*f1, z1)], side)
            bld.quad(
                bld.walls,
                [(*a0, z0), (*a1, z0), (*f1, z0), (*f0, z0)],
                _row_rect(cell, "cornice", 1),
            )
            bld.roof(
                [(*a0, z1), (*f0, z1 - 0.4), (*f1, z1 - 0.4), (*a1, z1)][::-1], material, color
            )

    # shop fronts with real names (ground floor, street side next to the shop)
    for shop in life.shops.get(b.id, []):
        if not street or life.frame_to_local is None:
            break
        x, y = life.frame_to_local(shop.lon, shop.lat)
        pt = Point(x, y)

        p, q = min(street, key=lambda e, pt=pt: LineString(e).distance(pt))
        length = math.dist(p, q)
        if length < 3:
            continue
        t = LineString([p, q]).project(pt)
        w = min(5.0, length - 0.6)
        t = min(max(t - w / 2, 0.3), length - 0.3 - w)
        g = min(cell.ground_m, max(eave, 3.2))
        out = 0.04
        a, c = life_mod.along(p, q, t, -out), life_mod.along(p, q, t + w, -out)
        designs = life.signs.windows[shop.kind]
        win = designs[life_mod._h(shop.name) % len(designs)]
        z_top = dz + min(g - 0.9, 3.4)
        bld.quad(bld.details, [(*a, dz + 0.15), (*c, dz + 0.15), (*c, z_top), (*a, z_top)], win)
        sign = life.signs.signs[shop.sign] if shop.sign >= 0 else life.signs.plain[shop.kind]
        a2, c2 = life_mod.along(p, q, t, -0.12), life_mod.along(p, q, t + w, -0.12)
        s0z, s1z = z_top + 0.08, z_top + 0.62
        bld.quad(bld.details, [(*a2, s0z), (*c2, s0z), (*c2, s1z), (*a2, s1z)], sign)
        if shop.kind in life_mod.AWNING_KINDS and life_mod.chance(shop.name + "awning", 0.7):
            aw = life.signs.awnings[life_mod._h(shop.name) % len(life.signs.awnings)]
            hi_a, hi_c = life_mod.along(p, q, t, -0.15), life_mod.along(p, q, t + w, -0.15)
            lo_a, lo_c = life_mod.along(p, q, t, -1.4), life_mod.along(p, q, t + w, -1.4)
            zh, zl = z_top - 0.05, z_top - 0.75
            bld.quad(
                bld.details,
                [(*lo_a, zl), (*lo_c, zl), (*hi_c, zh), (*hi_a, zh)],
                aw,
                two_sided=True,
            )

    # roof: dormers on pitched roofs, chimneys, units on flat roofs
    rect_wall = _row_rect(cell, "attic", 2, (0.2, 0.5))
    rect_grey = _row_rect(cell, "plinth", 0)
    if info.slopes and b.style in life_mod.DORMER_STYLES:
        for k, sl in enumerate(info.slopes):
            length = math.dist(sl.p, sl.q)
            a_in, hgt, wd = 0.9, 1.45, 1.4
            back = a_in + hgt / sl.rise_per_m
            if length < 6 or sl.depth < back + 0.3 or not life_mod.chance(f"{b.id}d{k}", 0.6):
                continue
            count = max(1, int((length - 2.5) / 4.2))
            for m in range(count):
                tc = (m + 0.5) * length / count
                f0 = life_mod.along(sl.p, sl.q, tc - wd / 2, a_in)
                f1 = life_mod.along(sl.p, sl.q, tc + wd / 2, a_in)
                b0 = life_mod.along(sl.p, sl.q, tc - wd / 2, back)
                b1 = life_mod.along(sl.p, sl.q, tc + wd / 2, back)
                zb = dz + sl.eave + a_in * sl.rise_per_m
                zt = zb + hgt
                window = _row_rect(cell, "floor1", 1, (0.25, 0.75))
                bld.quad(bld.walls, [(*f0, zb), (*f1, zb), (*f1, zt), (*f0, zt)], window)
                bld.tri(bld.walls, [(*f0, zb), (*f0, zt), (*b0, zt)], rect_wall)  # cheeks
                bld.tri(bld.walls, [(*f1, zb), (*b1, zt), (*f1, zt)], rect_wall)
                bld.roof([(*f0, zt), (*f1, zt), (*b1, zt), (*b0, zt)], material, color)
    if info.kind in ("ring", "gabled", "hipped"):
        spots = []
        if info.ridge:
            r0, r1 = info.ridge
            spots = [
                (r0[0] + (r1[0] - r0[0]) * f, r0[1] + (r1[1] - r0[1]) * f) for f in (0.2, 0.75)
            ]
        elif info.top is not None and not info.top.is_empty:
            ring = list(info.top.exterior.coords)[:-1]
            spots = ring[:: max(1, len(ring) // 4)]
            spots = [
                ((x + info.top.centroid.x * 0.15) / 1.15, (y + info.top.centroid.y * 0.15) / 1.15)
                for x, y in spots
            ]
        for k, (x, y) in enumerate(spots[:5]):
            if life_mod.chance(f"{b.id}c{k}", 0.75):
                bld.box(x, y, dz + info.top_z - 0.6, 0.6, 0.6, 1.7, 0.0, rect_wall)
    elif (
        info.kind == "flat"
        and info.top is not None
        and outline.area > 60
        and material not in ("green", "deck")
    ):
        inner = outline.buffer(-2.0)
        if not inner.is_empty:
            n_units = min(6, int(outline.area // 180) + 1)
            x0, y0, x1, y1 = inner.bounds
            for k in range(n_units * 3):
                if n_units <= 0:
                    break
                hx = life_mod._h(f"{b.id}u{k}")
                x = x0 + (hx % 1000) / 1000 * (x1 - x0)
                y = y0 + ((hx >> 12) % 1000) / 1000 * (y1 - y0)
                if not inner.contains(Point(x, y)):
                    continue
                bld.box(x, y, dz + eave, 1.3, 0.8, 0.9, (hx % 90) * math.pi / 180, rect_grey)
                n_units -= 1
            if levels >= 5 and life_mod.chance(b.id + "lift", 0.7):
                c = inner.representative_point()
                bld.box(c.x, c.y, dz + eave, 3.0, 2.6, 2.6, 0.0, rect_wall)
