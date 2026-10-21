"""When Overpass does not answer: older data, then OSM's own API, then go on without."""

from __future__ import annotations

import json
import os
import time
import urllib.error

import pytest

from xpsb.osm import classify, overpass

RING = [(11.60, 48.14), (11.61, 48.14), (11.61, 48.15), (11.60, 48.15), (11.60, 48.14)]


@pytest.fixture
def down(monkeypatch):
    monkeypatch.delenv("XPSB_FIXTURES_DIR", raising=False)
    calls = []

    def fail(endpoint, body):
        calls.append(endpoint)
        raise urllib.error.URLError("down")

    monkeypatch.setattr(overpass, "_post", fail)
    monkeypatch.setattr(overpass.time, "sleep", lambda s: None)
    return calls


def test_own_server_first_then_the_mirrors(down, monkeypatch, tmp_path):
    monkeypatch.setattr(
        overpass, "osm_api_map", lambda ring: (_ for _ in ()).throw(overpass.OverpassError("no"))
    )
    with pytest.raises(overpass.OverpassError):
        overpass.fetch("q", tmp_path, retries=0, ring=RING, own_endpoint="https://my.overpass/api")
    assert down[0] == "https://my.overpass/api" and len(down) == 1 + len(overpass.ENDPOINTS)


def test_an_older_answer_for_an_enclosing_area_stands_in(down, monkeypatch, tmp_path):
    big = [(11.5, 48.1), (11.7, 48.1), (11.7, 48.2), (11.5, 48.2), (11.5, 48.1)]
    overpass._store(tmp_path, tmp_path / "old.json", {"elements": [{"type": "node", "id": 1}]}, big)
    past = time.time() - 30 * 86400
    os.utime(tmp_path / "old.json", (past, past))
    data = overpass.fetch("another query", tmp_path, retries=0, ring=RING)
    assert data["source"] == "cache" and data["age_days"] == pytest.approx(30, abs=0.1)
    assert data["elements"] == [{"type": "node", "id": 1}]


def test_osm_api_answer_becomes_overpass_style_elements(down, monkeypatch, tmp_path):
    api = [
        {"type": "node", "id": 1, "lat": 48.141, "lon": 11.601},
        {"type": "node", "id": 2, "lat": 48.141, "lon": 11.602},
        {"type": "node", "id": 3, "lat": 48.142, "lon": 11.602},
        {
            "type": "node",
            "id": 4,
            "lat": 48.142,
            "lon": 11.601,
            "tags": {"historic": "memorial", "memorial": "statue"},
        },
        {
            "type": "way",
            "id": 10,
            "nodes": [1, 2, 3, 1],
            "tags": {"building": "yes", "height": "9"},
        },
        {"type": "way", "id": 11, "nodes": [1, 2, 3, 1]},
        {
            "type": "relation",
            "id": 20,
            "tags": {"type": "multipolygon", "building": "yes"},
            "members": [{"type": "way", "ref": 11, "role": "outer"}],
        },
    ]
    monkeypatch.setattr(
        overpass,
        "osm_api_map",
        lambda ring: {"elements": overpass.from_osm_api(api), "source": "osm_api"},
    )
    data = overpass.fetch("q", tmp_path, retries=0, ring=RING)
    assert data["source"] == "osm_api"
    els = {(e["type"], e["id"]): e for e in data["elements"]}
    assert ("way", 11) not in els  # untagged ways only as relation members
    assert len(els[("way", 10)]["geometry"]) == 4
    assert els[("relation", 20)]["members"][0]["geometry"]
    from shapely.geometry import Polygon

    items = classify.classify(data["elements"], Polygon(RING), {"buildings", "monuments"}, set())
    assert {i.id for i in items} >= {"way/10", "relation/20"}
    # Stored like an Overpass answer: the next fetch is a plain cache hit.
    assert json.loads(next(tmp_path.glob("*.json")).read_text())["source"] == "osm_api"
