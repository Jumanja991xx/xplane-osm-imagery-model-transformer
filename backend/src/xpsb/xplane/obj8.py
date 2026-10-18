"""Minimal OBJ8 writer (layout follows XObjReadWrite.cpp: XObj8Write).

Axes: +X east, +Y up, +Z south. One texture per object.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Vertex:
    x: float
    y: float
    z: float
    nx: float
    ny: float
    nz: float
    s: float
    t: float


@dataclass
class Obj8:
    texture: str
    vertices: list[Vertex] = field(default_factory=list)
    indices: list[int] = field(default_factory=list)
    # Emitted before the TRIS command, e.g. "ATTR_LOD 0 4000" or "ATTR_no_blend 0.5".
    attributes: list[str] = field(default_factory=list)
    comment: str = "xpsb"

    def add_quad(
        self,
        corners: list[tuple[float, float, float]],
        normal: tuple[float, float, float],
        uv: list[tuple[float, float]],
    ) -> None:
        """Corners counter-clockwise when seen from the side the normal points to."""
        base = len(self.vertices)
        for (x, y, z), (s, t) in zip(corners, uv, strict=True):
            self.vertices.append(Vertex(x, y, z, *normal, s, t))
        # Front faces are counter-clockwise (OpenGL default; Laminar's ObjView enables
        # GL_CULL_FACE without changing glFrontFace). Confirmed visually in Spike A.
        self.indices += [base, base + 1, base + 2, base, base + 2, base + 3]

    def to_text(self) -> str:
        if not self.vertices or not self.indices:
            raise ValueError("empty object")
        if max(self.indices) >= len(self.vertices):
            raise ValueError("index out of range")
        lines = [
            "I",
            f"800 {self.comment}",
            "OBJ",
            "",
            f"TEXTURE {self.texture}",
            f"POINT_COUNTS {len(self.vertices)} 0 0 {len(self.indices)}",
        ]
        for v in self.vertices:
            lines.append(
                f"VT {v.x:.3f} {v.y:.3f} {v.z:.3f} "
                f"{v.nx:.4f} {v.ny:.4f} {v.nz:.4f} {v.s:.4f} {v.t:.4f}"
            )
        full = len(self.indices) - len(self.indices) % 10
        for i in range(0, full, 10):
            lines.append("IDX10 " + " ".join(str(n) for n in self.indices[i : i + 10]))
        lines += [f"IDX {n}" for n in self.indices[full:]]
        lines += self.attributes
        lines.append(f"TRIS 0 {len(self.indices)}")
        return "\n".join(lines) + "\n"
