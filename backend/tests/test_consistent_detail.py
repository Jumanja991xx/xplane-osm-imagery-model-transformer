"""Every building gets the same care: windows on churches and raised parts, a
bigger budget for big blocks, roof colours from the aerial image."""

from __future__ import annotations

from collections import Counter

from PIL import Image, ImageDraw
from test_osm_sites import LAT0, LON0, aoi, rect, run, way

from xpsb.build import assemble
from xpsb.build.sandbox import ScriptInputs
from xpsb.geo import METERS_PER_DEG_LAT, meters_per_deg_lon
from xpsb.imagery import roofs
from xpsb.imagery.wms import ImageryInfo
from xpsb.osm import rows


def _materials(items) -> Counter:
    mesh = assemble.generate(items[0], items, "medium")[0]
    return Counter(p.material for p in mesh.patches)


def test_churches_and_their_towers_get_windows():
    items = run(
        [
            way(1, rect(0, 0, 50, 22), building="church", height="22"),
            way(2, rect(0, 0, 38, 22), **{"building:part": "yes", "height": "22"}),
            way(3, rect(38, 4, 50, 18), **{"building:part": "tower", "height": "40"}),
            way(
                4,
                rect(39, 5, 49, 17),
                **{"building:part": "tower", "min_height": "40", "height": "62"},
            ),
        ]
    )
    mats = _materials(items)
    assert mats["glass_pane"] > 0 and mats["door"] > 0


def test_raised_parts_get_windows_but_no_door():
    items = run(
        [
            way(1, rect(0, 0, 20, 20), building="yes", height="30"),
            way(
                2,
                rect(0, 0, 20, 20),
                **{"building:part": "yes", "min_height": "10", "height": "30"},
            ),
        ]
    )
    mats = _materials(items)
    assert mats["glass_pane"] > 0
    assert mats["door"] == 0


def test_big_blocks_get_a_bigger_budget():
    small = ScriptInputs(footprint=[[0, 0], [12, 0], [12, 10], [0, 10]], path=None, params={})
    big = ScriptInputs(footprint=[[0, 0], [300, 0], [300, 200], [0, 200]], path=None, params={})
    normal = assemble.DETAIL["medium"][0]
    assert assemble.triangle_budget(small, "medium") == normal
    assert assemble.triangle_budget(big, "medium") == int(normal * assemble.MAX_BUDGET_FACTOR)
    items = run(
        [way(1, rect(0, 0, 300, 200), building="apartments", **{"building:levels": "6"})],
        aoi(-400, -400, 400, 400),
    )
    assert _materials(items)["glass_pane"] > 0  # real windows, not painted ones


def _image(colors: list[tuple[tuple[float, float, float, float], tuple]]):
    """A 200x200 m aerial image (0.5 m pixels) with coloured rectangles (metres)."""
    size = 400
    img = Image.new("RGB", (size, size), (90, 110, 70))
    draw = ImageDraw.Draw(img)
    for (x0, y0, x1, y1), c in colors:
        draw.rectangle([x0 * 2 + 200, 200 - y1 * 2, x1 * 2 + 200, 200 - y0 * 2], fill=c)
    info = ImageryInfo(
        source_id="t",
        source_name="t",
        layer="t",
        attribution="t",
        license="t",
        west=LON0 - 100 / meters_per_deg_lon(LAT0),
        south=LAT0 - 100 / METERS_PER_DEG_LAT,
        east=LON0 + 100 / meters_per_deg_lon(LAT0),
        north=LAT0 + 100 / METERS_PER_DEG_LAT,
        width_px=size,
        height_px=size,
        gsd_m=0.5,
    )
    return img, info


def test_roof_colours_come_from_the_aerial_image():
    items = run(
        [
            way(1, rect(0, 0, 20, 12), building="yes"),
            way(2, rect(40, 0, 60, 12), building="yes"),
            way(3, rect(-50, 0, -30, 12), building="yes", **{"roof:colour": "#334455"}),
        ]
    )
    # a chimney's shadow on the red roof must not darken its colour
    img, info = _image(
        [
            ((0, 0, 20, 12), (170, 60, 40)),
            ((5, 5, 6, 6), (0, 0, 0)),
            ((40, 0, 60, 12), (120, 120, 125)),
        ]
    )
    assert roofs.apply(items, img, info) == 2
    colors = sorted(it.attrs.roof_color for it in items)
    assert colors == ["#334455", "#78787d", "#aa3c28"]


def test_recognition_only_images_give_no_colours():
    items = run([way(1, rect(0, 0, 20, 12), building="yes")])
    img, info = _image([((0, 0, 20, 12), (170, 60, 40))])
    info.package_ok = False
    assert roofs.apply(items, img, info) == 0
    assert items[0].attrs.roof_color is None


def test_lower_parts_take_their_colour_where_no_tower_covers_them():
    items = run(
        [
            way(1, rect(0, 0, 40, 20), building="church", height="20"),
            way(2, rect(0, 0, 30, 20), **{"building:part": "yes", "height": "20"}),
            way(3, rect(30, 0, 40, 20), **{"building:part": "tower", "height": "50"}),
        ]
    )
    img, info = _image([((0, 0, 30, 20), (160, 70, 50)), ((30, 0, 40, 20), (80, 140, 110))])
    roofs.apply(items, img, info)
    parts = {p.part: p.roof_color for p in items[0].parts}
    assert parts["yes"] == "#a04632"
    assert parts["tower"] == "#508c6e"


def _row(n: int, **extra) -> list[dict]:
    return [
        way(i + 1, rect(i * 6, 0, (i + 1) * 6, 10), building="house", height="9", **extra)
        for i in range(n)
    ]


def test_row_houses_under_one_roof_are_joined():
    items = run(_row(4))
    img, info = _image([((0, 0, 24, 10), (165, 75, 55))])
    assert rows.merge(items, roofs.measure(items, img, info)) == 4
    houses = [it for it in items if str(it.category) == "buildings"]
    assert len(houses) == 1
    assert houses[0].tags["xpsb:row_of"] == "4"
    assert assemble.generate(houses[0], items, "medium")[0].triangle_count > 0


def test_houses_with_different_roofs_stay_apart():
    items = run(_row(4))
    # The aerial image shows two roofs: red on the left two, grey on the right two.
    img, info = _image([((0, 0, 12, 10), (165, 75, 55)), ((12, 0, 24, 10), (90, 90, 95))])
    rows.merge(items, roofs.measure(items, img, info))
    assert sorted(it.tags.get("xpsb:row_of", "1") for it in items) == ["2", "2"]


def test_named_and_taller_houses_stay_apart():
    els = _row(3)
    els.append(way(9, rect(18, 0, 24, 10), building="house", height="15"))
    els.append(way(10, rect(24, 0, 30, 10), building="house", height="9", name="Pfarrhaus"))
    items = run(els)
    rows.merge(items)
    assert len(items) == 3  # the row of three, the tall house, the named one


def test_a_row_has_a_door_per_house_and_can_be_split_again():
    items = run(_row(4))
    img, info = _image([((0, 0, 24, 10), (165, 75, 55))])
    rows.merge(items, roofs.measure(items, img, info))
    roofs.apply(items, img, info)  # after the AI, as in the analysis
    (row,) = [it for it in items if str(it.category) == "buildings"]
    assert len(row.row_members) == 4
    mesh = assemble.generate(row, items, "medium")[0]
    doors = [p for p in mesh.patches if p.material == "door"]
    assert sum(len(p.tris) for p in doors) >= 4 * 2  # four doors, not one
    houses = rows.split(row)
    assert len(houses) == 4 and all(h.edited for h in houses)
    assert {h.osm_ref for h in houses} == {"way/1", "way/2", "way/3", "way/4"}
    assert all(h.attrs.roof_color for h in houses)
    again = houses + []
    assert rows.merge(again) == 0  # the user's decision stays
