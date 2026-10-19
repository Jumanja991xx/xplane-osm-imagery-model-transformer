"""Baked mesh → OBJ8 text and → JSON for the in-app 3D preview."""

from __future__ import annotations

from xpsb.build.bake import BakedMesh
from xpsb.build.mesh import Anim, Mesh
from xpsb.xplane.lights import GLOW, SPILL, obj_line
from xpsb.xplane.obj8 import Group, Obj8, Vertex

SKIRT_M = 1.0  # walls reach below ground so objects never float on gentle slopes


def _to_obj_axes(x: float, y: float, z: float) -> tuple[float, float, float]:
    # Local: x east, y north, z up  →  OBJ8: +X east, +Y up, +Z south.
    return x, z, -y


WIND_DATAREF = "sim/graphics/animation/windsock_psi"  # where a windsock points (degrees)
CLOCK_DATAREF = "sim/time/total_running_time_sec"


def _anim_open(anim: Anim) -> list[str]:
    px, py, pz = _to_obj_axes(*anim.pivot)
    lines = ["ANIM_begin", f"ANIM_trans {px:.3f} {py:.3f} {pz:.3f} {px:.3f} {py:.3f} {pz:.3f}"]
    if anim.wind:
        # Headings turn clockwise seen from above: about -Y in OBJ axes.
        lines += [
            f"ANIM_rotate_begin 0 -1 0 {WIND_DATAREF}",
            "ANIM_rotate_key 0 0",
            "ANIM_rotate_key 360 360",
            "ANIM_rotate_end",
        ]
    return lines


def _frame_open(anim: Anim, frame: int) -> list[str]:
    """Shows frame `frame` only in its slice of the loop."""
    dt = anim.period_s / anim.frames
    t0, t1 = frame * dt, (frame + 1) * dt
    lines = ["ANIM_begin"]
    if frame > 0:
        lines += [
            f"ANIM_hide 0 {t0 - 0.001:.4f} {CLOCK_DATAREF}",
            f"ANIM_keyframe_loop {anim.period_s:.4f}",
        ]
    if frame < anim.frames - 1:
        lines += [
            f"ANIM_hide {t1:.4f} {anim.period_s:.4f} {CLOCK_DATAREF}",
            f"ANIM_keyframe_loop {anim.period_s:.4f}",
        ]
    return lines


def to_obj8(
    mesh: Mesh,
    baked: BakedMesh,
    texture: str,
    lod_m: float,
    normal_texture: str | None = None,
    lit_texture: str | None = None,
    near_m: float | None = None,
) -> Obj8:
    """Two detail levels when the mesh has near/far parts: up to `near_m` the
    detailed parts, from there to `lod_m` their cheap stand-ins; shared parts in
    both. Animations (flags) and lights in every level."""
    obj = Obj8(texture=texture, normal_texture=normal_texture, lit_texture=lit_texture)
    # (lod, anim index or -1, frame) -> indices
    parts: dict[tuple[str, int, int], list[int]] = {}
    for patch, uvs in zip(mesh.patches, baked.uvs, strict=True):
        lod = patch.lod if patch.lod in ("near", "far") else "all"
        a, f = patch.anim if patch.anim is not None else (-1, 0)
        indices = parts.setdefault((lod, a, f), [])
        pivot = mesh.anims[a].pivot if a >= 0 else (0.0, 0.0, 0.0)
        for back in (False, True) if patch.two_sided else (False,):
            base = len(obj.vertices)
            for p, n, (s, t) in zip(patch.positions, patch.normals, uvs, strict=True):
                nx, ny, nz = _to_obj_axes(*n)
                if back:
                    nx, ny, nz = -nx, -ny, -nz
                x, y, z = p
                if a >= 0:  # animated parts are written around their pivot
                    x, y, z = x - pivot[0], y - pivot[1], z - pivot[2]
                elif z <= 0.001:
                    z = -SKIRT_M
                obj.vertices.append(Vertex(*_to_obj_axes(x, y, z), nx, ny, nz, s, t))
            for i, j, k in patch.tris:
                # Ours are counter-clockwise from the front; X-Plane draws clockwise
                # ones (verified in Spike A). The back side keeps the original order.
                indices += (
                    [base + i, base + j, base + k] if back else [base + i, base + k, base + j]
                )
    has_levels = any(key[0] in ("near", "far") for key in parts)
    if has_levels and near_m and near_m < lod_m:
        levels = [(f"ATTR_LOD 0 {near_m:.0f}", ("all", "near"))]
        if any(key[0] in ("all", "far") for key in parts):
            levels.append((f"ATTR_LOD {near_m:.0f} {lod_m:.0f}", ("all", "far")))
    else:
        levels = [(f"ATTR_LOD 0 {lod_m:.0f}", ("all", "near"))]

    def take(keys) -> tuple[int, int]:
        start = len(obj.indices)
        for key in keys:
            obj.indices += parts.get(key, [])
        return start, len(obj.indices) - start

    for attr, lods in levels:
        start, count = take([(lod, -1, 0) for lod in lods])
        level = [Group([attr], start, count, end_of_level=False)]
        for a, anim in enumerate(mesh.anims):
            frames = []
            for f in range(anim.frames):
                fs, fc = take([(lod, a, f) for lod in lods])
                if fc:
                    frames.append(Group(_frame_open(anim, f), fs, fc, ["ANIM_end"], False))
            if frames:
                level += [Group(_anim_open(anim), 0, 0, end_of_level=False), *frames]
                level.append(Group([], 0, 0, ["ANIM_end"], end_of_level=False))
        level[-1].end_of_level = True
        obj.groups += level
    obj.lights = [obj_line(light) for light in mesh.lights]
    return obj


def to_preview(mesh: Mesh, baked: BakedMesh) -> dict:
    """Compact three.js-friendly arrays (local frame, z up)."""
    positions: list[float] = []
    normals: list[float] = []
    uvs: list[float] = []
    index: list[int] = []
    for patch, puv in zip(mesh.patches, baked.uvs, strict=True):
        if patch.lod == "far":  # the preview shows the close-up version
            continue
        if patch.anim is not None and patch.anim[1] != 0:  # first frame of animations
            continue
        for back in (False, True) if patch.two_sided else (False,):
            base = len(positions) // 3
            for p, n, (s, t) in zip(patch.positions, patch.normals, puv, strict=True):
                positions += [round(p[0], 3), round(p[1], 3), round(p[2], 3)]
                sign = -1 if back else 1
                normals += [round(sign * n[0], 3), round(sign * n[1], 3), round(sign * n[2], 3)]
                uvs += [round(s, 5), round(t, 5)]
            for a, b, c in patch.tris:
                index += [base + a, base + c, base + b] if back else [base + a, base + b, base + c]
    lights = []
    for li in mesh.lights:
        entry = {"x": round(li.x, 2), "y": round(li.y, 2), "z": round(li.z, 2), "kind": li.name}
        if li.name == SPILL:  # R G B A cd DX DY DZ WIDTH (direction in OBJ axes)
            r, g, b, _a, cd, dx, dy, dz, width = li.params
            entry.update(
                color=[round(r, 3), round(g, 3), round(b, 3)],
                cd=round(cd),
                dir=[round(dx, 3), round(-dz, 3), round(dy, 3)],
                cos=round(width, 3),
            )
        elif li.name != GLOW:
            entry.update(color=[1.0, 0.6, 0.3], cd=2000, dir=[0, 0, -1], cos=0.3)
        else:
            continue  # the glow belongs to a spill light at the same spot
        lights.append(entry)
    return {
        "positions": positions,
        "normals": normals,
        "uvs": uvs,
        "index": index,
        "lights": lights,
        "lit": baked.lit is not None,
        "alpha": baked.image.mode == "RGBA",
    }
