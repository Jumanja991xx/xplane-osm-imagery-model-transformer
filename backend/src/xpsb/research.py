"""Research per object: what is it and how does it look?

For the important objects of an area (named, linked to Wikidata/Wikipedia or
large) this collects free facts and photos: Wikidata (type, height, capacity,
year, architect, style), the Wikipedia summary, Wikimedia Commons photos (main
image, the object's category, photos nearby) and street photos from Panoramax
or Mapillary. No AI and no costs; only names and positions leave the computer.
The AI architect builds from this later.
"""

from __future__ import annotations

import json
import math
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from shapely.geometry import shape

from xpsb import photos
from xpsb.geo import METERS_PER_DEG_LAT, meters_per_deg_lon
from xpsb.photos import PhotoCandidate, PhotoError, http_get
from xpsb.scene import Research, SceneItem

WIKIDATA_ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIPEDIA_SUMMARY = "https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"
MAX_OBJECTS = 15
FEW_OBJECTS = 3  # up to this many objects in the area: all are researched and designed
MAX_PHOTOS = 5
NAME_MATCH_M = 800  # a Wikidata item found by name must be this close
EXTRACT_CHARS = 1200
# Titles that are rarely photos of the building itself.
SKIP_TITLE = re.compile(
    r"(map|plan|logo|karte|grundriss|lageplan|diagram|wappen|coat of arms|\.svg|\.pdf|\.tif)",
    re.IGNORECASE,
)
IDENTITY_TYPES = {"stadium", "church", "cathedral", "chapel", "castle", "palace", "museum"}


# ---- which objects ----------------------------------------------------------------


def _area_m2(item: SceneItem) -> float:
    geom = shape(item.geometry)
    c = geom.centroid
    return geom.area * meters_per_deg_lon(c.y) * METERS_PER_DEG_LAT


def score(item: SceneItem) -> float:
    """How much an object deserves research and an individual design."""
    tags = item.tags
    s = 0.0
    if tags.get("wikidata") or tags.get("wikipedia"):
        s += 100
    if item.name:
        s += 40
    if (
        (tags.get("building") or "") in IDENTITY_TYPES
        or tags.get("tourism")
        or tags.get("historic")
        or tags.get("site:main") == "yes"
    ):
        s += 30
    if str(item.category) == "grandstands":
        s += 15
    if item.source == "user":  # drawn by hand: the user cares about this one
        s += 30
    area = _area_m2(item) if item.geometry.get("type") != "Point" else 0
    s += min(40.0, area / 250.0)
    return s


def important(items: list[SceneItem], limit: int = MAX_OBJECTS) -> list[SceneItem]:
    """Objects worth it: identified, named, large (or stands); best first. In a small
    area with only a few objects (one house, one church) all of them count."""
    pool = [
        it
        for it in items
        if it.kind == "object"
        and it.included
        and not it.proposal
        and str(it.category) != "barriers"
    ]
    if len(pool) > FEW_OBJECTS:
        pool = [it for it in pool if score(it) >= 30]
    return sorted(pool, key=score, reverse=True)[:limit]


# ---- Wikidata / Wikipedia ---------------------------------------------------------


def _get_json(url: str) -> dict:
    return json.loads(http_get(url))


def _claims(entity: dict, prop: str) -> list[dict]:
    out = []
    for c in (entity.get("claims") or {}).get(prop, []):
        value = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if value is not None:
            out.append(value)
    return out


def _quantity(entity: dict, prop: str) -> float | None:
    for v in _claims(entity, prop):
        try:
            amount = float(str(v.get("amount", "")).lstrip("+"))
        except ValueError:
            continue
        unit = str(v.get("unit", ""))
        if unit.endswith("/Q174728"):  # centimetre
            amount /= 100
        elif unit.endswith("/Q3710"):  # foot
            amount *= 0.3048
        return amount
    return None


def _year(entity: dict, prop: str) -> str | None:
    for v in _claims(entity, prop):
        m = re.match(r"[+-]?(\d{4})", str(v.get("time", "")))
        if m:
            return m.group(1)
    return None


def _label(entity: dict, langs: tuple[str, ...]) -> str | None:
    for lang in langs:
        value = ((entity.get("labels") or {}).get(lang) or {}).get("value")
        if value:
            return value
    return None


def _description(entity: dict, langs: tuple[str, ...]) -> str | None:
    for lang in langs:
        value = ((entity.get("descriptions") or {}).get(lang) or {}).get("value")
        if value:
            return value
    return None


def fetch_entity(qid: str) -> dict | None:
    if not re.fullmatch(r"Q\d+", qid):
        return None
    data = _get_json(WIKIDATA_ENTITY.format(qid=qid))
    return (data.get("entities") or {}).get(qid)


def find_by_name(name: str, lon: float, lat: float, lang: str) -> tuple[str, dict] | None:
    """Wikidata item with this name close to the object (or None)."""
    params = {
        "action": "wbsearchentities",
        "search": name,
        "language": lang,
        "format": "json",
        "limit": "5",
    }
    hits = [
        h.get("id")
        for h in _get_json(WIKIDATA_API + "?" + urllib.parse.urlencode(params)).get("search", [])
    ]
    hits = [h for h in hits if h and re.fullmatch(r"Q\d+", h)]
    if not hits:
        return None
    params = {
        "action": "wbgetentities",
        "ids": "|".join(hits),
        "props": "claims|labels|descriptions|sitelinks",
        "format": "json",
    }
    entities = _get_json(WIKIDATA_API + "?" + urllib.parse.urlencode(params)).get("entities", {})
    for qid in hits:
        entity = entities.get(qid) or {}
        for c in _claims(entity, "P625"):
            d = math.hypot(
                (c.get("longitude", 999) - lon) * meters_per_deg_lon(lat),
                (c.get("latitude", 999) - lat) * METERS_PER_DEG_LAT,
            )
            if d <= NAME_MATCH_M:
                return qid, entity
    return None


def item_labels(qids: list[str], langs: tuple[str, ...]) -> dict[str, str]:
    qids = [q for q in dict.fromkeys(qids) if re.fullmatch(r"Q\d+", q)][:40]
    if not qids:
        return {}
    params = {"action": "wbgetentities", "ids": "|".join(qids), "props": "labels", "format": "json"}
    entities = _get_json(WIKIDATA_API + "?" + urllib.parse.urlencode(params)).get("entities", {})
    return {q: _label(e, langs) or q for q, e in entities.items()}


def wikipedia_summary(lang: str, title: str) -> tuple[str, str] | None:
    url = WIKIPEDIA_SUMMARY.format(lang=lang, title=urllib.parse.quote(title.replace(" ", "_")))
    data = _get_json(url)
    extract = (data.get("extract") or "").strip()
    page = ((data.get("content_urls") or {}).get("desktop") or {}).get("page") or url
    return (extract[:EXTRACT_CHARS], page) if extract else None


# ---- one object -------------------------------------------------------------------

SOURCE_ERRORS = (PhotoError, ValueError, KeyError, TypeError, AttributeError)


def _safe(fn, default):
    """One source failing (offline, changed API) must not spoil the others."""
    try:
        return fn()
    except SOURCE_ERRORS:
        return default


@dataclass
class Found:
    research: Research
    candidates: list[PhotoCandidate] = field(default_factory=list)
    height_m: float | None = None


def research_item(item: SceneItem, language: str, mapillary_token: str | None) -> Found:
    langs = (language, "en", "de", "hu", "fr", "it", "es")
    geom = shape(item.geometry)
    p = geom.representative_point()
    lon, lat = p.x, p.y
    tags = item.tags
    lines: list[str] = []
    sources: list[str] = []
    candidates: list[PhotoCandidate] = []
    height = None

    entity: dict | None = None
    qid = tags.get("wikidata")
    if qid:
        entity = _safe(lambda: fetch_entity(qid), None)
    elif item.name:
        hit = _safe(lambda: find_by_name(item.name, lon, lat, language), None)
        if hit:
            qid, entity = hit

    wiki_lang, wiki_title = None, None
    if tags.get("wikipedia") and ":" in tags["wikipedia"]:
        wiki_lang, wiki_title = tags["wikipedia"].split(":", 1)

    if entity:
        sources.append(f"https://www.wikidata.org/wiki/{qid}")
        types = [v.get("id") for v in _claims(entity, "P31")]
        people = [v.get("id") for v in _claims(entity, "P84")]
        styles = [v.get("id") for v in _claims(entity, "P149")]
        materials = [v.get("id") for v in _claims(entity, "P186")]
        names = _safe(lambda: item_labels(types + people + styles + materials, langs), {})
        lines.append(f"Name: {_label(entity, langs) or item.name} (Wikidata {qid})")
        if types:
            lines.append("Type: " + ", ".join(names.get(t, t) for t in types[:3]))
        desc = _description(entity, langs)
        if desc:
            lines.append(f"Description: {desc}")
        height = _quantity(entity, "P2048")
        facts = [
            ("Height", height, "m"),
            ("Length", _quantity(entity, "P2043"), "m"),
            ("Width", _quantity(entity, "P2049"), "m"),
            ("Floors above ground", _quantity(entity, "P1101"), ""),
            ("Capacity", _quantity(entity, "P1083"), "people"),
        ]
        for name, value, unit in facts:
            if value is not None:
                lines.append(f"{name}: {value:g} {unit}".rstrip())
        year = _year(entity, "P1619") or _year(entity, "P571")
        if year:
            lines.append(f"Opened/built: {year}")
        for label, ids in (("Architect", people), ("Style", styles), ("Material", materials)):
            if ids:
                lines.append(f"{label}: " + ", ".join(names.get(i, i) for i in ids[:3]))
        # Photos: main image, then the object's Commons category.
        for file in [v for v in _claims(entity, "P18") if isinstance(v, str)][:1]:
            candidates += _safe(
                lambda f=file: photos.commons_query({"titles": f"File:{f}"}, lon, lat), []
            )
        for cat in [v for v in _claims(entity, "P373") if isinstance(v, str)][:1]:
            params = {
                "generator": "categorymembers",
                "gcmtitle": f"Category:{cat}",
                "gcmtype": "file",
                "gcmlimit": "25",
            }
            candidates += _safe(lambda p=params: photos.commons_query(p, lon, lat), [])
        if not wiki_title:
            links = entity.get("sitelinks") or {}
            for lang in langs:
                link = links.get(f"{lang}wiki")
                if link and link.get("title"):
                    wiki_lang, wiki_title = lang, link["title"]
                    break
    elif item.name:
        lines.append(f"Name: {item.name}")

    if wiki_title:
        summary = _safe(lambda: wikipedia_summary(wiki_lang or "en", wiki_title), None)
        if summary:
            lines.append(f"Wikipedia: {summary[0]}")
            sources.append(summary[1])

    osm = ", ".join(f"{k}={v}" for k, v in sorted(tags.items()) if k not in ("name",))
    if osm:
        lines.append(f"OSM tags: {osm}")

    # Photos taken near the object (Commons, Panoramax, Mapillary) complete the set.
    if len(candidates) < MAX_PHOTOS:
        w, s, e, n = geom.bounds
        radius = max(
            60.0, 0.6 * math.hypot((e - w) * meters_per_deg_lon(lat), (n - s) * METERS_PER_DEG_LAT)
        )
        nearby = _safe(
            lambda: photos.search(lon, lat, mapillary_token, radius=min(radius, 400)), None
        )
        if nearby is not None:  # street-level photos only if they look at the object
            candidates += [
                c
                for c in nearby.candidates
                if (c.distance_m or 0) <= radius and c.facing is not False
            ]

    chosen: list[PhotoCandidate] = []
    seen: set[str] = set()
    for c in candidates:
        key = c.image_url
        if key in seen or (c.title and SKIP_TITLE.search(c.title)):
            continue
        seen.add(key)
        chosen.append(c)
        if len(chosen) >= MAX_PHOTOS:
            break
    return Found(
        research=Research(
            facts="\n".join(lines),
            wikidata=qid,
            sources=sources,
        ),
        candidates=chosen,
        height_m=height,
    )


def attach_photos(item: SceneItem, found: Found, folder: Path) -> int:
    """Downloads the chosen photos onto the item (skips what fails)."""
    added = 0
    have = {p.page_url for p in item.photos if p.page_url}
    for c in found.candidates:
        if len(item.photos) >= photos.MAX_PHOTOS_PER_ITEM:
            break
        if c.page_url and c.page_url in have:
            continue
        try:
            raw = photos.download(c)
            meta = c.model_dump(include={"source", "title", "author", "license", "page_url"})
            item.photos.append(photos.store(folder, raw, meta))
            added += 1
        except PhotoError:
            continue
    return added
