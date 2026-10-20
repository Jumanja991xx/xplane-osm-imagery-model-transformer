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
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

from xpsb.build.mesh import GeometryError, _triangulate_2d
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


def generated_faces(
    outline: Polygon, eave: float, roof_type: str
) -> tuple[list[list[Vec3]], list[list[Vec3]]]:
    """(walls above the eaves, roof faces) for a footprint in local metres. Gabled
    and hipped roofs on (nearly) rectangular houses, flat roofs otherwise."""
    rect = outline.minimum_rotated_rectangle
    simple = (
        roof_type in ("gabled", "hipped", "pyramid", "mansard", "mixed")
        and rect.geom_type == "Polygon"
        and outline.area / max(rect.area, 1e-6) > 0.85
        and len(outline.interiors) == 0
    )
    if not simple:
        rings = [list(orient(outline, 1.0).exterior.coords)[:-1]]
        rings += [list(r.coords)[:-1] for r in orient(outline, 1.0).interiors]
        roof = [(x, y, eave) for x, y in rings[0]]
        if len(rings) > 1:  # courtyards: flat roof as one face per triangle
            flat = Polygon(rings[0], rings[1:])
            faces = []
            for tri in shapely.constrained_delaunay_triangles(flat).geoms:
                faces.append([(x, y, eave) for x, y in list(tri.exterior.coords)[:3]])
            return [], faces
        return [], [roof]
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
    if roof_type == "gabled":
        r1, r2 = (*mid_ad, top), (*mid_bc, top)
        roofs = [
            [(*a, eave), (*b, eave), r2, r1],
            [(*cc, eave), (*d, eave), r1, r2],
        ]
        walls = [[(*b, eave), (*cc, eave), r2], [(*d, eave), (*a, eave), r1]]
        return walls, roofs
    # hipped / pyramid: ridge shortened by half the width at both ends
    length = math.dist(a, b)
    inset = min(width / 2, length / 2) / max(length, 1e-6)
    r1 = (mid_ad[0] + (mid_bc[0] - mid_ad[0]) * inset, mid_ad[1] + (mid_bc[1] - mid_ad[1]) * inset)
    r2 = (mid_bc[0] + (mid_ad[0] - mid_bc[0]) * inset, mid_bc[1] + (mid_ad[1] - mid_bc[1]) * inset)
    t1, t2 = (*r1, top), (*r2, top)
    roofs = [
        [(*a, eave), (*b, eave), t2, t1],
        [(*b, eave), (*cc, eave), t2],
        [(*cc, eave), (*d, eave), t1, t2],
        [(*d, eave), (*a, eave), t1],
    ]
    roofs = [r if len(set(r)) == len(r) else list(dict.fromkeys(r)) for r in roofs]
    return [], [r for r in roofs if len(r) >= 3]


# ---- a block ----------------------------------------------------------------------


def build_block(
    buildings: list[CityBuilding],
    frame: Frame,
    atlas: Atlas,
    roof_material: dict[str, str],
) -> BlockMesh:
    """`roof_material`: style id -> roof material (tiles, metal, gravel ...)."""
    grounds = sorted(b.ground_z for b in buildings if b.ground_z is not None)
    z_origin = grounds[len(grounds) // 2] if grounds else 0.0
    bld = _Builder(atlas)
    skipped = []
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
            walls, roofs = generated_faces(outline, eave, b.roof_type)
            for w in walls:
                bld.wall([(x, y, z + dz) for x, y, z in w], dz, eave, cell)
            for r in roofs:
                bld.roof([(x, y, z + dz) for x, y, z in r], material, color)
    bounds = bld.finish_roofs()
    for obj in (bld.walls, bld.roofs):
        obj.attributes = [f"ATTR_LOD 0 {LOD_M}"]
    triangles = (len(bld.walls.indices) + len(bld.roofs.indices)) // 3
    return BlockMesh(
        walls=bld.walls,
        roofs=bld.roofs,
        roof_faces=bld.roof_faces,
        roof_bounds=bounds,
        buildings=len(buildings) - len(skipped),
        triangles=triangles,
        skipped=skipped,
    )
