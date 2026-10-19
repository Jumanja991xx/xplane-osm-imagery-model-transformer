"""OSM details that decide whether a landmark is recognised at all: stadium grounds
without a building, building parts, type=building relations, big buildings at the
edge of the drawn area."""

from __future__ import annotations

from shapely.geometry import Polygon, shape

from xpsb.build import assemble
from xpsb.geo import METERS_PER_DEG_LAT, meters_per_deg_lon
from xpsb.osm import classify

LON0, LAT0 = 19.0990, 47.5030
WANTED_OBJ = {"buildings", "grandstands", "canopies", "barriers", "masts"}
WANTED_SURF = {"grass", "asphalt", "dirt", "tartan", "concrete"}


def ll(x: float, y: float) -> dict:
    return {
        "lon": LON0 + x / meters_per_deg_lon(LAT0),
        "lat": LAT0 + y / METERS_PER_DEG_LAT,
    }


def rect(x0, y0, x1, y1) -> list[dict]:
    return [ll(x0, y0), ll(x1, y0), ll(x1, y1), ll(x0, y1), ll(x0, y0)]


def way(i: int, geom: list[dict], **tags) -> dict:
    return {"type": "way", "id": i, "geometry": geom, "tags": tags}


def aoi(x0=-300, y0=-300, x1=300, y1=300) -> Polygon:
    return Polygon([(p["lon"], p["lat"]) for p in rect(x0, y0, x1, y1)])


def run(elements, area=None):
    return classify.classify(elements, area or aoi(), WANTED_OBJ, WANTED_SURF)


def test_stadium_grounds_without_building_become_a_stadium():
    elements = [
        way(
            1,
            rect(-160, -130, 160, 130),
            leisure="stadium",
            name="Puskás Aréna",
            wikidata="Q39069592",
            capacity="67155",
            sport="soccer",
        ),
        way(2, rect(-52, -34, 52, 34), leisure="pitch", surface="grass"),
        way(3, rect(120, 100, 130, 110), building="yes"),  # a kiosk, not the stadium
    ]
    items = run(elements)
    stadium = next(i for i in items if i.id == "way/1/stands")
    assert stadium.name == "Puskás Aréna"
    assert stadium.tags["wikidata"] == "Q39069592" and stadium.tags["building"] == "stadium"
    poly = shape(stadium.geometry)
    pitch = shape(next(i for i in items if i.osm_ref == "way/2").geometry)
    assert poly.contains(pitch.centroid) and poly.area > 2 * pitch.area
    assert poly.within(Polygon([(p["lon"], p["lat"]) for p in rect(-161, -131, 161, 131)]))
    kiosk = next(i for i in items if i.osm_ref == "way/3")
    assert kiosk.tags.get("site:main") != "yes" and kiosk.name is None
    # The default script builds a bowl for it.
    params = assemble._script_inputs(stadium, items, "low").params
    assert params["building"] == "stadium"


def test_main_building_of_stadium_grounds_gets_name_and_bowl():
    ring = rect(-120, -100, 120, 100)
    elements = [
        way(1, rect(-160, -130, 160, 130), leisure="stadium", name="Arena", wikidata="Q1"),
        way(2, rect(-52, -34, 52, 34), leisure="pitch"),
        {
            "type": "relation",
            "id": 9,
            "tags": {"type": "multipolygon", "building": "yes"},
            "members": [
                {"type": "way", "ref": 10, "role": "outer", "geometry": ring},
                {"type": "way", "ref": 11, "role": "inner", "geometry": rect(-60, -40, 60, 40)},
            ],
        },
    ]
    items = run(elements)
    bowl = next(i for i in items if i.osm_ref == "relation/9")
    assert bowl.name == "Arena" and bowl.tags["wikidata"] == "Q1"
    assert not any(i.id.endswith("/stands") for i in items)
    assert assemble._script_inputs(bowl, items, "low").params["building"] == "stadium"


def test_building_parts_are_folded_into_their_outline():
    elements = [
        way(1, rect(0, 0, 40, 20), building="church", name="St. Test", height="15"),
        way(
            2,
            rect(0, 0, 30, 20),
            **{"building:part": "yes", "height": "15", "roof:shape": "gabled"},
        ),
        way(
            3,
            rect(30, 0, 40, 20),
            **{"building:part": "tower", "height": "45", "roof:shape": "pyramidal"},
        ),
        way(
            4,
            rect(100, 100, 110, 110),
            **{"building:part": "yes", "height": "8", "min_height": "5"},
        ),  # no outline: own object
    ]
    items = run(elements)
    refs = {i.osm_ref for i in items}
    assert "way/2" not in refs and "way/3" not in refs and "way/4" in refs
    church = next(i for i in items if i.osm_ref == "way/1")
    assert len(church.parts) == 2
    params = assemble._script_inputs(church, items, "low").params
    assert {p["height"] for p in params["parts"]} == {15.0, 45.0}
    assert params["parts_cover"] >= 0.99
    loose = next(i for i in items if i.osm_ref == "way/4")
    assert loose.attrs.min_height_m == 5.0
    # Both build: tower higher than the nave, the loose roof floats.
    mesh, _, _ = assemble.generate(church, items, "low")
    assert mesh.bounds()[1][2] > 45
    mesh, _, _ = assemble.generate(loose, items, "low")
    assert mesh.bounds()[0][2] >= 4.9


def test_type_building_relation_names_its_outline():
    elements = [
        way(5, rect(0, 0, 50, 50), building="castle"),
        {
            "type": "relation",
            "id": 7,
            "tags": {"type": "building", "name": "Altes Schloss", "wikidata": "Q17121832"},
            "members": [
                {"type": "way", "ref": 5, "role": "outline", "geometry": rect(0, 0, 50, 50)}
            ],
        },
    ]
    items = run(elements)
    assert len(items) == 1
    assert items[0].name == "Altes Schloss" and items[0].tags["wikidata"] == "Q17121832"


def test_big_building_mostly_inside_the_area_is_kept():
    # Courtyard building: its representative point may fall outside a tight area.
    elements = [way(1, rect(-100, -40, 100, 40), building="palace", name="Schloss")]
    items = run(elements, aoi(-90, -50, 120, 50))
    assert [i.osm_ref for i in items] == ["way/1"]
    items = run(elements, aoi(60, -50, 200, 50))  # only a fifth inside: not ours
    assert items == []


def test_castle_grounds_name_the_main_building():
    elements = [
        way(1, rect(-100, -100, 100, 100), historic="castle", name="Burg", wikidata="Q5"),
        way(2, rect(-40, -30, 40, 30), building="yes"),
        way(3, rect(60, 60, 70, 70), building="yes"),
    ]
    items = run(elements)
    main = next(i for i in items if i.osm_ref == "way/2")
    assert main.name == "Burg" and main.tags["historic"] == "castle"
    other = next(i for i in items if i.osm_ref == "way/3")
    assert other.name is None and other.tags["site:name"] == "Burg"


def test_every_named_area_is_a_site():
    elements = [
        way(1, rect(-150, -150, 150, 150), amenity="university", name="Uni Test", wikidata="Q9"),
        way(2, rect(-70, -50, 70, 50), building="university"),
        way(3, rect(100, 100, 120, 120), building="yes"),
        way(4, rect(-200, -200, 250, 250), leisure="nature_reserve", name="Reservat"),
    ]
    items = run(elements, aoi(-260, -260, 260, 260))
    main = next(i for i in items if i.osm_ref == "way/2")
    assert main.name == "Uni Test" and main.tags["site:type"] == "university"
    assert next(i for i in items if i.osm_ref == "way/3").tags["site:name"] == "Uni Test"
    assert classify.site_kind({"leisure": "nature_reserve", "name": "x"}) is None
    assert classify.site_kind({"building": "yes", "name": "x"}) is None


def test_all_kinds_of_ground_are_surfaces():
    cases = [
        ({"place": "square", "name": "Hősök tere"}, "concrete"),
        ({"highway": "pedestrian", "area": "yes"}, "concrete"),
        ({"leisure": "park"}, "grass"),
        ({"leisure": "playground", "surface": "sand"}, "dirt"),
        ({"landuse": "village_green"}, "grass"),
        ({"landuse": "construction"}, "dirt"),
        ({"amenity": "marketplace", "surface": "paving_stones"}, "concrete"),
        ({"man_made": "bridge", "surface": "asphalt"}, "asphalt"),
    ]
    for i, (tags, expected) in enumerate(cases):
        items = run([way(100 + i, rect(0, 0, 30, 30), **tags)])
        assert [str(x.category) for x in items] == [expected], tags
    # A closed footway is a line, not a paved area.
    assert run([way(200, rect(0, 0, 30, 30), highway="footway", surface="asphalt")]) == []


def test_grounds_enclosing_the_whole_drawn_area():
    # The user draws tightly around the bowl: the grounds have no node inside the area.
    elements = [
        way(
            1,
            rect(-200, -170, 200, 170),
            leisure="stadium",
            name="Puskás Aréna",
            wikidata="Q39069592",
            capacity="67155",
        ),
        way(2, rect(-52, -34, 52, 34), leisure="pitch", surface="grass"),
        way(3, rect(130, -10, 145, 5), building="yes", name="Torony", height="18"),
    ]
    items = run(elements, aoi(-150, -130, 150, 130))
    stands = next(i for i in items if i.id == "way/1/stands")
    assert stands.name == "Puskás Aréna" and stands.tags["building"] == "stadium"
    tower = next(i for i in items if i.osm_ref == "way/3")
    assert tower.name == "Torony" and tower.tags["site:name"] == "Puskás Aréna"
    # The same grounds twice in the answer (both parts of the query): one stadium.
    items = run(elements + elements[:1], aoi(-150, -130, 150, 130))
    assert sum(i.id.endswith("/stands") for i in items) == 1
