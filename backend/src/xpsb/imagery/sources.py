"""Imagery sources. Only open sources are built in; anything else the user adds
themselves and confirms they hold the rights."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ImagerySource(BaseModel):
    id: str
    name: str
    url: str  # WMS endpoint (GetCapabilities is read to pick the layer)
    layer: str | None = None  # preferred layer; falls back to the first image layer
    attribution: str
    license: str
    # west, south, east, north – rough coverage, used to suggest the right source
    coverage: tuple[float, float, float, float] | None = None
    max_gsd_m: float = Field(default=0.4, gt=0)  # finest ground resolution worth requesting
    builtin: bool = True


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


def custom_source(url: str, layer: str | None, attribution: str) -> ImagerySource:
    return ImagerySource(
        id="custom",
        name="Eigene WMS-Quelle",
        url=url,
        layer=layer or None,
        attribution=attribution or "Eigene Quelle",
        license="vom Nutzer bestätigt",
        builtin=False,
    )


def covers(source: ImagerySource, lon: float, lat: float) -> bool:
    if source.coverage is None:
        return True
    w, s, e, n = source.coverage
    return w <= lon <= e and s <= lat <= n


def resolve_source(settings, lon: float, lat: float) -> tuple[ImagerySource | None, str | None]:
    """The source a job will use, or (None, reason i18n key)."""
    choice = settings.source
    if choice == "none":
        return None, "imagery.off"
    if choice == "custom":
        if not settings.custom_url.startswith(("https://", "http://")):
            return None, "imagery.custom_missing"
        return custom_source(
            settings.custom_url, settings.custom_layer, settings.custom_attribution
        ), None
    candidates = (
        BUILTIN_SOURCES if choice == "auto" else [s for s in BUILTIN_SOURCES if s.id == choice]
    )
    for source in candidates:
        if covers(source, lon, lat):
            return source, None
    return None, "imagery.not_covered"
