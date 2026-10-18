"""Draped polygon (.pol) writer, matching WED_ResourceMgr::WritePol."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LoadCenter:
    lat: float
    lon: float
    size_m: float
    texture_px: int


@dataclass(frozen=True)
class DrapedPolygonDef:
    texture: str
    # Tiling materials wrap (TEXTURE); orthophotos must not (TEXTURE_NOWRAP).
    wrap: bool
    scale_s_m: float
    scale_t_m: float
    layer_group: str = "beaches"
    layer_offset: int = 1
    load_center: LoadCenter | None = None
    no_alpha: bool = False

    def to_text(self) -> str:
        lines = ["A", "850 xpsb", "DRAPED_POLYGON", ""]
        if self.load_center:
            lc = self.load_center
            lines.append(f"LOAD_CENTER {lc.lat:.5f} {lc.lon:.5f} {lc.size_m:.1f} {lc.texture_px}")
        lines.append(f"{'TEXTURE' if self.wrap else 'TEXTURE_NOWRAP'} {self.texture}")
        lines.append(f"SCALE {self.scale_s_m:.1f} {self.scale_t_m:.1f}")
        if self.no_alpha:
            lines.append("NO_ALPHA")
        lines.append(f"LAYER_GROUP {self.layer_group} {self.layer_offset}")
        return "\n".join(lines) + "\n"
