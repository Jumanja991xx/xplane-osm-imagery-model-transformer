"""Other sceneries the user has installed for the same area (Custom Scenery).

Sceneries meet in X-Plane: an ortho (aerial image as ground) under our draped
surfaces is covered by them, and objects of packs further down in
scenery_packs.ini stay unless our exclusion zones cover them. Knowing who is
there lets the app suggest the right settings before anything is built.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from xpsb.xplane.dsf_text import Tile

MESH_DSF_BYTES = 20_000_000  # bigger DSFs carry a terrain mesh (ortho or HD mesh)


class OtherScenery(BaseModel):
    name: str
    kind: str  # "ortho" (aerial image as ground) | "mesh" | "objects"
    enabled: bool = True
    order: int | None = None  # position in scenery_packs.ini (0 = top, drawn first)


def _ini_order(custom: Path) -> dict[str, tuple[int, bool]]:
    ini = custom / "scenery_packs.ini"
    out: dict[str, tuple[int, bool]] = {}
    try:
        lines = ini.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    n = 0
    for line in lines:
        key, _, path = line.strip().partition(" ")
        if key not in ("SCENERY_PACK", "SCENERY_PACK_DISABLED"):
            continue
        name = path.strip().rstrip("/\\").replace("\\", "/").split("/")[-1]
        out[name] = (n, key == "SCENERY_PACK")
        n += 1
    return out


def for_tile(xplane_path: str | None, tile: Tile, own: str | None = None) -> list[OtherScenery]:
    """Packs in Custom Scenery with a DSF for this 1x1 degree tile (not our own)."""
    if not xplane_path:
        return []
    custom = Path(xplane_path) / "Custom Scenery"
    if not custom.is_dir():
        return []
    order = _ini_order(custom)
    found = []
    for pack in sorted(p for p in custom.iterdir() if p.is_dir()):
        if own and pack.name == own:
            continue
        dsf = pack / tile.dsf_relative_path
        try:
            size = dsf.stat().st_size
        except OSError:
            continue
        low = pack.name.lower()
        if "ortho" in low or (pack / "terrain").is_dir():
            kind = "ortho"
        elif size > MESH_DSF_BYTES:
            kind = "mesh"
        else:
            kind = "objects"
        pos, enabled = order.get(pack.name, (None, True))
        found.append(OtherScenery(name=pack.name, kind=kind, enabled=enabled, order=pos))
    return sorted(found, key=lambda s: (s.order is None, s.order or 0, s.name))
