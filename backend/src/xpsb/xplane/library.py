"""Catalog of the X-Plane library: objects other sceneries may place by their
virtual path (e.g. lib/street/lamp.obj) without shipping the files.

Reads the library.txt files of X-Plane's default scenery and of installed
library packs in Custom Scenery. Only public, non-deprecated .obj exports are
offered; sizes come from the vertex table of the first real file.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

MAX_OBJ_BYTES = 4_000_000  # bigger files are skipped when measuring
# Groups offered to the AI architect: (name, words in the virtual path).
GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("lights", ("light", "lamp", "flood", "lantern", "mast")),
    ("fences", ("fence", "barrier", "rail", "wall", "bollard", "gate")),
    ("vegetation", ("tree", "bush", "hedge", "plant", "shrub")),
    ("vehicles", ("car", "truck", "bus", "van", "vehicle", "tractor", "trailer")),
    ("street", ("bench", "bin", "sign", "kiosk", "bus_stop", "phone", "hydrant", "bike")),
    ("flags", ("flag", "banner", "billboard", "board", "screen")),
    ("people", ("people", "person", "pedestrian", "crowd")),
    ("site", ("container", "tent", "crane", "cone", "pallet", "toilet", "stand", "shed")),
)
PER_GROUP = 25
CACHE_VERSION = 1


@dataclass
class LibraryObject:
    path: str  # virtual path
    pack: str  # scenery pack folder that exports it
    default: bool  # part of X-Plane itself (always present)
    file: str  # first real file (absolute)
    size: tuple[float, float, float] | None = None  # x, z (depth), y (height) in metres


@dataclass
class Catalog:
    objects: dict[str, LibraryObject] = field(default_factory=dict)

    def __contains__(self, path: str) -> bool:
        return path in self.objects

    def __len__(self) -> int:
        return len(self.objects)

    def offer(self, request: str = "") -> list[LibraryObject]:
        """A manageable selection for the AI: per group the shortest paths
        (usually the generic ones); groups the request mentions get more."""
        words = request.lower()
        chosen: dict[str, LibraryObject] = {}
        for _, keys in GROUPS:
            hits = sorted(
                (o for o in self.objects.values() if _matches(o.path, keys)),
                key=lambda o: (not o.default, len(o.path), o.path),
            )
            boost = 2 if any(k in words for k in keys) else 1
            for o in hits[: PER_GROUP * boost]:
                chosen[o.path] = o
        return list(chosen.values())


def _matches(path: str, keys: tuple[str, ...]) -> bool:
    """Whole words or word starts/ends ("trees", "streetlamp"); "street" is no "tree"."""
    tokens = [t for t in re.split(r"[^a-z]+", path.lower()) if t]
    return any(t.startswith(k) or t.endswith(k) for t in tokens for k in keys)


_EXPORT = re.compile(r"^(EXPORT|EXPORT_EXTEND|EXPORT_BACKUP)\s+(\S+)\s+(.+?)\s*$")
_EXPORT_RATIO = re.compile(r"^EXPORT_RATIO\s+\S+\s+(\S+)\s+(.+?)\s*$")


def parse_library(text: str, pack_dir: Path, pack: str, default: bool) -> list[LibraryObject]:
    out: list[LibraryObject] = []
    public, deprecated = True, False
    for raw in text.splitlines():
        line = raw.strip()
        if line in ("PUBLIC", "PRIVATE", "DEPRECATED") or line.startswith(("PUBLIC ", "PRIVATE ")):
            public = line.startswith("PUBLIC")
            deprecated = line == "DEPRECATED"
            continue
        m = _EXPORT.match(line) or _EXPORT_RATIO.match(line)
        if not m:
            continue
        vpath, real = (m.group(2), m.group(3)) if m.re is _EXPORT else (m.group(1), m.group(2))
        if not public or deprecated or not vpath.lower().endswith(".obj") or "\\" in vpath:
            continue
        out.append(
            LibraryObject(
                path=vpath,
                pack=pack,
                default=default,
                file=str(pack_dir / real.replace("\\", "/")),
            )
        )
    return out


def _library_files(xplane: Path) -> list[tuple[Path, bool]]:
    default = sorted((xplane / "Resources" / "default scenery").glob("*/library.txt"))
    custom = sorted((xplane / "Custom Scenery").glob("*/library.txt"))
    return [(p, True) for p in default] + [(p, False) for p in custom]


def measure(file: str) -> tuple[float, float, float] | None:
    """Bounding box of an OBJ8 file from its VT lines (x, z, y extents)."""
    path = Path(file)
    try:
        if path.stat().st_size > MAX_OBJ_BYTES:
            return None
        text = path.read_text(encoding="latin-1", errors="replace")
    except OSError:
        return None
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for line in text.splitlines():
        if line.startswith("VT"):
            parts = line.split()
            if len(parts) < 4:
                continue
            try:
                xyz = [float(parts[1]), float(parts[2]), float(parts[3])]
            except ValueError:
                continue
            for i in range(3):
                lo[i] = min(lo[i], xyz[i])
                hi[i] = max(hi[i], xyz[i])
    if lo[0] == float("inf"):
        return None
    return (round(hi[0] - lo[0], 2), round(hi[2] - lo[2], 2), round(hi[1] - lo[1], 2))


def load(xplane_path: str | None, cache_file: Path) -> Catalog:
    """Catalog of the given X-Plane folder, cached until a library.txt changes."""
    if not xplane_path:
        return Catalog()
    xplane = Path(xplane_path)
    files = _library_files(xplane)
    stamp = {str(p): p.stat().st_mtime for p, _ in files}
    try:
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        if cached.get("version") == CACHE_VERSION and cached.get("stamp") == stamp:
            objects = [
                LibraryObject(**{**o, "size": tuple(o["size"]) if o["size"] else None})
                for o in cached["objects"]
            ]
            return Catalog({o.path: o for o in objects})
    except (OSError, ValueError, KeyError, TypeError):
        pass
    catalog = Catalog()
    for lib, default in files:
        try:
            text = lib.read_text(encoding="latin-1", errors="replace")
        except OSError:
            continue
        for obj in parse_library(text, lib.parent, lib.parent.name, default):
            catalog.objects.setdefault(obj.path, obj)  # first export wins, like X-Plane
    # Sizes only for what may be offered (measuring thousands of files takes time).
    for obj in catalog.offer("".join(k for _, keys in GROUPS for k in keys)):
        obj.size = measure(obj.file)
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(
                {
                    "version": CACHE_VERSION,
                    "stamp": stamp,
                    "objects": [asdict(o) for o in catalog.objects.values()],
                }
            ),
            encoding="utf-8",
        )
    except OSError:
        pass
    return catalog
