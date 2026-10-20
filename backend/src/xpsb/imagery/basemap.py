"""The user's own XYZ source as the background of the map.

The map fetches its tiles through the backend instead of from the tile server
directly: the desktop app's content security policy then stays closed (only the
built-in map server and the local backend), and any server the user enters
works - with its subdomains, {-y} rows, and a disk cache that spares the server.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from xpsb.imagery import xyz
from xpsb.imagery.sources import ImagerySource

CACHE_MAX_AGE_S = 30 * 24 * 3600
_SUFFIX = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def source_key(source: ImagerySource) -> str:
    """Changes when the URL or subdomains change (new tiles, not the cached ones)."""
    text = source.url + "|" + ",".join(source.subdomains)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def tile(source: ImagerySource, cache_dir: Path, z: int, x: int, y: int) -> tuple[bytes, str]:
    folder = cache_dir / source_key(source) / str(z) / str(x)
    for ctype, suffix in _SUFFIX.items():
        path = folder / f"{y}.{suffix}"
        if path.exists() and time.time() - path.stat().st_mtime < CACHE_MAX_AGE_S:
            return path.read_bytes(), ctype
    data, ctype = xyz.tile_bytes(source, z, x, y)
    suffix = _SUFFIX.get(ctype.split(";")[0].strip())
    if suffix:
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f"{y}.{suffix}.part"
        tmp.write_bytes(data)
        tmp.replace(folder / f"{y}.{suffix}")
    return data, ctype
