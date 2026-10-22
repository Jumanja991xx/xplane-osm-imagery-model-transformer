"""Place search for the map ("Allianz Arena" -> Munich, Fröttmaning).

Suggestions while typing come from Photon (komoot's search on OpenStreetMap data,
made for search-as-you-type). Nominatim, OpenStreetMap's own search, is only
asked when the user presses Enter: its usage policy does not allow typing
suggestions. Only the typed text and the map centre (to rank nearby places
first) leave the computer. Data (c) OpenStreetMap contributors, ODbL.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from pathlib import Path
from threading import Lock

from pydantic import BaseModel

from xpsb.osm.overpass import USER_AGENT

PHOTON = "https://photon.komoot.io/api/"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
LIMIT = 7
PHOTON_LANGS = ("de", "en", "fr", "it")
_CACHE: OrderedDict[tuple, list[Place]] = OrderedDict()
_LOCK = Lock()


class Place(BaseModel):
    name: str
    detail: str = ""  # street, city, country - what tells two places apart
    kind: str = ""  # e.g. "stadium", "city"
    lon: float
    lat: float
    bbox: tuple[float, float, float, float] | None = None  # west, south, east, north


class PlaceError(RuntimeError):
    pass


def _fixtures() -> Path | None:
    value = os.environ.get("XPSB_FIXTURES_DIR")
    return Path(value) if value else None


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read(2_000_000))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise PlaceError(f"{urllib.parse.urlparse(url).hostname}: {exc}") from exc


def _join(*parts: str | None) -> str:
    seen: list[str] = []
    for p in parts:
        if p and p not in seen:
            seen.append(p)
    return ", ".join(seen)


def photon(query: str, lang: str, near: tuple[float, float] | None) -> list[Place]:
    params = {"q": query, "limit": str(LIMIT)}
    if lang in PHOTON_LANGS:
        params["lang"] = lang
    if near is not None:
        params["lon"], params["lat"] = f"{near[0]:.4f}", f"{near[1]:.4f}"
    data = _get_json(PHOTON + "?" + urllib.parse.urlencode(params))
    out = []
    for f in data.get("features", []):
        p = f.get("properties") or {}
        lon, lat = (f.get("geometry") or {}).get("coordinates", [None, None])[:2]
        if lon is None or lat is None:
            continue
        ext = p.get("extent")  # west, north, east, south
        bbox = (ext[0], ext[3], ext[2], ext[1]) if ext and len(ext) == 4 else None
        street = _join(p.get("street"), p.get("housenumber")).replace(", ", " ")
        name = p.get("name") or street or p.get("city") or ""
        if not name:
            continue
        out.append(
            Place(
                name=name,
                detail=_join(
                    street if street != name else None,
                    p.get("district"),
                    p.get("city") if p.get("city") != name else None,
                    p.get("state"),
                    p.get("country"),
                ),
                kind=str(p.get("osm_value") or ""),
                lon=float(lon),
                lat=float(lat),
                bbox=bbox,
            )
        )
    return out


def nominatim(query: str, lang: str) -> list[Place]:
    params = {
        "q": query,
        "format": "jsonv2",
        "limit": str(LIMIT),
        "accept-language": lang,
    }
    out = []
    for r in _get_json(NOMINATIM + "?" + urllib.parse.urlencode(params)):
        try:
            s, n, w, e = (float(v) for v in r.get("boundingbox", []))
            bbox = (w, s, e, n)
        except ValueError:
            bbox = None
        full = str(r.get("display_name") or "")
        name = r.get("name") or full.split(",")[0]
        out.append(
            Place(
                name=name,
                detail=", ".join(part.strip() for part in full.split(",")[1:4]),
                kind=str(r.get("type") or ""),
                lon=float(r["lon"]),
                lat=float(r["lat"]),
                bbox=bbox,
            )
        )
    return out


def search(
    query: str, lang: str = "de", near: tuple[float, float] | None = None, final: bool = False
) -> list[Place]:
    """Places for the typed text. `final`: the user pressed Enter (Nominatim may then
    be asked as well if Photon has nothing or is down)."""
    query = " ".join(query.split())[:200]
    if len(query) < 2:
        return []
    fx = _fixtures()
    if fx is not None:  # offline tests
        f = fx / "places.json"
        return [Place(**p) for p in json.loads(f.read_text("utf-8"))] if f.is_file() else []
    # Rounded centre: nearby typing does not miss the cache for every map move.
    key = (query.lower(), lang, near and (round(near[0], 1), round(near[1], 1)), final)
    with _LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    try:
        found = photon(query, lang, near)
    except PlaceError:
        if not final:
            raise
        found = []
    if not found and final:
        found = nominatim(query, lang)
    with _LOCK:
        _CACHE[key] = found
        while len(_CACHE) > 200:
            _CACHE.popitem(last=False)
    return found
