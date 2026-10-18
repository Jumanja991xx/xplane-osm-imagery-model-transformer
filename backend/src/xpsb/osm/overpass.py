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
    'nwr["surface"]["area"="yes"]',
]


class OverpassError(RuntimeError):
    pass


def build_query(ring: list[tuple[float, float]], timeout_s: int = 90) -> str:
    poly = " ".join(f"{lat:.7f} {lon:.7f}" for lon, lat in ring[:-1])
    parts = "\n".join(f'  {sel}(poly:"{poly}");' for sel in SELECTORS)
    return f"[out:json][timeout:{timeout_s}];\n(\n{parts}\n);\nout geom tags;\n"


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
