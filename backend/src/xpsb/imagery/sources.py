"""Imagery sources. Only open sources are built in; anything else the user adds
themselves and confirms they hold the rights."""

from __future__ import annotations

import urllib.parse
from typing import Literal

from pydantic import BaseModel, Field


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
    max_gsd_m=0.2,
)

BUILTIN_SOURCES = [BAYERN_DOP40, NRW_DOP]

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


def covers(source: ImagerySource, lon: float, lat: float) -> bool:
    if source.coverage is None:
        return True
    w, s, e, n = source.coverage
    return w <= lon <= e and s <= lat <= n


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
