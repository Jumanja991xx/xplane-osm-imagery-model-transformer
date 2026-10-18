"""Generates the offline test data for a small speedway stadium.

    uv run python tests/fixtures/make_speedway.py

Writes tests/fixtures/speedway/{overpass.json, wms_capabilities.xml, wms_image.png}.
The image is a synthetic "aerial photo" drawn from the same shapes, so imagery,
OSM outlines and AI crops line up exactly like real data would.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).parent / "speedway"
# Same area as the backend tests (Hockenheimring infield), ~400 m x 300 m.
W, S, E, N = 8.5630, 49.3265, 8.5685, 49.3292
CX, CY = (W + E) / 2, (S + N) / 2
MX = 111_320 * math.cos(math.radians(CY))
MY = 111_320


def pt(dx: float, dy: float) -> dict:
    return {"lat": round(CY + dy / MY, 7), "lon": round(CX + dx / MX, 7)}


def rect(x0, y0, x1, y1):
    return [pt(x0, y0), pt(x1, y0), pt(x1, y1), pt(x0, y1), pt(x0, y0)]


def oval(rx, ry, n=48):
    pts = [
        pt(rx * math.cos(2 * math.pi * i / n), ry * math.sin(2 * math.pi * i / n)) for i in range(n)
    ]
    return pts + [pts[0]]


# South grandstand that exists in the image but not in the OSM data (x0, y0, x1, y1 in m).
UNMAPPED_GRANDSTAND_M = (-60, -110, 60, -92)


def way(i, geometry, **tags):
    return {"type": "way", "id": i, "geometry": geometry, "tags": tags}


UNMAPPED_GRANDSTAND = rect(*UNMAPPED_GRANDSTAND_M)

elements = [
    way(101, oval(70, 40), leisure="track", sport="speedway", name="Speedwaybahn"),
    way(102, oval(50, 22), leisure="pitch", sport="soccer"),
    way(103, rect(-60, 50, 60, 68), building="grandstand", height="9", name="Haupttribüne"),
    way(104, rect(-110, -70, -80, -40), building="yes", **{"building:levels": "2"}),
    way(105, rect(80, -70, 130, -45), building="industrial", name="Fahrerlager-Halle"),
    way(106, rect(-30, -75, 30, -60), building="roof", name="Boxendach"),
    way(107, [pt(-90, -50), pt(-90, 75), pt(95, 75), pt(95, -50)], barrier="fence"),
    way(108, rect(-150, 80, -100, 120), amenity="parking"),
    {
        "type": "node",
        "id": 201,
        "lat": pt(90, 60)["lat"],
        "lon": pt(90, 60)["lon"],
        "tags": {"man_made": "mast", "tower:type": "lighting", "height": "25"},
    },
    {
        "type": "relation",
        "id": 301,
        "tags": {"type": "multipolygon", "building": "yes", "name": "Clubhaus"},
        "members": [
            {"type": "way", "ref": 1, "role": "outer", "geometry": rect(110, 40, 160, 80)},
            {"type": "way", "ref": 2, "role": "inner", "geometry": rect(125, 52, 145, 68)},
        ],
    },
    # Centre outside the area: must be ignored.
    way(109, rect(400, 400, 430, 430), building="yes"),
    # Not in the catalog: must be ignored.
    way(110, rect(-20, 90, 0, 100), natural="scrub"),
]

overpass = {"version": 0.6, "generator": "xpsb fixture", "elements": elements}

CAPABILITIES = """<?xml version="1.0" encoding="UTF-8"?>
<WMT_MS_Capabilities version="1.1.1">
  <Service><Name>OGC:WMS</Name><Title>Fixture DOP</Title></Service>
  <Capability>
    <Layer>
      <Title>DOP40</Title>
      <SRS>EPSG:4326</SRS>
      <Layer queryable="0"><Name>by_dop40c</Name><Title>DOP40 Farbe</Title></Layer>
      <Layer queryable="0"><Name>by_dop40g</Name><Title>DOP40 Graustufen</Title></Layer>
      <Layer queryable="1"><Name>metadaten</Name><Title>Befliegungsdatum</Title></Layer>
    </Layer>
  </Capability>
</WMT_MS_Capabilities>
"""


def draw_image(path: Path, px_per_m: float = 2.5) -> None:
    margin_m = 60
    w_m = (E - W) * MX + 2 * margin_m
    h_m = (N - S) * MY + 2 * margin_m
    img = Image.new("RGB", (int(w_m * px_per_m), int(h_m * px_per_m)), (86, 122, 64))
    d = ImageDraw.Draw(img)

    def xy(p):
        x = ((p["lon"] - W) * MX + margin_m) * px_per_m
        y = ((N - p["lat"]) * MY + margin_m) * px_per_m
        return (x, y)

    d.polygon([xy(p) for p in oval(70, 40)], fill=(170, 140, 100))
    d.polygon([xy(p) for p in oval(50, 22)], fill=(70, 140, 60))
    d.polygon([xy(p) for p in rect(-150, 80, -100, 120)], fill=(95, 95, 98))
    roofs = (
        (rect(-60, 50, 60, 68), (200, 60, 50)),
        (rect(-110, -70, -80, -40), (150, 150, 155)),
        (rect(80, -70, 130, -45), (120, 125, 135)),
        (rect(-30, -75, 30, -60), (190, 190, 195)),
        (rect(110, 40, 160, 80), (160, 80, 60)),
        # Not in OSM: only the AI object search can find it.
        (UNMAPPED_GRANDSTAND, (60, 90, 170)),
    )
    for g, c in roofs:
        d.polygon([xy(p) for p in g], fill=c)
    d.polygon([xy(p) for p in rect(125, 52, 145, 68)], fill=(86, 122, 64))
    img.save(path)
    (path.parent / "wms_image.json").write_text(
        json.dumps(
            {
                "west": W - margin_m / MX,
                "east": E + margin_m / MX,
                "south": S - margin_m / MY,
                "north": N + margin_m / MY,
            }
        )
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "overpass.json").write_text(
        json.dumps(overpass, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    (OUT / "wms_capabilities.xml").write_text(CAPABILITIES, encoding="utf-8")
    draw_image(OUT / "wms_image.png")
    print(f"fixtures written to {OUT}")


if __name__ == "__main__":
    main()
