"""Bridges: found in OSM with the roads leading onto them, built per structure with
approach ramps, and the real bridge painted out of the aerial image below."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw
from shapely.geometry import shape
from test_osm_sites import LAT0, LON0, aoi, ll, rect

from xpsb.build import assemble
from xpsb.geo import METERS_PER_DEG_LAT, meters_per_deg_lon
from xpsb.imagery import retouch
from xpsb.osm import classify, overpass

WANT = {"bridges", "buildings"}


def _line(i, pts, **tags):
    return {"type": "way", "id": i, "geometry": [ll(x, y) for x, y in pts], "tags": tags}


def _run(elements):
    return classify.classify(elements, aoi(-400, -400, 400, 400), WANT, set())


def _bridges(items):
    return [it for it in items if str(it.category) == "bridges"]


def _river_crossing(**tags):
    return [
        _line(10, [(-60, 0), (60, 0)], bridge="yes", layer="1", **tags),
        _line(11, [(-60, 0), (-140, 0), (-200, 40)], highway="primary"),
        _line(12, [(200, 0), (60, 0)], highway="primary"),  # drawn towards the bridge
        _line(13, [(0, -80), (0, 80)], highway="residential"),  # passes underneath
    ]


def test_a_bridge_way_becomes_a_bridge_with_its_approaches():
    items = _run(_river_crossing(highway="primary", lanes="4", name="Erzsébet híd"))
    (b,) = _bridges(items)
    assert b.name == "Erzsébet híd"
    assert b.geometry["type"] == "LineString"
    assert b.attrs.width_m == pytest.approx(4 * 3.3 + 2.0)
    assert b.attrs.height_m is None and b.attrs.bridge_structure == "beam"  # not mapped
    assert len(b.approaches) == 2  # not the road underneath
    for ap in b.approaches:  # each starts at the bridge and leads away from it
        start = ap["coordinates"][0]
        assert min(abs(start[0] - ll(x, 0)["lon"]) for x in (-60, 60)) < 1e-6


def test_structure_material_and_outline_come_from_osm():
    els = _river_crossing(highway="secondary", **{"bridge:structure": "suspension"})
    els.append(
        {
            "type": "way",
            "id": 20,
            "geometry": rect(-60, -9, 60, 9),
            "tags": {"man_made": "bridge", "name": "Kettenbrücke", "wikidata": "Q1"},
        }
    )
    (b,) = _bridges(_run(els))
    assert b.name == "Kettenbrücke" and b.tags["wikidata"] == "Q1"
    assert b.attrs.bridge_structure == "suspension"
    assert b.attrs.width_m == pytest.approx(18, abs=0.5)  # from the outline
    stone = _bridges(_run(_river_crossing(highway="footway", **{"bridge:material": "stone"})))
    assert stone[0].attrs.bridge_structure == "arch" and stone[0].attrs.material == "stone"


def test_bridge_ways_that_continue_each_other_are_one_bridge():
    els = [
        _line(1, [(-80, 0), (0, 0)], highway="primary", bridge="yes", layer="1"),
        _line(2, [(0, 0), (80, 0)], highway="primary", bridge="yes", layer="1"),
        _line(3, [(-50, 50), (-44, 50)], highway="footway", bridge="yes"),  # a culvert
    ]
    (b,) = _bridges(_run(els))
    assert shape(b.geometry).length * meters_per_deg_lon(LAT0) == pytest.approx(160, rel=0.02)


@pytest.mark.parametrize("structure", ["beam", "arch", "truss", "suspension", "cable-stayed"])
def test_every_structure_builds_with_ramps(structure):
    items = _run(_river_crossing(highway="primary", **{"bridge:structure": structure}))
    (b,) = _bridges(items)
    mesh = assemble.generate(b, items, "medium")[0]
    mats = {p.material for p in mesh.patches}
    assert "asphalt" in mats
    xs = [p[0] for patch in mesh.patches for p in patch.positions]
    assert max(xs) - min(xs) > 180  # the ramps reach beyond the 120 m bridge
    without = b.model_copy(update={"approaches": []})
    mesh2 = assemble.generate(without, items, "medium")[0]
    xs2 = [p[0] for patch in mesh2.patches for p in patch.positions]
    assert max(xs2) - min(xs2) < 135


def test_overpass_asks_for_bridges_and_the_roads_onto_them():
    q = overpass.build_query([(19.0, 47.0), (19.01, 47.0), (19.01, 47.01), (19.0, 47.0)])
    assert 'man_made"="bridge"' in q and "node(w.br)" in q and "way(bn)" in q


def _image():
    size = 400  # 200 x 200 m, 0.5 m pixels: dark water, a light bridge across it
    img = Image.new("RGB", (size, size), (30, 50, 60))
    draw = ImageDraw.Draw(img)
    draw.rectangle([60, 192, 340, 208], fill=(200, 200, 200))
    bounds = (
        LON0 - 100 / meters_per_deg_lon(LAT0),
        LAT0 - 100 / METERS_PER_DEG_LAT,
        LON0 + 100 / meters_per_deg_lon(LAT0),
        LAT0 + 100 / METERS_PER_DEG_LAT,
    )
    return img, bounds


def test_the_real_bridge_is_painted_out_of_the_aerial_image():
    items = _run(_river_crossing(highway="primary"))
    (b,) = _bridges(items)
    assert retouch.hidden(b)
    house = (
        next(it for it in items if str(it.category) == "buildings")
        if any(str(it.category) == "buildings" for it in items)
        else None
    )
    assert house is None or not retouch.hidden(house)
    img, bounds = _image()
    out = np.asarray(retouch.apply(img, bounds, retouch.areas(items))).astype(int)
    before = np.asarray(img).astype(int)
    assert out[200, 200].sum() < 250  # water now, not the bridge
    assert (out[20, 20] == before[20, 20]).all()  # far away: untouched
    b.attrs.hide_below = False
    assert retouch.areas(items) == []


def test_from_far_away_only_the_big_parts_are_drawn():
    import re

    from xpsb.build.bake import bake
    from xpsb.build.objexport import to_obj8

    items = _run(_river_crossing(highway="primary", **{"bridge:structure": "suspension"}))
    (b,) = _bridges(items)
    mesh = assemble.generate(b, items, "medium")[0]
    lod = assemble._lod(mesh)
    near = assemble._near(mesh, lod)
    text = to_obj8(
        mesh, bake(mesh, 1024), "t.png", lod, None, None, near, assemble._distant(mesh, lod, near)
    ).to_text()
    lods = re.findall(r"ATTR_LOD (\d+) (\d+)", text)
    assert len(lods) == 2 and lods[0][0] == "0" and lods[0][1] == lods[1][0]
    counts = [int(c) for c in re.findall(r"TRIS \d+ (\d+)", text)]
    assert counts[-1] < 0.4 * counts[0]  # cables, hangers, railings and lamps are gone


DSF_TEXT = """PATCH_BEGIN 0 0 -1 1 5
BEGIN_PRIMITIVE 0
PATCH_VERTEX 19.0 47.0 100.0 0 0
PATCH_VERTEX 19.2 47.0 120.0 0 0
PATCH_VERTEX 19.0 47.2 100.0 0 0
END_PRIMITIVE
BEGIN_PRIMITIVE 1
PATCH_VERTEX 50.0 50.0 1 0 0
PATCH_VERTEX 50.1 50.0 1 0 0
PATCH_VERTEX 50.0 50.1 1 0 0
END_PRIMITIVE
PATCH_END
"""


def test_terrain_heights_come_from_the_dsf_mesh():
    from xpsb.xplane import terrain

    tris = terrain.triangles_from_text(DSF_TEXT.splitlines(), (18.9, 46.9, 19.3, 47.3))
    assert tris.shape == (1, 3, 3)  # the far-away strip is left out
    ground = terrain.Terrain(tris)
    assert ground.height(19.1, 47.05) == pytest.approx(110.0)
    assert ground.height(25.0, 47.0) is None


def test_the_deck_meets_the_banks_of_a_valley():
    from xpsb.osm import bridges

    items = _run(_river_crossing(highway="primary"))
    (b,) = _bridges(items)
    # A valley: the banks 14 m above the river, X-Plane's ground in metres above sea.
    lon0 = ll(0, 0)["lon"]
    mx = meters_per_deg_lon(LAT0)

    def height(lon, lat):
        x = (lon - lon0) * mx
        return 300.0 + min(14.0, max(0.0, abs(x) - 30) * 0.5)

    b.ground = bridges.sample_ground(b, height)
    mesh = assemble.generate(b, items, "medium")[0]
    params = assemble._script_inputs(b, items, "medium").params["bridge"]
    deck = params["deck"]
    assert deck[0][2] == pytest.approx(14.3, abs=0.6) and deck[-1][2] == pytest.approx(
        14.3, abs=0.6
    )
    assert min(p[2] for p in deck) >= 14.0  # high enough over the river
    (x0, y0, z0), (x1, y1, z1) = mesh.bounds()
    assert z0 < -4  # piers reach into the riverbed
    flat = b.model_copy(update={"ground": []})
    deck_flat = assemble._script_inputs(flat, items, "medium").params["bridge"]["deck"]
    assert {p[2] for p in deck_flat} == {6.0}  # no terrain: the guessed height
