"""Free 3D scans of statues and monuments (Wikidata "3D model" -> Wikimedia Commons).

Many famous statues have a scan on Wikimedia Commons, linked from their Wikidata
item (property P4896). If its licence allows sharing (CC0, public domain, CC BY,
CC BY-SA - the same rule as for photo textures), the STL file is downloaded,
reduced to a few thousand triangles (and a cheap version for the distance),
stood upright with its foot at 0 and stored with the job. The build script places
it with scan(); the author and licence go into the package README.
"""

from __future__ import annotations

import json
import struct
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import numpy as np

from xpsb.photos import (
    USER_AGENT,
    WIKIMEDIA_API,
    Photo,
    PhotoError,
    _fixtures,
    _plain,
    http_get,
    redistributable,
)
from xpsb.scene import Scan

MAX_SCAN_BYTES = 120 * 1024 * 1024  # bigger scans are left alone (download time)
NEAR_TRIANGLES = 6000
FAR_TRIANGLES = 600


class ScanError(ValueError):
    pass


# ---- finding and checking --------------------------------------------------------


def commons_files(entity: dict) -> list[str]:
    """File names of the 3D models linked from a Wikidata item (STL only)."""
    out = []
    for c in (entity.get("claims") or {}).get("P4896", []):
        value = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(value, str) and value.lower().endswith(".stl"):
            out.append(value)
    return out


def file_info(name: str) -> dict:
    """URL, size, author and licence of a Commons file."""
    params = {
        "action": "query",
        "format": "json",
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
        "titles": f"File:{name}",
    }
    data = json.loads(http_get(WIKIMEDIA_API + "?" + urllib.parse.urlencode(params)))
    for page in (data.get("query") or {}).get("pages", {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        if info.get("url"):
            return {
                "url": info["url"],
                "size": int(info.get("size") or 0),
                "page_url": info.get("descriptionurl"),
                "author": _plain((meta.get("Artist") or {}).get("value")),
                "license": _plain((meta.get("LicenseShortName") or {}).get("value")),
            }
    raise ScanError(f"{name}: not found on Commons")


def shareable(license: str | None) -> bool:
    return redistributable(Photo(id="scan", source="wikimedia", license=license))


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read(MAX_SCAN_BYTES + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ScanError(f"download failed: {exc}") from exc
    if len(data) > MAX_SCAN_BYTES:
        raise ScanError("scan too large")
    return data


# ---- geometry ------------------------------------------------------------------------


def parse_stl(data: bytes) -> tuple[np.ndarray, np.ndarray]:
    """Vertices (N, 3) and triangles (M, 3) of a binary or ASCII STL file."""
    if len(data) >= 84:
        (count,) = struct.unpack_from("<I", data, 80)
        if 84 + count * 50 == len(data):  # binary
            raw = np.frombuffer(
                data,
                dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]),
                count=count,
                offset=84,
            )
            corners = raw["v"].reshape(-1, 3).astype(np.float64)
            return _weld(corners)
    text = data.decode("ascii", errors="ignore")
    values = [line.split()[1:4] for line in text.splitlines() if line.strip().startswith("vertex")]
    if len(values) < 3:
        raise ScanError("not an STL file")
    corners = np.array(values, dtype=np.float64)[: len(values) // 3 * 3]
    return _weld(corners)


def _weld(corners: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if not np.isfinite(corners).all():
        raise ScanError("scan contains invalid coordinates")
    span = float(np.ptp(corners, axis=0).max()) or 1.0
    keys = np.round(corners / span * 1e6).astype(np.int64)
    _, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    verts = corners[first]
    tris = inverse.reshape(-1, 3)
    good = (tris[:, 0] != tris[:, 1]) & (tris[:, 1] != tris[:, 2]) & (tris[:, 0] != tris[:, 2])
    return verts, tris[good]


def decimate(verts: np.ndarray, tris: np.ndarray, target: int) -> tuple[np.ndarray, np.ndarray]:
    """Vertex clustering: merges the vertices in each cell of a grid, the grid made
    coarser until at most `target` triangles are left. Keeps the silhouette."""
    if len(tris) <= target:
        return verts, tris
    lo = verts.min(axis=0)
    span = float(np.ptp(verts, axis=0).max()) or 1.0
    best = None
    lo_cells, hi_cells = 4, 512
    for _ in range(14):
        cells = (lo_cells + hi_cells) // 2
        size = span / cells
        keys = np.floor((verts - lo) / size).astype(np.int64)
        _, cluster = np.unique(keys, axis=0, return_inverse=True)
        cluster = cluster.reshape(-1)
        t = cluster[tris]
        keep = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
        t = np.unique(np.sort(t[keep], axis=1), axis=0, return_index=True)[1]
        count = len(t)
        if count <= target:
            best = cells
            lo_cells = cells + 1
        else:
            hi_cells = cells - 1
        if lo_cells > hi_cells:
            break
    cells = best or 4
    size = span / cells
    keys = np.floor((verts - lo) / size).astype(np.int64)
    _, cluster = np.unique(keys, axis=0, return_inverse=True)
    cluster = cluster.reshape(-1)
    n = int(cluster.max()) + 1
    sums = np.zeros((n, 3))
    np.add.at(sums, cluster, verts)
    counts = np.bincount(cluster, minlength=n)[:, None]
    new_verts = sums / np.maximum(counts, 1)
    t = cluster[tris]
    keep = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
    t = t[keep]
    _, idx = np.unique(np.sort(t, axis=1), axis=0, return_index=True)
    return new_verts, t[np.sort(idx)]


def upright(verts: np.ndarray, up: str = "auto") -> tuple[np.ndarray, str]:
    """Turns the scan so its up axis is z (scanners write z or y up; a statue is
    taller than wide), foot at z=0, centred on x/y, height 1."""
    ext = np.ptp(verts, axis=0)
    if up == "auto":
        up = "z" if ext[2] >= 0.6 * ext.max() else ("y" if ext[1] >= ext[0] else "x")
    if up == "y":  # y up (glTF/OBJ habit): (x, y, z) -> (x, -z, y)
        verts = np.column_stack([verts[:, 0], -verts[:, 2], verts[:, 1]])
    elif up == "x":
        verts = np.column_stack([verts[:, 1], verts[:, 2], verts[:, 0]])
    lo, hi = verts.min(axis=0), verts.max(axis=0)
    centre = (lo + hi) / 2
    height = float(hi[2] - lo[2]) or 1.0
    out = (verts - np.array([centre[0], centre[1], lo[2]])) / height
    return out, up


def prepare(data: bytes, path: Path) -> tuple[int, str]:
    """Parses, reduces and stands up a scan; stores near and far versions (.npz).
    Returns (triangles of the near version, up axis)."""
    verts, tris = parse_stl(data)
    if len(tris) < 4:
        raise ScanError("scan has no surface")
    verts, up = upright(verts)
    near_v, near_t = decimate(verts, tris, NEAR_TRIANGLES)
    far_v, far_t = decimate(verts, tris, FAR_TRIANGLES)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path,
        near_v=near_v.astype(np.float32),
        near_t=near_t.astype(np.int32),
        far_v=far_v.astype(np.float32),
        far_t=far_t.astype(np.int32),
    )
    return len(near_t), up


def fetch(entity: dict, folder: Path) -> Scan | None:
    """The first shareable STL scan of a Wikidata item, ready to build (or None)."""
    if _fixtures() is not None:  # offline tests: no downloads
        return None
    for name in commons_files(entity)[:3]:
        try:
            info = file_info(name)
            if not shareable(info["license"]) or info["size"] > MAX_SCAN_BYTES:
                continue
            ident = uuid.uuid4().hex[:12]
            triangles, up = prepare(_download(info["url"]), folder / f"{ident}.npz")
        except (ScanError, PhotoError, ValueError, KeyError):
            continue  # the next file, or no scan
        return Scan(
            file=f"scans/{ident}.npz",
            title=name,
            author=info["author"],
            license=info["license"],
            page_url=info["page_url"],
            triangles=triangles,
            up=up,
        )
    return None


def load(path: Path) -> dict[str, np.ndarray]:
    with np.load(path) as d:
        return {k: d[k] for k in ("near_v", "near_t", "far_v", "far_t")}
