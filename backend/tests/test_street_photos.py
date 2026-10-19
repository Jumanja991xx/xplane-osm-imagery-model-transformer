"""Street-level photos (Mapillary, Panoramax) as the open counterpart of Street View:
photos looking at the object first, of a 360° panorama only the view towards it."""

from __future__ import annotations

import io
import json

from PIL import Image

from xpsb import photos
from xpsb.geo import METERS_PER_DEG_LAT, meters_per_deg_lon

LON, LAT = 19.0990, 47.5030  # the object


def at(x: float, y: float) -> list[float]:
    return [LON + x / meters_per_deg_lon(LAT), LAT + y / METERS_PER_DEG_LAT]


def mapillary_answer(*images) -> bytes:
    return json.dumps({"data": list(images)}).encode()


def image(i: str, pos: list[float], compass: float, pano: bool = False) -> dict:
    return {
        "id": i,
        "thumb_256_url": f"https://cdn.example/{i}-256.jpg",
        "thumb_1024_url": f"https://cdn.example/{i}-1024.jpg",
        "thumb_2048_url": f"https://cdn.example/{i}-2048.jpg",
        "computed_geometry": {"type": "Point", "coordinates": pos},
        "computed_compass_angle": compass,
        "is_pano": pano,
    }


def test_photos_looking_at_the_object_come_first(monkeypatch):
    answer = mapillary_answer(
        image("away", at(0, -10), 180.0),  # 10 m south, looking south: away
        image("looks", at(0, -40), 5.0),  # 40 m south, looking north: at the object
        image("pano", at(30, 0), 0.0, pano=True),  # 360°: always usable
    )
    monkeypatch.setattr(photos, "http_get", lambda url, timeout=30: answer)
    found = photos.search_mapillary(LON, LAT, 250, "TOKEN")
    by_id = {c.page_url.rsplit("=", 1)[1]: c for c in found}
    assert by_id["away"].facing is False and by_id["looks"].facing is True
    assert by_id["pano"].facing is True and by_id["pano"].image_url.endswith("-2048.jpg")
    assert abs(by_id["looks"].bearing - 0.0) < 1 and abs(by_id["pano"].bearing - 270.0) < 1

    monkeypatch.setattr(photos, "search_wikimedia", lambda *a: [])
    monkeypatch.setattr(photos, "search_panoramax", lambda *a: [])
    monkeypatch.setattr(photos, "search_mapillary", lambda *a: found)
    order = [c.page_url.rsplit("=", 1)[1] for c in photos.search(LON, LAT, "TOKEN").candidates]
    assert order == ["pano", "looks", "away"]  # nearest useful first, looking away last


def test_panorama_is_cut_towards_the_object(monkeypatch):
    # Equirectangular test panorama: the object (to the west) is painted red.
    pano = Image.new("RGB", (800, 400), (90, 90, 90))
    # camera looks north (centre column x=400); west = -90° -> x = 400 - 200 = 200
    pano.paste((220, 30, 30), (170, 100, 230, 300))
    buf = io.BytesIO()
    pano.save(buf, format="JPEG")
    view = Image.open(io.BytesIO(photos.view_towards(buf.getvalue(), 0.0, 270.0)))
    assert view.size == (200, 200)
    r, g, b = view.getpixel((100, 100))
    assert r > 150 and g < 90  # the object is in the middle of the view
    # Across the seam (object straight behind the camera) works as well.
    behind = Image.open(io.BytesIO(photos.view_towards(buf.getvalue(), 0.0, 180.0)))
    assert behind.size == (200, 200)

    c = photos.PhotoCandidate(
        id="x",
        source="mapillary",
        thumb_url="t",
        image_url="i",
        heading=0.0,
        pano=True,
        bearing=270.0,
    )
    monkeypatch.setattr(photos, "http_get", lambda url, timeout=30: buf.getvalue())
    assert Image.open(io.BytesIO(photos.download(c))).size == (200, 200)
    c.pano = False  # ordinary photos stay as they are
    assert Image.open(io.BytesIO(photos.download(c))).size == (800, 400)


def test_panoramax_direction_and_panoramas(monkeypatch):
    feature = {
        "id": "p1",
        "geometry": {"type": "Point", "coordinates": at(0, 30)},
        "properties": {
            "datetime": "2025-06-01T10:00:00Z",
            "view:azimuth": 180,
            "pers:interior_orientation": {"field_of_view": 360},
        },
        "assets": {"thumb": {"href": "t"}, "sd": {"href": "s"}},
    }
    monkeypatch.setattr(
        photos, "http_get", lambda url, timeout=30: json.dumps({"features": [feature]}).encode()
    )
    (c,) = photos.search_panoramax(LON, LAT, 250)
    assert c.pano and c.facing is True and abs(c.bearing - 180.0) < 1
