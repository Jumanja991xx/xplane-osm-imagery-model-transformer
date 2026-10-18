"""Baked mesh → OBJ8 text and → JSON for the in-app 3D preview."""

from __future__ import annotations

from xpsb.build.bake import BakedMesh
from xpsb.build.mesh import Mesh
from xpsb.xplane.obj8 import Obj8, Vertex

SKIRT_M = 1.0  # walls reach below ground so objects never float on gentle slopes


def _to_obj_axes(x: float, y: float, z: float) -> tuple[float, float, float]:
    # Local: x east, y north, z up  →  OBJ8: +X east, +Y up, +Z south.
    return x, z, -y


def to_obj8(mesh: Mesh, baked: BakedMesh, texture: str, lod_m: float) -> Obj8:
    obj = Obj8(texture=texture)
    indices: list[int] = []
    for patch, uvs in zip(mesh.patches, baked.uvs, strict=True):
        for back in (False, True) if patch.two_sided else (False,):
            base = len(obj.vertices)
            for p, n, (s, t) in zip(patch.positions, patch.normals, uvs, strict=True):
                nx, ny, nz = _to_obj_axes(*n)
                if back:
                    nx, ny, nz = -nx, -ny, -nz
                x, y, z = p
                if z <= 0.001:
                    z = -SKIRT_M
                obj.vertices.append(Vertex(*_to_obj_axes(x, y, z), nx, ny, nz, s, t))
            for a, b, c in patch.tris:
                # Ours are counter-clockwise from the front; X-Plane draws clockwise
                # ones (verified in Spike A). The back side keeps the original order.
                indices += (
                    [base + a, base + b, base + c] if back else [base + a, base + c, base + b]
                )
    obj.indices = indices
    obj.attributes.append(f"ATTR_LOD 0 {lod_m:.0f}")
    return obj


def to_preview(mesh: Mesh, baked: BakedMesh) -> dict:
    """Compact three.js-friendly arrays (local frame, z up)."""
    positions: list[float] = []
    normals: list[float] = []
    uvs: list[float] = []
    index: list[int] = []
    for patch, puv in zip(mesh.patches, baked.uvs, strict=True):
        for back in (False, True) if patch.two_sided else (False,):
            base = len(positions) // 3
            for p, n, (s, t) in zip(patch.positions, patch.normals, puv, strict=True):
                positions += [round(p[0], 3), round(p[1], 3), round(p[2], 3)]
                sign = -1 if back else 1
                normals += [round(sign * n[0], 3), round(sign * n[1], 3), round(sign * n[2], 3)]
                uvs += [round(s, 5), round(t, 5)]
            for a, b, c in patch.tris:
                index += [base + a, base + c, base + b] if back else [base + a, base + b, base + c]
    return {"positions": positions, "normals": normals, "uvs": uvs, "index": index}
