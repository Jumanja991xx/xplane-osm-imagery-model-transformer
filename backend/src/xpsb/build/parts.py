"""Building parts: which geometry of an object belongs together (tower, roof, wing,
the windows of one side ...), so the user can pick a part in the 3D preview and move,
turn, resize or hide it, alone or together with others.

A part is what the script makes between two `part("name")` calls. Without a name,
each tool call is a part of its own; calls of the same tool right after one another
(a loop of lamp posts, the walls of one floor) stay one part. The ids are the names
(or "<tool>-<n>"), so changes keep working when the script is run again.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from xpsb.build.mesh import GeometryError, Mesh
from xpsb.xplane import lights as xp_lights

MAX_NAME = 60
NAME_OK = re.compile(r"^[^\x00-\x1f]{1,60}$")


@dataclass
class PartInfo:
    id: str
    label: str  # the script's name for it ("" = named after the tool)
    tool: str
    call: str  # the first call, shortened (tells the AI which part is meant)


def _short(args: tuple, kwargs: dict) -> str:
    def one(v) -> str:
        if isinstance(v, list) and len(v) > 4:
            return f"[{len(v)} items]"
        text = repr(v)
        return text if len(text) <= 40 else text[:37] + "..."

    out = [one(a) for a in args] + [f"{k}={one(v)}" for k, v in kwargs.items()]
    return ", ".join(out)[:300]


class PartTracker:
    """Stamps everything a tool call adds with the current part."""

    def __init__(self, mesh: Mesh):
        self.mesh = mesh
        self.label = ""
        self._last: tuple[str, str, str] | None = None  # (label, tool, part) of the last call
        self._count: dict[str, int] = {}
        self._known: set[str] = {p.id for p in mesh.parts}

    def name(self, label=None) -> None:
        """part("Turm"): what the script makes from here on belongs to the part
        "Turm" (until the next part() call); part() or part(None) ends it."""
        if label is None or label == "":
            self.label = ""
            return
        if not isinstance(label, str) or not NAME_OK.match(label):
            raise GeometryError(f"part: name must be text of 1 to {MAX_NAME} characters")
        self.label = label.strip()

    def call(self, tool: str, fn, args: tuple, kwargs: dict):
        mesh = self.mesh
        before = (len(mesh.patches), len(mesh.placements), len(mesh.lights))
        result = fn(*args, **kwargs)
        if self.label:
            pid = self.label
        elif self._last is not None and self._last[:2] == ("", tool):
            pid = self._last[2]
        else:
            n = self._count.get(tool, 0)
            self._count[tool] = n + 1
            pid = f"{tool}-{n}"
        self._last = (self.label, tool, pid)
        if pid not in self._known:
            self._known.add(pid)
            mesh.parts.append(PartInfo(pid, self.label, tool, f"{tool}({_short(args, kwargs)})"))
        for p in mesh.patches[before[0] :]:
            p.part = p.part or pid
        for p in mesh.placements[before[1] :]:
            p.part = p.part or pid
        for li in mesh.lights[before[2] :]:
            li.part = li.part or pid
        return result


# ---- changes made by the user ---------------------------------------------------


def _pivot(mesh: Mesh, ids: set[str]) -> tuple[float, float, float] | None:
    """Middle of the parts' footprint, on their lowest point."""
    pts = [p for patch in mesh.patches if patch.part in ids for p in patch.positions]
    pts += [(pl.x, pl.y, 0.0) for pl in mesh.placements if pl.part in ids]
    pts += [(li.x, li.y, li.z) for li in mesh.lights if li.part in ids]
    if not pts:
        return None
    xs, ys, zs = zip(*pts, strict=True)
    return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, min(zs))


def apply_ops(mesh: Mesh, ops) -> None:
    """Applies the user's changes (scene.PartOp, in order) to the parts they name.
    Parts the script no longer makes are skipped."""
    for op in ops:
        ids = set(op.parts)
        if op.hide:
            mesh.patches = [p for p in mesh.patches if p.part not in ids]
            mesh.placements = [p for p in mesh.placements if p.part not in ids]
            mesh.lights = [li for li in mesh.lights if li.part not in ids]
            continue
        pivot = _pivot(mesh, ids)
        if pivot is None:
            continue
        _transform(mesh, ids, pivot, op.scale, op.turn_deg, op.move)


def _transform(mesh: Mesh, ids, pivot, scale, turn_deg, move) -> None:
    px, py, pz = pivot
    sx, sy, sz = scale
    a = math.radians(turn_deg)  # clockwise seen from above, like a heading
    ca, sa = math.cos(a), math.sin(a)
    dx, dy, dz = move

    def point(p):
        x, y, z = (p[0] - px) * sx, (p[1] - py) * sy, (p[2] - pz) * sz
        return (px + x * ca + y * sa + dx, py - x * sa + y * ca + dy, pz + z + dz)

    def normal(n):
        x, y, z = n[0] / sx, n[1] / sy, n[2] / sz
        length = math.sqrt(x * x + y * y + z * z) or 1.0
        x, y, z = x / length, y / length, z / length
        return (x * ca + y * sa, -x * sa + y * ca, z)

    anims = set()
    for patch in mesh.patches:
        if patch.part not in ids:
            continue
        patch.positions = [point(p) for p in patch.positions]
        patch.normals = [normal(n) for n in patch.normals]
        if patch.anim is not None:
            anims.add(patch.anim[0])
    for i in anims:
        mesh.anims[i].pivot = point(mesh.anims[i].pivot)
    for pl in mesh.placements:
        if pl.part in ids:
            pl.x, pl.y, _ = point((pl.x, pl.y, pz))
            pl.heading = (pl.heading + turn_deg) % 360.0
    for li in mesh.lights:
        if li.part not in ids:
            continue
        li.x, li.y, li.z = point((li.x, li.y, li.z))
        if li.name == xp_lights.SPILL and turn_deg:
            # direction in OBJ axes: x east, y up, z south
            params = list(li.params)
            ex, ny = params[5], -params[7]
            params[5], params[7] = ex * ca + ny * sa, -(-ex * sa + ny * ca)
            li.params = tuple(params)
