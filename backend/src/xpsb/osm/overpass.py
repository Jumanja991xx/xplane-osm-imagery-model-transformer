"""Overpass API client.

Fair use (community servers): identify ourselves with a User-Agent, cache answers
on disk, back off on 429/504. A busy server can keep a request open for minutes
without answering; waiting for each server in turn could take half an hour before
the fallbacks got their chance. So the next server is asked only when the one
before has not answered within HEDGE_S (most answers come in seconds, so mostly
one request is sent), the first answer wins, and after DEADLINE_S the fallbacks
take over.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

from shapely.geometry import Polygon

from xpsb import __version__

log = logging.getLogger(__name__)

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
USER_AGENT = (
    f"XPlaneAISceneryBuilder/{__version__} "
    "(+https://github.com/Jumanja991xx/xplane-osm-imagery-model-transformer)"
)
CACHE_MAX_AGE_S = 7 * 24 * 3600
REQUEST_TIMEOUT_S = 100  # per request (the query asks the server for at most 90 s)
HEDGE_S = 25.0  # no answer yet: ask the next server as well
DEADLINE_S = 150.0  # all Overpass servers together; then older data / the OSM API

# Everything the catalog can use; the classifier decides what each element becomes.
SELECTORS = [
    'nwr["building"]',
    'nwr["building:part"]',
    'way["barrier"~"^(fence|wall|guard_rail|retaining_wall|handrail|jersey_barrier)$"]',
    'nwr["leisure"~"^(pitch|track|stadium|sports_centre)$"]',
    'nwr["landuse"~"^(grass|recreation_ground)$"]',
    'way["highway"="raceway"]',
    'nwr["area:highway"]',
    'nwr["amenity"="parking"]',
    'nwr["man_made"~"^(mast|tower|flagpole)$"]',
    'node["tower:type"="lighting"]',
    # Statues, busts, obelisks, columns (points mostly).
    'nwr["historic"~"^(memorial|monument)$"]',
    'nwr["tourism"="artwork"]["artwork_type"~"^(statue|bust|sculpture)$"]',
    'nwr["man_made"="obelisk"]',
    'nwr["surface"]["area"="yes"]',
    'wr["surface"][!"highway"]',
    # Squares, parks, green and open ground.
    'wr["place"="square"]',
    'wr["highway"~"^(pedestrian|footway)$"]["area"="yes"]',
    'wr["amenity"~"^(marketplace|parking_space)$"]',
    'wr["landuse"~"^(village_green|meadow|flowerbed|cemetery|construction|brownfield)$"]',
    'wr["leisure"~"^(park|garden|playground|dog_park|golf_course|common)$"]',
    'wr["natural"~"^(sand|beach)$"]',
    # Bridges: their outlines and the ways on them (roads, railways, paths).
    'wr["man_made"="bridge"]',
    'way["bridge"]["bridge"!="no"]["highway"]',
    'way["bridge"]["bridge"!="no"]["railway"]',
    # Water in courtyards (thermal baths, palaces): the building scripts show it.
    'wr["leisure"="swimming_pool"]',
    'wr["amenity"="fountain"]',
    'wr["natural"="water"]["water"!~"^(river|canal|lake|reservoir|stream|lock)$"]',
]

# Named grounds around buildings: what a building belongs to (name, Wikidata).
SITE_KEYS = (
    "leisure",
    "historic",
    "amenity",
    "tourism",
    "man_made",
    "office",
    "healthcare",
    "military",
    "landuse",
)
# Too big or too general to say anything about the buildings on them.
SKIP = "nature_reserve|forest|farmland|meadow|district|residential|commercial|retail|industrial"
SITE_FILTERS = [f'["{key}"]["name"]["{key}"!~"^({SKIP})$"]' for key in SITE_KEYS]
SELECTORS += [f"wr{f}" for f in SITE_FILTERS]


class OverpassError(RuntimeError):
    pass


def build_query(ring: list[tuple[float, float]], timeout_s: int = 90) -> str:
    poly = " ".join(f"{lat:.7f} {lon:.7f}" for lon, lat in ring[:-1])
    parts = "\n".join(f'  {sel}(poly:"{poly}");' for sel in SELECTORS)
    # Grounds that enclose the whole drawn area (a stadium drawn tightly inside its
    # grounds) have no node inside it: ask which named areas contain its centre.
    inside = Polygon(ring).representative_point()
    enclosing = "\n".join(
        f"  way(pivot.encl){f};\n  relation(pivot.encl){f};" for f in SITE_FILTERS
    )
    # The roads leading onto the bridges (they share a node with a bridge way):
    # the approach ramps follow them.
    approaches = (
        f'way["bridge"]["bridge"!="no"]["highway"](poly:"{poly}")->.br;\n'
        "node(w.br)->.bn;\n"
        'way(bn)["highway"][!"bridge"];\nout geom;\n'
    )
    return (
        f"[out:json][timeout:{timeout_s}];\n(\n{parts}\n);\nout geom;\n"
        + approaches
        + f"is_in({inside.y:.7f},{inside.x:.7f})->.encl;\n(\n{enclosing}\n);\nout geom;\n"
    )


def _fixture_dir() -> Path | None:
    # Offline development and browser tests: answers come from files.
    value = os.environ.get("XPSB_FIXTURES_DIR")
    return Path(value) if value else None


OSM_API_MAP = "https://api.openstreetmap.org/api/0.6/map.json"
OSM_API_MAX_DEG2 = 0.25  # the API's own limit for one area


def _post(endpoint: str, body: bytes) -> dict:
    req = urllib.request.Request(
        endpoint, data=body, headers={"User-Agent": USER_AGENT}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
        return json.loads(resp.read())


def _race(
    endpoints: list[str], body: bytes, retries: int, on_try: Callable[[str], None] | None
) -> tuple[dict | None, str]:
    """Asks the servers one after another, the next one already when the one before
    takes longer than HEDGE_S or fails; the first answer wins. A server that failed
    fast (busy: 429/504, unreachable) is asked again up to `retries` times."""
    answers: queue.Queue = queue.Queue()
    deadline = time.monotonic() + DEADLINE_S
    order = [ep for _ in range(retries + 1) for ep in endpoints]
    last_error = "no endpoint tried"
    running = 0

    def ask(endpoint: str) -> None:
        try:
            answers.put((endpoint, _post(endpoint, body), None))
        except urllib.error.HTTPError as exc:
            answers.put((endpoint, None, f"HTTP {exc.code}"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            answers.put((endpoint, None, str(exc)))

    next_try = 0
    while time.monotonic() < deadline and (running or next_try < len(order)):
        if next_try < len(order):
            endpoint = order[next_try]
            next_try += 1
            if next_try > len(endpoints):
                time.sleep(2.0)  # a second round: give busy servers a moment
            if on_try:
                on_try(endpoint)
            threading.Thread(target=ask, args=(endpoint,), daemon=True).start()
            running += 1
            wait = HEDGE_S
        else:
            wait = deadline - time.monotonic()
        try:
            endpoint, data, error = answers.get(
                timeout=max(0.0, min(wait, deadline - time.monotonic()))
            )
        except queue.Empty:
            continue  # no answer in time: ask the next server as well
        running -= 1
        if data is not None:
            return data, ""
        last_error = f"{endpoint}: {error}"
        log.warning("overpass %s", last_error)
        # Failed: the next server right away (the loop starts it now).
    if running:
        last_error += f"; no answer within {DEADLINE_S:.0f} s"
    return None, last_error


def fetch(
    query: str,
    cache_dir: Path,
    retries: int = 2,
    ring: list[tuple[float, float]] | None = None,
    own_endpoint: str | None = None,
    on_try: Callable[[str], None] | None = None,
) -> dict:
    """OSM data for the query. When every Overpass server fails: an older answer for
    the same (or a larger, enclosing) area from the cache, then OpenStreetMap's own
    API for the area (`ring`). The answer says where it came from ("source")."""
    key = hashlib.sha256(query.encode()).hexdigest()[:32]
    fixtures = _fixture_dir()
    if fixtures is not None:
        return json.loads((fixtures / "overpass.json").read_text(encoding="utf-8"))

    cache_file = cache_dir / f"{key}.json"
    if cache_file.exists() and time.time() - cache_file.stat().st_mtime < CACHE_MAX_AGE_S:
        log.info("overpass cache hit %s", key)
        return json.loads(cache_file.read_text(encoding="utf-8"))

    body = urllib.parse.urlencode({"data": query}).encode()
    endpoints = ([own_endpoint] if own_endpoint else []) + ENDPOINTS
    data, last_error = _race(endpoints, body, retries, on_try)
    if data is not None:
        _store(cache_dir, cache_file, data, ring)
        return data
    old = _stale(cache_dir, cache_file, ring)
    if old is not None:
        return old
    if ring is not None:
        try:
            data = osm_api_map(ring)
        except (urllib.error.URLError, TimeoutError, ValueError, OverpassError) as exc:
            last_error += f"; OSM API: {exc}"
        else:
            _store(cache_dir, cache_file, data, ring)
            return data
    raise OverpassError(last_error)


def _bbox(ring) -> tuple[float, float, float, float]:
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def _store(cache_dir: Path, cache_file: Path, data: dict, ring) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(data), encoding="utf-8")
    if ring is not None:  # which area it covers (to stand in for smaller areas later)
        cache_file.with_suffix(".area").write_text(json.dumps(_bbox(ring)), encoding="utf-8")


def _stale(cache_dir: Path, cache_file: Path, ring) -> dict | None:
    """An older answer: for this query, or for an area that encloses this one."""
    candidates = [cache_file] if cache_file.exists() else []
    if ring is not None and cache_dir.is_dir():
        w, s, e, n = _bbox(ring)
        for meta in sorted(cache_dir.glob("*.area"), key=lambda p: -p.stat().st_mtime):
            try:
                cw, cs, ce, cn = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if cw <= w and cs <= s and ce >= e and cn >= n and meta.with_suffix(".json").exists():
                candidates.append(meta.with_suffix(".json"))
    for f in candidates:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        age_days = (time.time() - f.stat().st_mtime) / 86400
        log.warning("overpass down: using cached data from %.0f days ago", age_days)
        return {**data, "source": "cache", "age_days": round(age_days, 1)}
    return None


def osm_api_map(ring) -> dict:
    """The area from OpenStreetMap's main API (a different service than Overpass),
    turned into Overpass-style elements with geometry."""
    w, s, e, n = _bbox(ring)
    if (e - w) * (n - s) > OSM_API_MAX_DEG2:
        raise OverpassError("area too large for the OSM API")
    url = f"{OSM_API_MAP}?bbox={w:.6f},{s:.6f},{e:.6f},{n:.6f}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = json.loads(resp.read())
    return {"elements": from_osm_api(raw.get("elements", [])), "source": "osm_api"}


def from_osm_api(elements: list[dict]) -> list[dict]:
    """Elements of the OSM API (ways list node ids) as Overpass returns them with
    `out geom` (ways and relation members carry their coordinates)."""
    nodes = {el["id"]: el for el in elements if el["type"] == "node"}
    ways: dict[int, list[dict]] = {}
    out: list[dict] = []
    for el in elements:
        if el["type"] == "way":
            geom = [
                {"lat": nodes[i]["lat"], "lon": nodes[i]["lon"]}
                for i in el.get("nodes", [])
                if i in nodes
            ]
            ways[el["id"]] = geom
            if el.get("tags"):
                out.append({"type": "way", "id": el["id"], "tags": el["tags"], "geometry": geom})
        elif el["type"] == "node" and el.get("tags"):
            out.append(el)
    for el in elements:
        if el["type"] == "relation" and el.get("tags"):
            members = []
            for m in el.get("members", []):
                member = {"type": m["type"], "ref": m["ref"], "role": m.get("role", "")}
                if m["type"] == "way" and m["ref"] in ways:
                    member["geometry"] = ways[m["ref"]]
                members.append(member)
            out.append({"type": "relation", "id": el["id"], "tags": el["tags"], "members": members})
    return out
