"""User-supplied XYZ tile sources (Web Mercator) end up on the same lon/lat grid."""

from __future__ import annotations

from conftest import AUTH
from fake_ai import speedway
from PIL import Image
from test_analysis import ALL, CUSTOM_IMAGERY, analyze, setup

from xpsb.imagery import wms, xyz
from xpsb.imagery.sources import custom_source

XYZ_IMAGERY = {
    **CUSTOM_IMAGERY,
    "custom_type": "xyz",
    "custom_url": "https://tiles.example.test/{z}/{x}/{y}.jpg",
    "custom_max_zoom": 19,
}


def test_tile_url_placeholders():
    assert xyz.tile_url("https://t/{z}/{x}/{y}.png", 3, 1, 2) == "https://t/3/1/2.png"
    assert xyz.tile_url("https://t/{z}/{x}/{-y}.png", 3, 1, 2) == "https://t/3/1/5.png"


def test_zoom_respects_tile_limit():
    source = custom_source("https://t/{z}/{x}/{y}", None, "", "xyz", 21)
    small = xyz.choose_zoom(source, 49.3, 400, 300)
    large = xyz.choose_zoom(source, 49.3, 5000, 5000)
    assert 19 <= small <= 21 and large < small


def test_xyz_mosaic_lines_up_with_the_map(tmp_path):
    source = custom_source("https://t/{z}/{x}/{y}", None, "Test", "xyz", 18)
    info = wms.fetch_area_image(source, wms.Bounds(8.5630, 49.3265, 8.5685, 49.3292), tmp_path)
    assert info.layer == "xyz" and 0.3 < info.gsd_m < 0.5
    with Image.open(tmp_path / "aoi.jpg") as img:
        x0, y0, x1, y1 = speedway.UNMAPPED_GRANDSTAND_M
        c = speedway.pt((x0 + x1) / 2, (y0 + y1) / 2)
        px = (c["lon"] - info.west) / (info.east - info.west) * info.width_px
        py = (info.north - c["lat"]) / (info.north - info.south) * info.height_px
        r, g, b = img.getpixel((int(px), int(py)))
    assert b > 140 and r < 100  # the blue grandstand of the fixture image


def test_xyz_needs_placeholders_and_rights(client):
    job = setup(
        client,
        imagery={**XYZ_IMAGERY, "custom_url": "https://tiles.example.test/a.jpg"},
        brief={**ALL, "use_ai": False},
    )
    keys = {i["key"] for i in job["pending_gate"]["items"]}
    assert "gate.a.imagery.custom_missing" in keys

    job = setup(
        client,
        imagery={**XYZ_IMAGERY, "custom_rights_confirmed": False},
        brief={**ALL, "use_ai": False},
    )
    assert job["pending_gate"]["blocked"] is True

    job = analyze(client, setup(client, imagery=XYZ_IMAGERY, brief={**ALL, "use_ai": False}))
    assert job["analysis"]["imagery"]["layer"] == "xyz"
    assert job["analysis"]["imagery"]["source_name"] == "Eigene XYZ-Quelle"
    assert client.get(f"/api/jobs/{job['id']}/imagery", headers=AUTH).status_code == 200
