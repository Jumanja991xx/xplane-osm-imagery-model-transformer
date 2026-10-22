"""Fine facade detail that is never drawn by X-Plane: window surrounds, sills,
keystones, pediments over windows, dentils under cornices. Blender bakes its
relief and shading into the textures of the walls around it (high-to-low), so a
wall shows the detail of a hand-modelled facade at the cost of a flat one.
Without Blender these patches are simply left out.

Coordinates on a wall: s along the wall from `origin` in direction `d`, z up,
`out` metres in front of the wall (outward normal `n`).
"""

from __future__ import annotations

import math

from xpsb.build.mesh import Patch, Vec2, Vec3, quad_face

FRAME_W = 0.16  # width of a window surround
FRAME_OUT = 0.07  # how far it stands out of the wall
SILL_OUT = 0.14
SILL_H = 0.12


def _at(origin, d, n, s: float, z: float, out: float) -> Vec3:
    return (origin[0] + d[0] * s + n[0] * out, origin[1] + d[1] * s + n[1] * out, z)


def _quad(patch: Patch, corners: list[Vec3]) -> None:
    q = quad_face(corners, patch.material, None)
    base = len(patch.positions)
    for p in q.positions:
        patch.add_vertex(p, q.normals[0], (0.0, 0.0))
    patch.tris += [(base, base + 1, base + 2), (base, base + 2, base + 3)]


def block(patch: Patch, origin, d, n, s0, s1, z0, z1, out) -> None:
    """A small block on the wall with chamfered (sloping) sides, like a moulded
    stone: the slopes are what the normal-map bake turns into visible relief."""
    if s1 - s0 < 1e-3 or z1 - z0 < 1e-3 or out < 1e-3:
        return
    bev = min(out * 0.6, (s1 - s0) * 0.25, (z1 - z0) * 0.25)
    fs0, fs1, fz0, fz1 = s0 + bev, s1 - bev, z0 + bev, z1 - bev

    def p(s, z, o):
        return _at(origin, d, n, s, z, o)

    _quad(patch, [p(fs0, fz0, out), p(fs1, fz0, out), p(fs1, fz1, out), p(fs0, fz1, out)])
    _quad(patch, [p(fs0, fz1, out), p(fs1, fz1, out), p(s1, z1, 0), p(s0, z1, 0)])  # top
    _quad(patch, [p(s0, z0, 0), p(s1, z0, 0), p(fs1, fz0, out), p(fs0, fz0, out)])  # bottom
    _quad(patch, [p(fs1, fz0, out), p(s1, z0, 0), p(s1, z1, 0), p(fs1, fz1, out)])  # right
    _quad(patch, [p(s0, z0, 0), p(fs0, fz0, out), p(fs0, fz1, out), p(s0, z1, 0)])  # left


def window(patch: Patch, origin, d, n, outline: list[Vec2], kind: str) -> None:
    """Surround, sill and (per kind) keystone or pediment around one window.
    kind: "plain" (sill only), "frame", "keystone", "pediment"."""
    s0 = min(s for s, _ in outline)
    s1 = max(s for s, _ in outline)
    z0 = min(z for _, z in outline)
    z1 = max(z for _, z in outline)
    w = s1 - s0
    block(patch, origin, d, n, s0 - 0.12, s1 + 0.12, z0 - SILL_H, z0, SILL_OUT)  # sill
    if kind == "plain":
        return
    f = FRAME_W
    arched = len(outline) > 4
    top = z1 - w / 2 if arched else z1
    block(patch, origin, d, n, s0 - f, s0, z0, top, FRAME_OUT)  # jambs
    block(patch, origin, d, n, s1, s1 + f, z0, top, FRAME_OUT)
    if arched:  # the arch as short pieces along the half circle
        r, cs = w / 2 + f / 2, s0 + w / 2
        steps = 8
        for k in range(steps):
            a0, a1 = math.pi * k / steps, math.pi * (k + 1) / steps
            for a in ((a0 + a1) / 2,):
                cx, cz = cs + r * math.cos(a), top + r * math.sin(a)
                half = r * (a1 - a0) / 2 + 0.02
                block(patch, origin, d, n, cx - half, cx + half, cz - f / 2, cz + f / 2, FRAME_OUT)
    else:
        block(patch, origin, d, n, s0 - f, s1 + f, z1, z1 + f, FRAME_OUT)  # lintel
    head = z1 + (0 if arched else f)
    if kind == "keystone":
        k = max(0.22, w * 0.18)
        block(
            patch,
            origin,
            d,
            n,
            s0 + w / 2 - k / 2,
            s0 + w / 2 + k / 2,
            head - 0.1,
            head + 0.32,
            FRAME_OUT + 0.05,
        )
    elif kind == "pediment":
        _pediment(patch, origin, d, n, s0 - f - 0.1, s1 + f + 0.1, head + 0.05)


def _pediment(patch: Patch, origin, d, n, s0, s1, z) -> None:
    """A low triangular pediment (a prism standing out of the wall)."""
    out = FRAME_OUT + 0.08
    h = (s1 - s0) * 0.22
    cs = (s0 + s1) / 2
    block(patch, origin, d, n, s0, s1, z, z + 0.12, out)  # its cornice

    def p(s, zz, o):
        return _at(origin, d, n, s, zz, o)

    zb = z + 0.12
    # front triangle as a degenerate quad, then the two sloping tops
    _quad(patch, [p(s0, zb, out), p(s1, zb, out), p(cs, zb + h, out), p(cs, zb + h, out)])
    _quad(patch, [p(cs, zb + h, out), p(s1, zb, out), p(s1, zb, 0), p(cs, zb + h, 0)])
    _quad(patch, [p(s0, zb, out), p(cs, zb + h, out), p(cs, zb + h, 0), p(s0, zb, 0)])


def dentils(patch: Patch, origin, d, n, length: float, z: float) -> None:
    """The row of small teeth under a main cornice."""
    step, w, h, out = 0.32, 0.14, 0.2, 0.13
    count = int(length / step)
    for k in range(count):
        s = (k + 0.5) * length / max(1, count) - w / 2
        block(patch, origin, d, n, s, s + w, z - h, z, out)
