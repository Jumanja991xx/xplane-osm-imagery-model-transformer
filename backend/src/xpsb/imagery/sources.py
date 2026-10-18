"""Imagery sources. Only open sources are built in; anything else the user adds
themselves and confirms they hold the rights."""

from __future__ import annotations

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


def custom_source(
    url: str, layer: str | None, attribution: str, kind: str = "wms", max_zoom: int = 19
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
        url = settings.custom_url
        if not url.startswith(("https://", "http://")):
            return None, "imagery.custom_missing"
        if settings.custom_type == "xyz" and not all(k in url for k in ("{z}", "{x}")):
            return None, "imagery.custom_missing"
        return custom_source(
            url,
            settings.custom_layer,
            settings.custom_attribution,
            settings.custom_type,
            settings.custom_max_zoom,
        ), None
    candidates = (
        BUILTIN_SOURCES if choice == "auto" else [s for s in BUILTIN_SOURCES if s.id == choice]
    )
    for source in candidates:
        if covers(source, lon, lat):
            return source, None
    return None, "imagery.not_covered"
