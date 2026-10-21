"""Statues: a human figure built from a few smooth body parts in a fixed pose.

Not a portrait of the real statue - a recognisable figure instead of a column:
head, torso, arms and legs (or a robe), what it holds in its hands, optionally
a horse under it. Near: about 1 000-2 500 triangles; from further away a much
cheaper stand-in of the same silhouette (lod "far").

Proportions are in units of the figure's own height H (top of the head of a
standing figure, eight heads tall); x to the figure's right, y forward, z up.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from xpsb.build.mesh import GeometryError, Mesh, Patch, Vec3

POSES = ("standing", "arm_raised", "arms_raised", "pointing", "seated", "rider", "bust")
HELD = ("none", "palm", "wreath", "sword", "torch", "book", "flag")

# How tall the whole figure is in units of H (raised arms and held things reach higher).
_EXTENT = {
    "standing": 1.0,
    "arm_raised": 1.2,
    "arms_raised": 1.2,
    "pointing": 1.0,
    "seated": 0.78,
    "rider": 1.42,
    "bust": 0.335,
}

Joint = tuple[float, float, float]


@dataclass
class Skeleton:
    hips: tuple[Joint, Joint]
    knees: tuple[Joint, Joint]
    ankles: tuple[Joint, Joint]
    toes: tuple[Joint, Joint]
    pelvis: Joint
    chest: Joint
    neck: Joint
    head: Joint
    shoulders: tuple[Joint, Joint]
    elbows: tuple[Joint, Joint]
    wrists: tuple[Joint, Joint]
    hold_at: Joint | None  # where a held thing sits (between the hands or in the right)


def _mirror(p: Joint) -> Joint:
    return (-p[0], p[1], p[2])


def skeleton(pose: str) -> Skeleton:
    """Joints of a figure of height 1 standing at the origin, facing +y."""
    dz = 0.0
    hip, knee, ankle, toe = (
        (0.09, 0.0, 0.50),
        (0.095, 0.01, 0.27),
        (0.09, -0.01, 0.045),
        (
            0.09,
            0.10,
            0.015,
        ),
    )
    if pose in ("seated", "rider"):
        dz = -0.20  # the upper body sits lower
        hip = (0.09, 0.0, 0.30)
        if pose == "seated":
            knee, ankle, toe = (0.10, 0.25, 0.30), (0.10, 0.27, 0.045), (0.10, 0.36, 0.015)
        else:  # legs around the horse's flanks
            knee, ankle, toe = (0.20, 0.10, 0.16), (0.19, 0.04, -0.06), (0.19, 0.12, -0.07)
    shoulder = (0.175, 0.0, 0.81 + dz)
    arms = {
        "standing": ((0.22, 0.0, 0.64), (0.23, 0.03, 0.48)),
        "seated": ((0.21, 0.12, 0.47), (0.17, 0.27, 0.37)),  # hands on the knees
        "rider": ((0.20, 0.14, 0.50), (0.12, 0.30, 0.48)),  # holding the reins
        "bust": ((0.22, 0.0, 0.64), (0.23, 0.03, 0.48)),
        "pointing": ((0.22, 0.0, 0.64), (0.23, 0.03, 0.48)),
        "arm_raised": ((0.22, 0.0, 0.64), (0.23, 0.03, 0.48)),
        "arms_raised": ((0.17, 0.02, 0.98), (0.08, 0.04, 1.12)),
    }
    elbow_l, wrist_l = arms.get(pose, arms["standing"])
    elbow_r, wrist_r = elbow_l, wrist_l
    hold: Joint | None = (0.24, 0.06, 0.46 + dz)
    if pose == "arm_raised":  # right arm up, a little forward
        elbow_r, wrist_r = (0.25, 0.04, 0.97), (0.27, 0.08, 1.12)
        hold = (0.27, 0.09, 1.15)
    elif pose == "pointing":  # right arm stretched forward
        elbow_r, wrist_r = (0.24, 0.17, 0.80), (0.25, 0.34, 0.80)
        hold = (0.25, 0.37, 0.80)
    elif pose == "arms_raised":  # both hands hold something high above the head
        hold = (0.0, 0.05, 1.15)
    elif pose in ("seated", "rider"):
        elbow_l, wrist_l = (
            (elbow_l[0], elbow_l[1], elbow_l[2] + dz),
            (
                wrist_l[0],
                wrist_l[1],
                wrist_l[2] + dz,
            ),
        )
        elbow_r, wrist_r = elbow_l, wrist_l
        hold = (wrist_r[0], wrist_r[1] + 0.02, wrist_r[2] + 0.05)
    return Skeleton(
        hips=(hip, _mirror(hip)),
        knees=(knee, _mirror(knee)),
        ankles=(ankle, _mirror(ankle)),
        toes=(toe, _mirror(toe)),
        pelvis=(0.0, 0.0, hip[2] + 0.02),
        chest=(0.0, 0.01, 0.71 + dz),
        neck=(0.0, 0.0, 0.85 + dz),
        head=(0.0, 0.01, 0.935 + dz),
        shoulders=(shoulder, _mirror(shoulder)),
        elbows=(elbow_r, _mirror(elbow_l)),
        wrists=(wrist_r, _mirror(wrist_l)),
        hold_at=hold,
    )


# ---- building blocks (all in the figure's frame, then placed by `_Frame`) ----------


class _Frame:
    """Figure frame -> object metres: scale, turn to `heading`, move to (x, y, z)."""

    def __init__(self, x: float, y: float, z: float, scale: float, heading: float):
        h = math.radians(heading)
        self.o = (x, y, z)
        self.s = scale
        self.f = (math.sin(h), math.cos(h))  # forward
        self.r = (math.cos(h), -math.sin(h))  # to the figure's right

    def p(self, q: Vec3) -> Vec3:
        X, Y, Z = q[0] * self.s, q[1] * self.s, q[2] * self.s
        return (
            self.o[0] + X * self.r[0] + Y * self.f[0],
            self.o[1] + X * self.r[1] + Y * self.f[1],
            self.o[2] + Z,
        )

    def n(self, q: Vec3) -> Vec3:
        return (
            q[0] * self.r[0] + q[1] * self.f[0],
            q[0] * self.r[1] + q[1] * self.f[1],
            q[2],
        )


def _unit(v: Vec3) -> Vec3:
    length = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2) or 1.0
    return (v[0] / length, v[1] / length, v[2] / length)


def _basis(axis: Vec3) -> tuple[Vec3, Vec3]:
    a = _unit(axis)
    ref = (0.0, 0.0, 1.0) if abs(a[2]) < 0.9 else (1.0, 0.0, 0.0)
    u = _unit(
        (
            a[1] * ref[2] - a[2] * ref[1],
            a[2] * ref[0] - a[0] * ref[2],
            a[0] * ref[1] - a[1] * ref[0],
        )
    )
    v = (a[1] * u[2] - a[2] * u[1], a[2] * u[0] - a[0] * u[2], a[0] * u[1] - a[1] * u[0])
    return u, v


class _Body:
    def __init__(self, frame: _Frame, patch: Patch):
        self.fr, self.patch = frame, patch

    def _vertex(self, q: Vec3, n: Vec3, uv: tuple[float, float]) -> int:
        return self.patch.add_vertex(
            self.fr.p(q), _unit(self.fr.n(n)), (uv[0] * self.fr.s, uv[1] * self.fr.s)
        )

    def _grid(self, rows: list[list[int]], closed: bool = True) -> None:
        for r0, r1 in zip(rows, rows[1:], strict=False):
            n = len(r0) - (0 if closed else 1)
            for i in range(n):
                j = (i + 1) % len(r0)
                a, b, c, d = r0[i], r0[j], r1[j], r1[i]
                self.patch.tris += [(a, b, c), (a, c, d)]

    def limb(self, a: Joint, b: Joint, ra: float, rb: float, segs: int) -> None:
        """Tapered round part from a to b (the ends are hidden in joints)."""
        axis = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        length = math.sqrt(sum(c * c for c in axis))
        if length < 1e-6:
            return
        u, v = _basis(axis)
        rows = []
        for t, (c, r) in enumerate(((a, ra), (b, rb))):
            row = []
            for i in range(segs + 1):
                ang = 2 * math.pi * i / segs
                d = tuple(math.cos(ang) * u[k] + math.sin(ang) * v[k] for k in range(3))
                q = (c[0] + d[0] * r, c[1] + d[1] * r, c[2] + d[2] * r)
                row.append(self._vertex(q, d, (ang * (ra + rb) / 2, t * length)))
            rows.append(row)
        self._grid(rows, closed=False)

    def blob(self, c: Joint, rx: float, ry: float, rz: float, segs: int, rings: int) -> None:
        """Ellipsoid (joints, head, hands, the horse's body parts)."""
        rows = []
        for j in range(rings + 1):
            lat = -math.pi / 2 + math.pi * j / rings
            row = []
            for i in range(segs + 1):
                lon = 2 * math.pi * i / segs
                e = (math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat))
                q = (c[0] + e[0] * rx, c[1] + e[1] * ry, c[2] + e[2] * rz)
                nrm = (e[0] / max(rx, 1e-6), e[1] / max(ry, 1e-6), e[2] / max(rz, 1e-6))
                row.append(self._vertex(q, nrm, (lon * rx, lat * rz)))
            rows.append(row)
        self._grid(rows, closed=False)

    def loft(self, sections: list[tuple[float, float, float, float, float]], segs: int) -> None:
        """Rings of ellipses (y, z, rx, ry, centre y offset) stacked upwards: torso, robe."""
        rows = []
        for y, z, rx, ry, _ in sections:
            row = []
            for i in range(segs + 1):
                ang = 2 * math.pi * i / segs
                cx, cy = math.cos(ang), math.sin(ang)
                q = (cx * rx, y + cy * ry, z)
                row.append(
                    self._vertex(q, (cx / max(rx, 1e-6), cy / max(ry, 1e-6), 0.0), (ang * rx, z))
                )
            rows.append(row)
        self._grid(rows, closed=False)
        top = sections[-1]
        if top[2] > 1e-3:  # close the top (shoulders of a bust, the robe's waist)
            centre = self._vertex((0.0, top[0], top[1]), (0.0, 0.0, 1.0), (0.0, 0.0))
            last = rows[-1]
            for i in range(segs):
                self.patch.tris.append((last[i], last[i + 1], centre))


# ---- the figure ---------------------------------------------------------------------


def _held(body: _Body, kind: str, at: Joint, segs: int) -> None:
    x, y, z = at
    if kind == "palm":  # a long frond held level: thin curved blade
        body.limb((x - 0.30, y, z + 0.02), (x, y, z + 0.05), 0.012, 0.022, 4)
        body.limb((x, y, z + 0.05), (x + 0.30, y, z + 0.02), 0.022, 0.012, 4)
    elif kind == "wreath":  # ring held up
        n = max(6, segs)
        pts = [
            (
                x + 0.07 * math.cos(2 * math.pi * i / n),
                y,
                z + 0.08 + 0.07 * math.sin(2 * math.pi * i / n),
            )
            for i in range(n + 1)
        ]
        for a, b in zip(pts, pts[1:], strict=False):
            body.limb(a, b, 0.016, 0.016, 4)
    elif kind == "sword":
        body.limb((x, y, z), (x, y + 0.02, z + 0.42), 0.012, 0.006, 4)
        body.limb((x - 0.05, y, z + 0.03), (x + 0.05, y, z + 0.03), 0.01, 0.01, 4)
    elif kind == "torch":
        body.limb((x, y, z - 0.06), (x, y, z + 0.14), 0.016, 0.022, 6)
        body.blob((x, y, z + 0.18), 0.035, 0.035, 0.06, 6, 4)
    elif kind == "book":
        body.blob((x, y + 0.03, z), 0.05, 0.02, 0.07, 4, 2)
    elif kind == "flag":
        body.limb((x, y, z - 0.25), (x, y, z + 0.45), 0.008, 0.008, 4)
        body.blob((x + 0.15, y, z + 0.33), 0.15, 0.006, 0.09, 4, 2)


def _horse(body: _Body, segs: int) -> None:
    """A standing horse under a rider; length about 1.3 H along y."""
    # body: an ellipsoid stretched along y, chest and rump as softer blobs
    body.blob((0.0, 0.0, 0.66), 0.21, 0.52, 0.22, segs, max(4, segs // 2))
    body.blob((0.0, 0.40, 0.68), 0.19, 0.18, 0.21, segs, 4)
    body.blob((0.0, -0.40, 0.68), 0.21, 0.20, 0.22, segs, 4)
    # neck up and forward, head
    body.limb((0.0, 0.45, 0.78), (0.0, 0.66, 1.08), 0.13, 0.08, segs)
    body.blob((0.0, 0.78, 1.04), 0.07, 0.17, 0.08, segs, 4)
    body.blob((0.0, 0.66, 1.10), 0.07, 0.07, 0.07, segs, 3)
    # legs: front pair a step forward, the right front one raised a little (walking)
    for side in (1.0, -1.0):
        x = 0.12 * side
        front_up = 0.10 if side > 0 else 0.0
        body.limb(
            (x, 0.42, 0.55), (x, 0.44 + front_up * 0.6, 0.30 + front_up), 0.07, 0.045, segs // 2 + 2
        )
        body.limb(
            (x, 0.44 + front_up * 0.6, 0.30 + front_up),
            (x, 0.42 + front_up, 0.02 + front_up),
            0.045,
            0.035,
            segs // 2 + 2,
        )
        body.limb((x, -0.42, 0.58), (x, -0.48, 0.30), 0.08, 0.045, segs // 2 + 2)
        body.limb((x, -0.48, 0.30), (x, -0.44, 0.02), 0.045, 0.035, segs // 2 + 2)
    # tail
    body.limb((0.0, -0.58, 0.76), (0.0, -0.70, 0.40), 0.05, 0.02, 5)


def _person(body: _Body, sk: Skeleton, robe: bool, pose: str, segs: int, near: bool) -> None:
    js = max(4, segs - 2)  # joints a little coarser than the limbs
    jr = max(3, js // 2)
    if pose != "bust":
        if robe and pose not in ("seated", "rider"):
            hz = sk.pelvis[2]
            body.loft(
                [
                    (0.02, 0.0, 0.17, 0.13, 0),
                    (0.01, hz * 0.45, 0.15, 0.11, 0),
                    (0.0, hz + 0.08, 0.12, 0.085, 0),
                ],
                segs,
            )
        else:
            for i in (0, 1):
                body.limb(sk.hips[i], sk.knees[i], 0.062, 0.045, segs)
                body.limb(sk.knees[i], sk.ankles[i], 0.045, 0.03, segs)
                body.limb(sk.ankles[i], sk.toes[i], 0.03, 0.028, max(4, segs // 2))
                if near:
                    body.blob(sk.knees[i], 0.046, 0.046, 0.046, js, jr)
        if robe and pose in ("seated", "rider"):  # a skirt over the thighs
            for i in (0, 1):
                body.limb(sk.hips[i], sk.knees[i], 0.09, 0.07, segs)
    # torso: hips, waist, chest, shoulders (a bust starts at the chest)
    p, c = sk.pelvis, sk.chest
    rings = [
        (0.0, p[2] - 0.06, 0.125, 0.085, 0),
        (0.0, p[2] + 0.05, 0.115, 0.078, 0),
        (0.01, c[2] - 0.04, 0.125, 0.085, 0),
        (0.015, c[2] + 0.04, 0.16, 0.095, 0),
        (0.0, sk.shoulders[0][2] - 0.01, 0.175, 0.085, 0),
        (0.0, sk.shoulders[0][2] + 0.025, 0.12, 0.07, 0),
        (0.0, sk.neck[2] - 0.01, 0.05, 0.045, 0),
    ]
    if pose == "bust":  # cut narrower, like a sculpted bust
        rings = [(y, z, rx * 0.8, ry * 0.9, o) for y, z, rx, ry, o in rings[2:]]
    elif not robe or pose in ("seated", "rider"):
        body.blob((0.0, 0.0, p[2] - 0.04), 0.13, 0.085, 0.08, segs, max(3, segs // 3))
    body.loft(rings, segs)
    body.limb(sk.neck, (sk.head[0], sk.head[1], sk.head[2] - 0.04), 0.04, 0.038, max(4, segs // 2))
    body.blob(sk.head, 0.054, 0.062, 0.07, segs, max(4, segs // 2))
    if near:  # nose and chin give the head a front
        hx, hy, hz = sk.head
        body.blob((hx, hy + 0.058, hz - 0.005), 0.012, 0.014, 0.022, 4, 2)
    # arms (a bust has none)
    if pose != "bust":
        for i in (0, 1):
            body.limb(sk.shoulders[i], sk.elbows[i], 0.042, 0.034, segs)
            body.limb(sk.elbows[i], sk.wrists[i], 0.034, 0.026, segs)
            if near:
                body.blob(sk.shoulders[i], 0.05, 0.05, 0.05, js, jr)
                body.blob(sk.elbows[i], 0.035, 0.035, 0.035, js, jr)
            hx, hy, hz = sk.wrists[i]
            body.blob(
                (hx, hy, hz + (0.035 if hz > sk.shoulders[i][2] else -0.035)),
                0.026,
                0.02,
                0.045,
                js,
                jr,
            )


def add_figure(
    mesh: Mesh,
    x: float,
    y: float,
    z: float,
    height: float,
    pose: str,
    heading: float,
    material: str,
    color,
    robe: bool,
    holding: str,
    horse_material: str | None = None,
) -> int:
    """Adds the figure; returns the triangles of its near version."""
    if pose not in POSES:
        raise GeometryError(f"figure: pose must be one of {', '.join(POSES)}")
    if holding not in HELD:
        raise GeometryError(f"figure: holding must be one of {', '.join(HELD)}")
    if not 0.2 <= height <= 80:
        raise GeometryError("figure: height must be between 0.2 and 80 m")
    unit = height / _EXTENT[pose]
    lift = 0.0
    if pose == "rider":
        lift = 0.62  # the rider sits on the horse's back
    elif pose == "bust":
        lift = -0.67  # only head and shoulders: start at the chest
    sk = skeleton(pose)
    before = mesh.triangle_count
    parts: list[Patch] = []
    for lod, segs in (("near", 12), ("far", 4)):
        frame = _Frame(x, y, z, unit, heading)
        patch = Patch(material, color, lod=lod)
        lifted = _Body(_Frame(x, y, z + lift * unit, unit, heading), patch)
        _person(lifted, sk, robe, pose, segs, lod == "near")
        if holding != "none" and sk.hold_at is not None and pose != "bust":
            _held(lifted, holding, sk.hold_at, segs)
        if pose == "seated":  # the block the figure sits on
            lifted.loft(
                [(-0.02, 0.0, 0.16, 0.16, 0), (-0.02, 0.27, 0.16, 0.16, 0)],
                4 if lod == "far" else 8,
            )
        parts.append(patch)
        if pose == "rider":
            hp = Patch(horse_material or material, color, lod=lod)
            _horse(_Body(frame, hp), segs)
            parts.append(hp)
    # Exactly the asked height, whatever the pose and the held thing add on top.
    top = max(p[2] for part in parts if part.lod == "near" for p in part.positions)
    k = height / max(top - z, 1e-6)
    for part in parts:
        part.positions = [
            (x + (px - x) * k, y + (py - y) * k, z + (pz - z) * k) for px, py, pz in part.positions
        ]
        part.uv_m = [(u * k, v * k) for u, v in part.uv_m]
        mesh.add(part)
    return mesh.triangle_count - before
