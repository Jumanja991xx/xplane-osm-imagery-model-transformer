"""Minimal WMS client: pick a layer from GetCapabilities, fetch an EPSG:4326
image of the area (tiled for large areas) and keep it with its exact bounds.

EPSG:4326 on purpose: the image is then a regular lon/lat grid, which is what
X-Plane maps onto draped polygons (texture coordinates are linear in lon/lat).
"""

from __future__ import annotations

import io
import json
import logging
import math
import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from pydantic import BaseModel

from xpsb.geo import METERS_PER_DEG_LAT, meters_per_deg_lon
from xpsb.imagery.sources import ImagerySource
from xpsb.osm.overpass import USER_AGENT

log = logging.getLogger(__name__)

TILE_PX = 2048
MAX_SIDE_PX = 6000
MARGIN_M = 30.0


class WmsError(RuntimeError):
    pass


class ImageryInfo(BaseModel):
    """Stored next to the image; also what the UI and the gates show."""

    source_id: str
    source_name: str
    layer: str
    attribution: str
    license: str
    west: float
    south: float
    east: float
    north: float
    width_px: int
    height_px: int
    gsd_m: float
    file: str = "aoi.jpg"


@dataclass(frozen=True)
class Bounds:
    west: float
    south: float
    east: float
    north: float

    def size_m(self) -> tuple[float, float]:
        lat = (self.south + self.north) / 2
        return (
            (self.east - self.west) * meters_per_deg_lon(lat),
            (self.north - self.south) * METERS_PER_DEG_LAT,
        )

    def grow_m(self, m: float) -> Bounds:
        lat = (self.south + self.north) / 2
        dx, dy = m / meters_per_deg_lon(lat), m / METERS_PER_DEG_LAT
        return Bounds(self.west - dx, self.south - dy, self.east + dx, self.north + dy)


def _fixtures() -> Path | None:
    value = os.environ.get("XPSB_FIXTURES_DIR")
    return Path(value) if value else None


def _http_get(url: str, timeout: float = 60) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), resp.headers.get("Content-Type", "")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise WmsError(f"{url.split('?')[0]}: {exc}") from exc


def _with_query(url: str, params: dict[str, str]) -> str:
    sep = "&" if "?" in url else "?"
    return url + sep + urllib.parse.urlencode(params)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def capabilities_layers(xml_bytes: bytes) -> list[str]:
    """Names of all layers that can be requested, in document order."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise WmsError(f"GetCapabilities is not XML: {exc}") from exc
    names = []
    for el in root.iter():
        if _local(el.tag) == "Layer":
            for child in el:
                if _local(child.tag) == "Name" and child.text:
                    names.append(child.text.strip())
    return names


def choose_layer(source: ImagerySource, available: list[str]) -> str:
    if source.layer and source.layer in available:
        return source.layer
    # Skip metadata/greyscale layers when guessing.
    for name in available:
        low = name.lower()
        if not any(word in low for word in ("meta", "grau", "grey", "gray", "info")):
            return name
    if available:
        return available[0]
    raise WmsError("the service lists no layers")


def get_capabilities(source: ImagerySource) -> list[str]:
    fixtures = _fixtures()
    if fixtures is not None:
        return capabilities_layers((fixtures / "wms_capabilities.xml").read_bytes())
    data, _ = _http_get(
        _with_query(
            source.url, {"SERVICE": "WMS", "REQUEST": "GetCapabilities", "VERSION": "1.1.1"}
        )
    )
    return capabilities_layers(data)


def _get_map(source: ImagerySource, layer: str, b: Bounds, w: int, h: int) -> Image.Image:
    fixtures = _fixtures()
    if fixtures is not None:
        return _fixture_map(fixtures, b, w, h)
    common = {
        "SERVICE": "WMS",
        "REQUEST": "GetMap",
        "LAYERS": layer,
        "STYLES": "",
        "FORMAT": "image/jpeg",
        "WIDTH": str(w),
        "HEIGHT": str(h),
    }
    attempts = [
        {
            **common,
            "VERSION": "1.1.1",
            "SRS": "EPSG:4326",
            "BBOX": f"{b.west},{b.south},{b.east},{b.north}",
        },
        # WMS 1.3.0 uses lat/lon axis order for EPSG:4326.
        {
            **common,
            "VERSION": "1.3.0",
            "CRS": "EPSG:4326",
            "BBOX": f"{b.south},{b.west},{b.north},{b.east}",
        },
    ]
    last = ""
    for params in attempts:
        data, ctype = _http_get(_with_query(source.url, params), timeout=120)
        if ctype.startswith("image/"):
            return Image.open(io.BytesIO(data)).convert("RGB")
        last = data[:300].decode("utf-8", "replace")
    raise WmsError(f"the service returned no image: {last}")


def _fixture_map(fixtures: Path, b: Bounds, w: int, h: int) -> Image.Image:
    img = Image.open(fixtures / "wms_image.png").convert("RGB")
    ext = json.loads((fixtures / "wms_image.json").read_text())
    sx = img.width / (ext["east"] - ext["west"])
    sy = img.height / (ext["north"] - ext["south"])
    box = (
        (b.west - ext["west"]) * sx,
        (ext["north"] - b.north) * sy,
        (b.east - ext["west"]) * sx,
        (ext["north"] - b.south) * sy,
    )
    return img.transform(
        (w, h), Image.Transform.EXTENT, box, Image.Resampling.BILINEAR, fillcolor=(40, 40, 40)
    )


def _wms_canvas(
    source: ImagerySource, layer: str, b: Bounds, progress=None
) -> tuple[Image.Image, float]:
    w_m, h_m = b.size_m()
    gsd = max(source.max_gsd_m, max(w_m, h_m) / MAX_SIDE_PX)
    width, height = max(1, round(w_m / gsd)), max(1, round(h_m / gsd))

    canvas = Image.new("RGB", (width, height))
    nx, ny = math.ceil(width / TILE_PX), math.ceil(height / TILE_PX)
    dlon = (b.east - b.west) / width
    dlat = (b.north - b.south) / height
    for j in range(ny):
        for i in range(nx):
            x0, y0 = i * TILE_PX, j * TILE_PX
            tw, th = min(TILE_PX, width - x0), min(TILE_PX, height - y0)
            tile_b = Bounds(
                b.west + x0 * dlon,
                b.north - (y0 + th) * dlat,
                b.west + (x0 + tw) * dlon,
                b.north - y0 * dlat,
            )
            canvas.paste(_get_map(source, layer, tile_b, tw, th), (x0, y0))
            if progress:
                progress((j * nx + i + 1) / (nx * ny))
    return canvas, gsd


def fetch_area_image(
    source: ImagerySource, area: Bounds, out_dir: Path, progress=None
) -> ImageryInfo:
    b = area.grow_m(MARGIN_M)
    if source.kind == "xyz":
        from xpsb.imagery import xyz

        canvas, gsd = xyz.fetch_mosaic(source, b, progress)
        layer = "xyz"
    else:
        layer = choose_layer(source, get_capabilities(source))
        canvas, gsd = _wms_canvas(source, layer, b, progress)
    width, height = canvas.size

    out_dir.mkdir(parents=True, exist_ok=True)
    canvas.save(out_dir / "aoi.jpg", quality=90)
    info = ImageryInfo(
        source_id=source.id,
        source_name=source.name,
        layer=layer,
        attribution=source.attribution,
        license=source.license,
        west=b.west,
        south=b.south,
        east=b.east,
        north=b.north,
        width_px=width,
        height_px=height,
        gsd_m=round(gsd, 3),
    )
    (out_dir / "aoi.json").write_text(info.model_dump_json(indent=2), encoding="utf-8")
    return info


def crop(
    image: Image.Image, info: ImageryInfo, west: float, south: float, east: float, north: float
) -> Image.Image:
    """Pixel crop of the area image for a lon/lat box (clamped to the image)."""
    sx = info.width_px / (info.east - info.west)
    sy = info.height_px / (info.north - info.south)
    box = (
        max(0, int((west - info.west) * sx)),
        max(0, int((info.north - north) * sy)),
        min(info.width_px, int(math.ceil((east - info.west) * sx))),
        min(info.height_px, int(math.ceil((info.north - south) * sy))),
    )
    return image.crop(box)
