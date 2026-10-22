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
class Group:
    """A piece of the object's command list: lines before (attributes such as
    ATTR_LOD, animation commands), a TRIS range (count 0: none), lines after
    (ANIM_end). The lights follow the last group of every detail level."""

    before: list[str]
    start: int
    count: int
    after: list[str] = field(default_factory=list)
    end_of_level: bool = True


@dataclass
class Obj8:
    texture: str
    vertices: list[Vertex] = field(default_factory=list)
    indices: list[int] = field(default_factory=list)
    # Emitted before the TRIS command, e.g. "ATTR_LOD 0 4000" or "ATTR_no_blend 0.5".
    attributes: list[str] = field(default_factory=list)
    comment: str = "xpsb"
    # Normal map (RGB normals, alpha = gloss); X-Plane ignores its blue channel
    # unless NORMAL_METALNESS is set, which we do not.
    normal_texture: str | None = None
    # Night texture: added on top when it is dark (lit windows, lamps).
    lit_texture: str | None = None
    # Texture of the draped geometry (ATTR_draped: laid onto the terrain like a
    # polygon, e.g. a soft contact shadow around a house).
    draped_texture: str | None = None
    # Command list (see Group); also (attribute lines, first index, count) tuples.
    # Empty: one group with `attributes` over all indices.
    groups: list = field(default_factory=list)
    # LIGHT_NAMED / LIGHT_PARAM lines, repeated in every detail level (a light
    # belongs to the level it is written in).
    lights: list[str] = field(default_factory=list)

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
        # X-Plane draws faces whose corners run CLOCKWISE as seen by the viewer.
        # Verified in X-Plane 12 (Spike A): counter-clockwise showed the inside of the cube.
        self.indices += [base, base + 2, base + 1, base, base + 3, base + 2]

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
            *([f"TEXTURE_LIT {self.lit_texture}"] if self.lit_texture else []),
            *([f"TEXTURE_NORMAL {self.normal_texture}"] if self.normal_texture else []),
            *([f"TEXTURE_DRAPED {self.draped_texture}"] if self.draped_texture else []),
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
        groups = self.groups or [Group(self.attributes, 0, len(self.indices))]
        for g in groups:
            if isinstance(g, tuple):
                g = Group(*g)
            if g.start < 0 or g.count < 0 or g.start + g.count > len(self.indices) or g.count % 3:
                raise ValueError("bad index group")
            lines += g.before
            if g.count:
                lines.append(f"TRIS {g.start} {g.count}")
            lines += g.after
            if g.end_of_level:
                lines += self.lights
        return "\n".join(lines) + "\n"
