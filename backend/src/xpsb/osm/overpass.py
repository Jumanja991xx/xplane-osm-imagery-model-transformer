"""Overpass API client.

Fair use (community servers): identify ourselves with a User-Agent, one request
per job, cache answers on disk, back off on 429/504 and try a second server.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from shapely.geometry import Polygon

from xpsb import __version__

log = logging.getLogger(__name__)

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
USER_AGENT = (
    f"XPlaneAISceneryBuilder/{__version__} "
    "(+https://github.com/Jumanja991xx/xplane-osm-imagery-model-transformer)"
)
CACHE_MAX_AGE_S = 7 * 24 * 3600

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
        'way(bn)["highway"][!"bridge"];\nout geom tags;\n'
    )
    return (
        f"[out:json][timeout:{timeout_s}];\n(\n{parts}\n);\nout geom tags;\n"
        + approaches
        + f"is_in({inside.y:.7f},{inside.x:.7f})->.encl;\n(\n{enclosing}\n);\nout geom tags;\n"
    )


def _fixture_dir() -> Path | None:
    # Offline development and browser tests: answers come from files.
    value = os.environ.get("XPSB_FIXTURES_DIR")
    return Path(value) if value else None


def fetch(query: str, cache_dir: Path, retries: int = 2) -> dict:
    key = hashlib.sha256(query.encode()).hexdigest()[:32]
    fixtures = _fixture_dir()
    if fixtures is not None:
        return json.loads((fixtures / "overpass.json").read_text(encoding="utf-8"))

    cache_file = cache_dir / f"{key}.json"
    if cache_file.exists() and time.time() - cache_file.stat().st_mtime < CACHE_MAX_AGE_S:
        log.info("overpass cache hit %s", key)
        return json.loads(cache_file.read_text(encoding="utf-8"))

    body = urllib.parse.urlencode({"data": query}).encode()
    last_error = "no endpoint tried"
    for attempt in range(retries + 1):
        for endpoint in ENDPOINTS:
            req = urllib.request.Request(
                endpoint, data=body, headers={"User-Agent": USER_AGENT}, method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                last_error = f"{endpoint}: HTTP {exc.code}"
                log.warning("overpass %s", last_error)
                continue
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                last_error = f"{endpoint}: {exc}"
                log.warning("overpass %s", last_error)
                continue
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(data), encoding="utf-8")
            return data
        time.sleep(5 * (attempt + 1))
    raise OverpassError(last_error)
