"""Place search for the map (Photon while typing, Nominatim on Enter)."""

from __future__ import annotations

import pytest

from xpsb import places

PHOTON = {
    "features": [
        {
            "geometry": {"coordinates": [11.6247, 48.2188]},
            "properties": {
                "name": "Allianz Arena",
                "osm_value": "stadium",
                "street": "Werner-Heisenberg-Allee",
                "housenumber": "25",
                "district": "Fröttmaning",
                "city": "München",
                "state": "Bayern",
                "country": "Deutschland",
                "extent": [11.6219, 48.2203, 11.6275, 48.2172],
            },
        },
        {"geometry": {"coordinates": [0, 0]}, "properties": {}},  # nameless: skipped
    ]
}
NOMINATIM = [
    {
        "name": "Allianz Arena",
        "display_name": "Allianz Arena, 25, Werner-Heisenberg-Allee, Fröttmaning, München",
        "type": "stadium",
        "lon": "11.6247",
        "lat": "48.2188",
        "boundingbox": ["48.2172", "48.2203", "11.6219", "11.6275"],
    }
]


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    monkeypatch.delenv("XPSB_FIXTURES_DIR", raising=False)
    places._CACHE.clear()


def test_typing_asks_photon_and_names_the_city(monkeypatch):
    urls = []

    def get(url):
        urls.append(url)
        return PHOTON

    monkeypatch.setattr(places, "_get_json", get)
    found = places.search("allianz arena", "de", near=(11.57, 48.14))
    assert len(found) == 1
    hit = found[0]
    assert hit.name == "Allianz Arena" and "München" in hit.detail and hit.kind == "stadium"
    assert hit.bbox == (11.6219, 48.2172, 11.6275, 48.2203)  # west, south, east, north
    assert "photon.komoot.io" in urls[0] and "lat=48.1400" in urls[0] and "lang=de" in urls[0]
    places.search("allianz  arena", "de", near=(11.57, 48.14))
    assert len(urls) == 1  # answered from the cache


def test_nominatim_only_on_enter_when_photon_fails(monkeypatch):
    def get(url):
        if "photon" in url:
            raise places.PlaceError("photon down")
        return NOMINATIM

    monkeypatch.setattr(places, "_get_json", get)
    with pytest.raises(places.PlaceError):
        places.search("allianz arena", "de")  # typing: no Nominatim (its usage policy)
    found = places.search("allianz arena", "de", final=True)
    assert found[0].name == "Allianz Arena" and found[0].bbox == (
        11.6219,
        48.2172,
        11.6275,
        48.2203,
    )


def test_short_queries_ask_nobody(monkeypatch):
    monkeypatch.setattr(places, "_get_json", lambda url: pytest.fail("no request"))
    assert places.search(" a ") == []
