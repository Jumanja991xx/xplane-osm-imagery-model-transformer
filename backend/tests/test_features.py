"""Water in courtyards (a thermal bath's pools, a palace fountain): OSM maps it apart
from the building; the building gets it and its script builds it."""

from __future__ import annotations

from test_osm_sites import WANTED_OBJ, WANTED_SURF, aoi, rect, way

from xpsb.build import assemble
from xpsb.build.sandbox import run_script
from xpsb.build.scripts import DEFAULT_SCRIPTS
from xpsb.osm import classify


def bath() -> list[dict]:
    outer = rect(-60, -40, 60, 40)
    inner = list(reversed(rect(-40, -20, 40, 20)))
    return [
        {
            "type": "relation",
            "id": 1,
            "tags": {"type": "multipolygon", "building": "yes", "amenity": "public_bath"},
            "members": [
                {"type": "way", "ref": 10, "role": "outer", "geometry": outer},
                {"type": "way", "ref": 11, "role": "inner", "geometry": inner},
            ],
        },
        way(20, rect(-25, -8, 25, 8), leisure="swimming_pool", location="outdoor"),
        way(21, rect(-38, -5, -30, 5), amenity="fountain"),
        way(22, rect(100, 100, 140, 140), leisure="swimming_pool"),  # not in a courtyard
        way(23, rect(-35, 10, -28, 16), leisure="swimming_pool", location="indoor"),
    ]


def test_pools_and_fountains_in_the_courtyard_belong_to_the_building():
    items = classify.classify(bath(), aoi(), WANTED_OBJ, WANTED_SURF)
    building = next(i for i in items if i.id == "relation/1")
    assert sorted(f["kind"] for f in building.features) == ["fountain", "pool"]
    assert classify.feature_kind({"natural": "water", "water": "river"}) is None
    assert classify.feature_kind({"natural": "water", "water": "pond"}) == "water"


def test_the_default_script_builds_the_water():
    items = classify.classify(bath(), aoi(), WANTED_OBJ, WANTED_SURF)
    building = next(i for i in items if i.id == "relation/1")
    inputs = assemble._script_inputs(building, items, "medium")
    pool = next(f for f in inputs.params["features"] if f["kind"] == "pool")
    xs = [p[0] for p in pool["footprint"]]
    assert round(min(xs)) == -25 and round(max(xs)) == 25  # local metres
    mesh = run_script(DEFAULT_SCRIPTS["buildings"], inputs, 60_000)
    assert {"pools", "fountains"} <= {p.id for p in mesh.parts}


def test_animals_and_abstract_works_are_not_built_as_people():
    art = {"tourism": "artwork", "artwork_type": "sculpture"}
    kind = classify.monument_kind
    assert kind({**art, "name": "Tyrannosaurus rex"}) == "sculpture"
    assert kind({**art, "name": "Dodó"}) == "sculpture"
    assert kind({**art, "name": "Lions"}) == "sculpture"
    assert kind({**art, "site:type": "zoo"}) == "sculpture"
    # names that only contain an animal stay statues of people
    assert kind({**art, "name": "Wolfgang Amadeus Mozart"}) == "statue"
    assert kind({**art, "name": "Zsigmondy Vilmos"}) == "statue"


def test_a_sculpture_has_no_figure():
    from xpsb.build.sandbox import ScriptInputs

    inputs = ScriptInputs(
        None, None, {"monument": {"kind": "sculpture", "material": "bronze"}, "height": 4.0}
    )
    mesh = run_script(DEFAULT_SCRIPTS["monuments"], inputs, 20_000)
    assert mesh.triangle_count > 0
    assert not any(p.tool == "figure" for p in mesh.parts)


def test_research_says_when_no_source_was_reachable(monkeypatch):
    from xpsb import photos, research
    from xpsb.scene import SceneItem

    def blocked(*_a, **_k):
        raise photos.PhotoError("www.wikidata.org: Tunnel connection failed: 403")

    for name in ("fetch_entity", "wikipedia_summary", "find_by_name"):
        monkeypatch.setattr(research, name, blocked)
    monkeypatch.setattr(research.photos, "search", blocked)
    item = SceneItem(
        id="way/1",
        kind="object",
        category="buildings",
        geometry={"type": "Point", "coordinates": [19.08, 47.51]},
        source="osm",
        name="Széchenyi",
        tags={"wikidata": "Q194783", "wikipedia": "hu:Széchenyi gyógyfürdő"},
    )
    found = research.research_item(item, "de", None)
    assert found.unreachable and "403" in found.errors[0]
    # reachable but nothing there: not "unreachable"
    monkeypatch.setattr(research, "fetch_entity", lambda q: None)
    monkeypatch.setattr(research, "wikipedia_summary", lambda *a: None)
    monkeypatch.setattr(research.photos, "search", lambda *a, **k: None)
    assert not research.research_item(item, "de", None).unreachable


def test_osm_colour_names_and_narrow_mansards_do_not_break_a_building():
    from xpsb.build.materials import parse_color
    from xpsb.build.sandbox import ScriptInputs

    assert parse_color("lightblue") == (173, 216, 230)
    assert parse_color("Light Blue") == (173, 216, 230)
    # a sliver too narrow for a mansard band still gets a roof
    sliver = [[0, 0], [30, 0], [30, 0.6], [0, 0.6]]
    mesh = run_script(
        'mansard(footprint, 4, rise=2, depth=3, color="lightblue")',
        ScriptInputs(sliver, None, {}),
        5_000,
    )
    assert mesh.triangle_count > 0


def test_domes_are_round_over_any_outline():
    import math

    from xpsb.build.sandbox import ScriptInputs

    # a round hall drawn with many points (OSM: 109 for Budapest's Biodome)
    circle = [
        [40 * math.cos(i * math.pi / 54), 40 * math.sin(i * math.pi / 54)] for i in range(108)
    ]
    params = {"roof_shape": "dome", "height": 12, "building": "yes"}
    mesh = run_script(DEFAULT_SCRIPTS["buildings"], ScriptInputs(circle, None, params), 60_000)
    assert "dome" in {p.id for p in mesh.parts}
    (_, _, _), (_, _, z1) = mesh.bounds()
    assert z1 > 12 + 15  # the dome rises well above the walls
