"""Imagery sources. Only open sources are built in; anything else the user adds
themselves and confirms they hold the rights."""

from __future__ import annotations

import json
import urllib.parse
from functools import lru_cache
from pathlib import Path
from typing import Literal

import shapely
from pydantic import BaseModel, Field
from shapely.geometry import Point, shape


class ImagerySource(BaseModel):
    id: str
    name: str
    kind: Literal["wms", "xyz"] = "wms"
    # WMS: endpoint (GetCapabilities is read to pick the layer).
    # XYZ: tile template with {z}, {x}, {y} (or {-y} for TMS).
    url: str
    layer: str | None = None  # preferred layer; falls back to the first image layer
    attribution: str
    license: str
    # west, south, east, north – rough coverage, used to suggest the right source
    coverage: tuple[float, float, float, float] | None = None
    # Exact coverage: a country or state in coverage.json (borders from Overture Maps,
    # ODbL, simplified, 2 km wider). Wins over `coverage`.
    region: str | None = None
    max_gsd_m: float = Field(default=0.4, gt=0)  # finest ground resolution worth requesting
    builtin: bool = True
    max_zoom: int = Field(default=19, ge=1, le=23)  # XYZ only
    # XYZ only: what {s} in the URL stands for, one after the other ("a", "b", "c").
    subdomains: list[str] = Field(default_factory=list)
    # May the image go into the package as ground texture? (Esri: recognition only)
    package_ok: bool = True


BAYERN_DOP40 = ImagerySource(
    id="bayern_dop40",
    name="Bayern – Digitales Orthophoto 40 cm (OpenData)",
    url="https://geoservices.bayern.de/od/wms/dop/v1/dop40",
    layer="by_dop40c",
    attribution="Bayerische Vermessungsverwaltung – www.geodaten.bayern.de",
    license="CC BY 4.0",
    coverage=(8.9, 47.2, 13.9, 50.6),
    region="DE-BY",
    max_gsd_m=0.4,
)

NRW_DOP = ImagerySource(
    id="nrw_dop",
    name="NRW – Digitale Orthophotos (OpenData)",
    url="https://www.wms.nrw.de/geobasis/wms_nw_dop",
    layer="nw_dop_rgb",
    attribution="Land NRW (2026), Datenlizenz Deutschland – Zero – Version 2.0",
    license="dl-de/zero-2-0",
    coverage=(5.8, 50.3, 9.5, 52.6),
    region="DE-NW",
    max_gsd_m=0.2,
)

# Further open aerial imagery of countries popular in flight simulation. All may be
# used for analysis and in a shared scenery with the attribution, except Japan's
# GSI tiles (their terms forbid converting and passing on the tiles: recognition only).
SWISSIMAGE = ImagerySource(
    id="swissimage",
    name="Schweiz – swisstopo SWISSIMAGE",
    kind="xyz",
    url="https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.swissimage/default/current/3857/{z}/{x}/{y}.jpeg",
    attribution="© swisstopo",
    license="OGD swisstopo (freie Nutzung mit Quellenangabe)",
    coverage=(5.9, 45.8, 10.5, 47.9),
    region="CH",
    max_zoom=20,
    max_gsd_m=0.1,
)

BASEMAP_AT = ImagerySource(
    id="basemap_at",
    name="Österreich – basemap.at Orthofoto",
    kind="xyz",
    url="https://{s}.wien.gv.at/basemap/bmaporthofoto30cm/normal/google3857/{z}/{y}/{x}.jpeg",
    subdomains=["maps", "maps1", "maps2", "maps3", "maps4"],
    attribution="Datenquelle: basemap.at",
    license="CC BY (basemap.at)",
    coverage=(9.5, 46.3, 17.2, 49.1),
    region="AT",
    max_zoom=19,
    max_gsd_m=0.3,
)

PDOK_NL = ImagerySource(
    id="pdok_nl",
    name="Niederlande – PDOK Luchtfoto (hohe Auflösung)",
    kind="xyz",
    url="https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0/Actueel_orthoHR/EPSG:3857/{z}/{x}/{y}.jpeg",
    attribution="Beeldmateriaal.nl / PDOK",
    license="CC BY 4.0 (Beeldmateriaal Nederland)",
    coverage=(3.3, 50.7, 7.3, 53.6),
    region="NL",
    max_zoom=19,
    max_gsd_m=0.1,
)

IGN_FR = ImagerySource(
    id="ign_fr",
    name="Frankreich – IGN BD ORTHO (Géoplateforme)",
    kind="xyz",
    url=(
        "https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
        "&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&TILEMATRIXSET=PM"
        "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&FORMAT=image/jpeg"
    ),
    attribution="© IGN – BD ORTHO",
    license="Licence Ouverte Etalab 2.0",
    coverage=(-5.2, 41.3, 9.6, 51.1),
    region="FR",
    max_zoom=19,
    max_gsd_m=0.2,
)

PNOA_ES = ImagerySource(
    id="pnoa_es",
    name="Spanien – IGN PNOA",
    kind="xyz",
    url=(
        "https://www.ign.es/wmts/pnoa-ma?request=GetTile&service=WMTS&VERSION=1.0.0"
        "&Layer=OI.OrthoimageCoverage&Style=default&Format=image/jpeg"
        "&TileMatrixSet=GoogleMapsCompatible&TileMatrix={z}&TileRow={y}&TileCol={x}"
    ),
    attribution="PNOA cedido por © Instituto Geográfico Nacional de España",
    license="CC BY 4.0 (scne.es)",
    coverage=(-18.2, 27.6, 4.4, 43.9),
    region="ES",
    max_zoom=19,
    max_gsd_m=0.25,
)

USGS_NAIP = ImagerySource(
    id="usgs_naip",
    name="USA – USGS NAIP Plus (NAIP + hochauflösende Stadt-Orthos)",
    url="https://imagery.nationalmap.gov/arcgis/services/USGSNAIPPlus/ImageServer/WMSServer",
    attribution="USGS The National Map / USDA NAIP",
    license="Public Domain (US-Bundesbehörden)",
    region="US",
    max_gsd_m=0.3,
)

GSI_JP = ImagerySource(
    id="gsi_jp",
    name="Japan – GSI Seamless Photo (国土地理院)",
    kind="xyz",
    url="https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg",
    attribution="地理院タイル（国土地理院）",
    license="GSI-Nutzungsbedingungen (nur zur Erkennung, nicht im Paket)",
    coverage=(122.9, 24.0, 146.0, 45.6),
    region="JP",
    max_zoom=18,
    max_gsd_m=0.3,
    package_ok=False,
)

# Order matters: smaller areas first (a border region is covered by both neighbours).
BUILTIN_SOURCES = [
    BAYERN_DOP40,
    NRW_DOP,
    SWISSIMAGE,
    BASEMAP_AT,
    PDOK_NL,
    IGN_FR,
    PNOA_ES,
    USGS_NAIP,
    GSI_JP,
]

# Worldwide, with the user's own ArcGIS key (ArcGIS Location Platform, Esri's terms).
# Used for recognition and the AI only - not as ground texture in the package.
ESRI_ACCOUNT = "arcgis_api_key"
ESRI_TILES = (
    "https://ibasemaps-api.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/"
    "{z}/{y}/{x}?token="
)


def esri_source(key: str) -> ImagerySource:
    return ImagerySource(
        id="esri",
        name="Esri World Imagery (weltweit, dein ArcGIS-Konto)",
        kind="xyz",
        url=ESRI_TILES + urllib.parse.quote(key, safe=""),
        attribution="Esri, Maxar, Earthstar Geographics, and the GIS User Community",
        license="Esri-Nutzungsbedingungen (nur zur Erkennung, nicht im Paket)",
        max_zoom=19,
        max_gsd_m=0.3,
        package_ok=False,
    )


def custom_source(
    url: str,
    layer: str | None,
    attribution: str,
    kind: str = "wms",
    max_zoom: int = 19,
    subdomains: list[str] | None = None,
) -> ImagerySource:
    return ImagerySource(
        id="custom",
        name="Eigene XYZ-Quelle" if kind == "xyz" else "Eigene WMS-Quelle",
        kind=kind,  # type: ignore[arg-type]
        url=url,
        layer=layer or None,
        attribution=attribution or "Eigene Quelle",
        license="vom Nutzer bestätigt",
        builtin=False,
        max_zoom=max_zoom,
        max_gsd_m=0.15,
        subdomains=subdomains or [],
    )


def xyz_template_ok(url: str, subdomains: list[str]) -> bool:
    """An XYZ template we can fill: {z}, {x}, {y} or {-y}, and subdomains for {s}."""
    return (
        url.startswith(("https://", "http://"))
        and all(k in url for k in ("{z}", "{x}"))
        and ("{y}" in url or "{-y}" in url)
        and ("{s}" not in url or bool(subdomains))
    )


def custom_from_settings(settings) -> ImagerySource | None:
    """The user's own source from the settings, or None while it is incomplete."""
    url = settings.custom_url
    if not url.startswith(("https://", "http://")):
        return None
    if settings.custom_type == "xyz" and not xyz_template_ok(url, settings.subdomains):
        return None
    return custom_source(
        url,
        settings.custom_layer,
        settings.custom_attribution,
        settings.custom_type,
        settings.custom_max_zoom,
        settings.subdomains,
    )


def map_source(settings) -> ImagerySource | None:
    """The user's XYZ source as map background: only when complete, confirmed and wanted."""
    if not (
        settings.source == "custom"
        and settings.custom_type == "xyz"
        and settings.custom_rights_confirmed
        and settings.custom_on_map
    ):
        return None
    return custom_from_settings(settings)


@lru_cache(maxsize=1)
def _regions() -> dict:
    data = json.loads((Path(__file__).with_name("coverage.json")).read_text(encoding="utf-8"))
    shapes = {code: shape(geom) for code, geom in data.items()}
    for geom in shapes.values():
        shapely.prepare(geom)
    return shapes


def covers(source: ImagerySource, lon: float, lat: float) -> bool:
    if source.coverage is not None:
        w, s, e, n = source.coverage
        if not (w <= lon <= e and s <= lat <= n):
            return False
    if source.region is not None:
        region = _regions().get(source.region)
        return region is None or region.contains(Point(lon, lat))
    return True


def candidates(
    settings, lon: float, lat: float, esri_key: str | None = None
) -> list[ImagerySource]:
    """All sources the settings allow for the place, best first ("auto": the open
    sources covering it, then Esri with the user's key). The analysis tries the next
    one when a server fails."""
    first, _ = resolve_source(settings, lon, lat, esri_key)
    if first is None:
        return []
    if settings.source != "auto":
        return [first]
    out = [s for s in BUILTIN_SOURCES if covers(s, lon, lat)]
    if esri_key:
        out.append(esri_source(esri_key))
    return out


def resolve_source(
    settings, lon: float, lat: float, esri_key: str | None = None
) -> tuple[ImagerySource | None, str | None]:
    """The source a job will use, or (None, reason i18n key). "auto" takes an open
    source that covers the area and falls back to Esri if the user has a key."""
    choice = settings.source
    if choice == "none":
        return None, "imagery.off"
    if choice == "esri":
        return (esri_source(esri_key), None) if esri_key else (None, "imagery.esri_no_key")
    if choice == "custom":
        source = custom_from_settings(settings)
        return (source, None) if source else (None, "imagery.custom_missing")
    candidates = (
        BUILTIN_SOURCES if choice == "auto" else [s for s in BUILTIN_SOURCES if s.id == choice]
    )
    for source in candidates:
        if covers(source, lon, lat):
            return source, None
    if choice == "auto" and esri_key:
        return esri_source(esri_key), None
    return None, "imagery.not_covered"
