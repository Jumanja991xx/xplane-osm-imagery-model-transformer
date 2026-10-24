"""Photos of real objects for the AI architect.

Two ways in: the user uploads own photos (and confirms the rights), or picks
photos from open sources near the object (Wikimedia Commons, Panoramax and,
with the user's own token, Mapillary - the open counterpart of Street View).
Street-level photos carry the camera direction: those looking at the object come
first, and of a 360° panorama only the view towards the object is kept.
Photos are input for the AI; one goes into the scenery package only when the user
or the AI cuts a facade out of it as texture AND its licence allows sharing it
(see redistributable; the authors are named in the README). Stored re-encoded as
JPEG (this also drops EXIF data such as GPS positions of private photos).
"""

from __future__ import annotations

import base64
import binascii
import html
import io
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Literal

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, computed_field

from xpsb.geo import METERS_PER_DEG_LAT, meters_per_deg_lon
from xpsb.osm.overpass import USER_AGENT

MAX_UPLOAD_BYTES = 25_000_000
STORE_MAX_PX = 1600
SEND_MAX_PX = 1024  # what goes to the AI
MAX_PHOTOS_PER_ITEM = 8
SEARCH_RADIUS_M = 250
MAPILLARY_ACCOUNT = "mapillary_token"

PhotoSource = Literal["user", "wikimedia", "panoramax", "mapillary"]

SOURCE_NAMES = {
    "wikimedia": "Wikimedia Commons",
    "panoramax": "Panoramax",
    "mapillary": "Mapillary",
}
WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"
PANORAMAX_API = "https://api.panoramax.xyz/api/search"
MAPILLARY_API = "https://graph.mapillary.com/images"


class PhotoError(ValueError):
    pass


# Licences that allow sharing changed copies (attribution / share-alike).
_FREE = re.compile(
    r"^(cc[-_ ]?0|cc[-_ ]?zero|pd|public[-_ ]?domain|cc[-_ ]?by([-_ ]?sa)?([-_ ]?[0-9.]+)?"
    r"([-_ ]?[a-z]{2})?|etalab([-_ ]?[0-9.]+)?|licence[-_ ]?ouverte.*|odbl.*)$",
    re.IGNORECASE,
)


class StreetCamera(BaseModel):
    """Where a street-level photo was taken and where it looks (streetfacade.py paints
    walls from it): position, compass direction of the image centre, and either the
    horizontal field of view (ordinary photo) or the angles a (cut-out of a) 360°
    panorama spans."""

    lon: float
    lat: float
    heading: float
    pano: bool = False
    fov: float | None = None  # ordinary photos; None: unknown (a usual 70°)
    yaw_span: float = 360.0  # panoramas: degrees across the image width
    pitch_span: float = 180.0  # ... and its height (horizon in the middle row)


class Photo(BaseModel):
    """A photo attached to a scene item (file: photos/<id>.jpg in the job folder)."""

    id: str
    source: PhotoSource = "user"
    title: str | None = None
    author: str | None = None
    license: str | None = None
    page_url: str | None = None
    camera: StreetCamera | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def shareable(self) -> bool:
        """May it go into a scenery package as a texture (see redistributable)?"""
        return redistributable(self)


def redistributable(photo: Photo) -> bool:
    """May this photo go into a scenery package that people share? Own photos (the
    user confirmed the rights), public domain, CC0, CC BY, CC BY-SA, Etalab."""
    if photo.source == "user":
        return True
    lic = (photo.license or "").strip()
    return bool(lic) and bool(_FREE.match(lic)) and "nc" not in lic.lower().split("-")


class PhotoCandidate(BaseModel):
    """A search hit; only its id goes back to the client for thumbnails and import."""

    id: str
    source: PhotoSource
    title: str | None = None
    author: str | None = None
    license: str | None = None
    page_url: str | None = None
    distance_m: float | None = None
    thumb_url: str  # kept server-side; the client asks for /photo-thumb/<id>
    image_url: str
    # Street-level photos: camera direction, 360° panorama, direction to the object.
    heading: float | None = None
    pano: bool = False
    bearing: float | None = None
    facing: bool | None = None  # None: unknown (no camera direction)
    cam_lon: float | None = None
    cam_lat: float | None = None
    fov: float | None = None  # horizontal field of view of ordinary photos


# ---- storing ---------------------------------------------------------------------


def _normalize(raw: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(raw)) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise PhotoError("not an image") from exc
    img.thumbnail((STORE_MAX_PX, STORE_MAX_PX), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)  # no EXIF: positions/camera data are dropped
    return buf.getvalue()


def decode_upload(data_b64: str) -> bytes:
    data = data_b64.split(",", 1)[1] if data_b64.startswith("data:") else data_b64
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise PhotoError("invalid data") from exc
    if len(raw) > MAX_UPLOAD_BYTES:
        raise PhotoError("file too large")
    return raw


def store(folder: Path, raw: bytes, meta: dict) -> Photo:
    folder.mkdir(parents=True, exist_ok=True)
    photo = Photo(id=uuid.uuid4().hex[:12], **meta)
    (folder / f"{photo.id}.jpg").write_bytes(_normalize(raw))
    return photo


def for_ai(path: Path) -> bytes:
    """PNG-free, smaller JPEG for sending (token cost scales with pixels)."""
    with Image.open(path) as img:
        img = img.convert("RGB")
    img.thumbnail((SEND_MAX_PX, SEND_MAX_PX), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


# ---- open sources ----------------------------------------------------------------


def _fixtures() -> Path | None:
    value = os.environ.get("XPSB_FIXTURES_DIR")
    return Path(value) / "photos" if value else None


def http_get(url: str, timeout: float = 30) -> bytes:
    """GET with the app's user agent. Offline fixtures replace the network in tests."""
    fx = _fixtures()
    if fx is not None:
        host = urllib.parse.urlparse(url).hostname or ""
        name = next(
            (
                k
                for k in ("wikidata", "wikipedia", "wikimedia", "panoramax", "mapillary")
                if k in host
            ),
            None,
        )
        path = urllib.parse.urlparse(url).path.lower()
        if path.endswith((".jpg", ".jpeg", ".png")) or name is None:
            return (fx / "sample.jpg").read_bytes()
        return (fx / f"{name}.json").read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(MAX_UPLOAD_BYTES + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise PhotoError(f"{urllib.parse.urlparse(url).hostname}: {exc}") from exc


def _distance_m(lon: float, lat: float, lon2: float, lat2: float) -> float:
    return math.hypot((lon2 - lon) * meters_per_deg_lon(lat), (lat2 - lat) * METERS_PER_DEG_LAT)


FACING_DEG = 40.0  # half the usual field of view of a street-level camera


def _bearing(cam_lon: float, cam_lat: float, lon: float, lat: float) -> float:
    """Compass direction from the camera to the object (0 = north, clockwise)."""
    dx = (lon - cam_lon) * meters_per_deg_lon(cam_lat)
    dy = (lat - cam_lat) * METERS_PER_DEG_LAT
    return math.degrees(math.atan2(dx, dy)) % 360.0


def _turn(heading: float, bearing: float) -> float:
    """Signed angle from the camera direction to the object, -180..180."""
    return (bearing - heading + 540.0) % 360.0 - 180.0


def _aim(c: PhotoCandidate, geom: list, lon: float, lat: float) -> PhotoCandidate:
    """Fills bearing/facing from the camera position and direction."""
    if len(geom) < 2 or c.heading is None:
        return c
    c.cam_lon, c.cam_lat = float(geom[0]), float(geom[1])
    c.bearing = round(_bearing(geom[0], geom[1], lon, lat), 1)
    c.facing = c.pano or abs(_turn(c.heading, c.bearing)) <= FACING_DEG
    return c


VIEW_FOV_DEG = 90.0  # the cut-out of a panorama: 90° wide, 90° high


def view_towards(raw: bytes, heading: float, bearing: float, fov: float = VIEW_FOV_DEG) -> bytes:
    """The part of an equirectangular 360° panorama that looks at the object (the
    image centre is the camera direction): like turning around in Street View."""
    try:
        with Image.open(io.BytesIO(raw)) as img:
            pano = img.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise PhotoError("not an image") from exc
    w, h = pano.size
    if w < 2 * h * 0.9:  # not equirectangular: keep the photo as it is
        return raw
    cx = (w / 2 + _turn(heading, bearing) / 360.0 * w) % w
    half = int(w * fov / 360.0 / 2)
    wide = Image.new("RGB", (w * 2, h))  # two copies side by side: crops across the seam
    wide.paste(pano, (0, 0))
    wide.paste(pano, (w, 0))
    left = int(cx) - half + (w if cx - half < 0 else 0)
    view = wide.crop((left, int(h * 0.25), left + 2 * half, int(h * 0.75)))
    buf = io.BytesIO()
    view.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def download(c: PhotoCandidate) -> bytes:
    """The candidate's image; for panoramas only the view towards the object."""
    raw = http_get(c.image_url)
    if c.pano and c.heading is not None and c.bearing is not None:
        return view_towards(raw, c.heading, c.bearing)
    return raw


def camera_of(c: PhotoCandidate, image: bytes) -> StreetCamera | None:
    """The camera of a downloaded street-level photo (`image` as download() returned
    it), None without position or direction."""
    if c.cam_lon is None or c.cam_lat is None or c.heading is None:
        return None
    if not c.pano:
        return StreetCamera(lon=c.cam_lon, lat=c.cam_lat, heading=c.heading, fov=c.fov)
    try:
        with Image.open(io.BytesIO(image)) as img:
            w, h = img.size
    except (UnidentifiedImageError, OSError):
        return None
    if abs(w / h - 1.0) < 0.1 and c.bearing is not None:  # the cut-out towards the object
        return StreetCamera(
            lon=c.cam_lon,
            lat=c.cam_lat,
            heading=c.bearing,
            pano=True,
            yaw_span=VIEW_FOV_DEG,
            pitch_span=VIEW_FOV_DEG,
        )
    if w >= 2 * h * 0.9:
        return StreetCamera(lon=c.cam_lon, lat=c.cam_lat, heading=c.heading, pano=True)
    return None  # a "panorama" in some other projection


def _bbox(lon: float, lat: float, radius: float) -> tuple[float, float, float, float]:
    dx, dy = radius / meters_per_deg_lon(lat), radius / METERS_PER_DEG_LAT
    return lon - dx, lat - dy, lon + dx, lat + dy


def _plain(value: str | None) -> str | None:
    """Commons metadata comes as HTML snippets."""
    if not value:
        return None
    text = html.unescape(re.sub(r"<[^>]+>", "", value)).strip()
    return text[:200] or None


def search_wikimedia(lon: float, lat: float, radius: float) -> list[PhotoCandidate]:
    params = {
        "action": "query",
        "format": "json",
        "generator": "geosearch",
        "ggscoord": f"{lat}|{lon}",
        "ggsradius": str(int(radius)),
        "ggslimit": "40",
        "ggsnamespace": "6",
        "prop": "imageinfo|coordinates",
        "iiprop": "url|extmetadata|mime",
        "iiurlwidth": "320",
    }
    data = json.loads(http_get(WIKIMEDIA_API + "?" + urllib.parse.urlencode(params)))
    return commons_candidates(data, lon, lat)


COMMONS_IMAGEINFO = {
    "action": "query",
    "format": "json",
    "prop": "imageinfo|coordinates",
    "iiprop": "url|extmetadata|mime",
    "iiurlwidth": "320",
}


def commons_query(params: dict[str, str], lon: float, lat: float) -> list[PhotoCandidate]:
    """Any Commons file query (titles, category members, geosearch) as candidates."""
    url = WIKIMEDIA_API + "?" + urllib.parse.urlencode({**COMMONS_IMAGEINFO, **params})
    return commons_candidates(json.loads(http_get(url)), lon, lat)


def commons_candidates(data: dict, lon: float, lat: float) -> list[PhotoCandidate]:
    out = []
    for page in (data.get("query") or {}).get("pages", {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        if not str(info.get("mime", "image/jpeg")).startswith("image/") or not info.get("thumburl"):
            continue
        meta = info.get("extmetadata") or {}
        coord = (page.get("coordinates") or [{}])[0]
        big = info["thumburl"].replace("/320px-", "/1280px-")
        out.append(
            PhotoCandidate(
                id=uuid.uuid4().hex[:12],
                source="wikimedia",
                title=str(page.get("title", "")).removeprefix("File:")[:200] or None,
                author=_plain((meta.get("Artist") or {}).get("value")),
                license=_plain((meta.get("LicenseShortName") or {}).get("value")),
                page_url=info.get("descriptionurl"),
                distance_m=_distance_m(lon, lat, coord["lon"], coord["lat"])
                if "lon" in coord
                else None,
                thumb_url=info["thumburl"],
                image_url=big if "/thumb/" in big else info.get("url", big),
            )
        )
    return out


def search_panoramax(lon: float, lat: float, radius: float) -> list[PhotoCandidate]:
    w, s, e, n = _bbox(lon, lat, radius)
    params = {"bbox": f"{w:.6f},{s:.6f},{e:.6f},{n:.6f}", "limit": "40"}
    data = json.loads(http_get(PANORAMAX_API + "?" + urllib.parse.urlencode(params)))
    out = []
    for feat in data.get("features", []):
        assets = feat.get("assets") or {}
        thumb = (assets.get("thumb") or {}).get("href")
        image = (assets.get("sd") or assets.get("hd") or {}).get("href")
        if not thumb or not image:
            continue
        props = feat.get("properties") or {}
        providers = feat.get("providers") or props.get("providers") or []
        geom = (feat.get("geometry") or {}).get("coordinates") or []
        azimuth = props.get("view:azimuth")
        fov = (props.get("pers:interior_orientation") or {}).get("field_of_view")
        c = PhotoCandidate(
            id=uuid.uuid4().hex[:12],
            source="panoramax",
            title=props.get("datetime"),
            author=(providers[0] or {}).get("name") if providers else None,
            license=props.get("license") or "CC-BY-SA-4.0",
            page_url=f"https://panoramax.xyz/#focus=pic&pic={feat.get('id')}",
            distance_m=_distance_m(lon, lat, geom[0], geom[1]) if len(geom) >= 2 else None,
            thumb_url=thumb,
            image_url=image,
            heading=float(azimuth) if isinstance(azimuth, (int, float)) else None,
            pano=isinstance(fov, (int, float)) and fov >= 359,
            fov=float(fov) if isinstance(fov, (int, float)) and 10 <= fov < 180 else None,
        )
        out.append(_aim(c, geom, lon, lat))
    return out


def search_mapillary(lon: float, lat: float, radius: float, token: str) -> list[PhotoCandidate]:
    w, s, e, n = _bbox(lon, lat, min(radius, 120))  # the API limits the bbox size
    params = {
        "access_token": token,
        "fields": "id,thumb_256_url,thumb_1024_url,thumb_2048_url,captured_at,computed_geometry,"
        "creator,compass_angle,computed_compass_angle,is_pano",
        "bbox": f"{w:.6f},{s:.6f},{e:.6f},{n:.6f}",
        "limit": "40",
    }
    try:
        data = json.loads(http_get(MAPILLARY_API + "?" + urllib.parse.urlencode(params)))
    except PhotoError as exc:  # never show the token in an error message
        raise PhotoError(str(exc).replace(token, "***")) from None
    out = []
    for img in data.get("data", []):
        if not img.get("thumb_256_url") or not img.get("thumb_1024_url"):
            continue
        geom = (img.get("computed_geometry") or {}).get("coordinates") or []
        heading = img.get("computed_compass_angle", img.get("compass_angle"))
        pano = bool(img.get("is_pano"))
        c = PhotoCandidate(
            id=uuid.uuid4().hex[:12],
            source="mapillary",
            title=str(img.get("captured_at") or "") or None,
            author=(img.get("creator") or {}).get("username"),
            license="CC-BY-SA-4.0",
            page_url=f"https://www.mapillary.com/app/?pKey={img.get('id')}",
            distance_m=_distance_m(lon, lat, geom[0], geom[1]) if len(geom) >= 2 else None,
            thumb_url=img["thumb_256_url"],
            # panoramas: the larger image, only the view towards the object is kept
            image_url=(img.get("thumb_2048_url") if pano else None) or img["thumb_1024_url"],
            heading=float(heading) if isinstance(heading, (int, float)) else None,
            pano=pano,
        )
        out.append(_aim(c, geom, lon, lat))
    return out


class SearchResult(BaseModel):
    candidates: list[PhotoCandidate]
    errors: dict[str, str]  # source -> message (the others still deliver)


def search(
    lon: float, lat: float, mapillary_token: str | None, radius: float = SEARCH_RADIUS_M
) -> SearchResult:
    candidates: list[PhotoCandidate] = []
    errors: dict[str, str] = {}
    sources = [
        ("wikimedia", lambda: search_wikimedia(lon, lat, radius)),
        ("panoramax", lambda: search_panoramax(lon, lat, radius)),
    ]
    if mapillary_token:
        sources.append(("mapillary", lambda: search_mapillary(lon, lat, radius, mapillary_token)))
    for name, fn in sources:
        try:
            candidates += fn()
        except (PhotoError, ValueError, KeyError, TypeError) as exc:
            errors[name] = str(exc)[:200]
    # Photos that look at the object first (then by distance); those looking away last.
    candidates.sort(
        key=lambda c: (c.facing is False, c.distance_m if c.distance_m is not None else 1e9)
    )
    return SearchResult(candidates=candidates[:60], errors=errors)
