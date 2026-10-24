"""City mode: ordinary buildings in blocks, landmarks single, one DSF placement each."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import AUTH, wait_for_state
from test_job_gates import BRIEF, approve, create
from test_osm_sites import aoi, rect, run, way

from xpsb.city import build as city


def test_osm_buildings_become_blocks_with_one_atlas(tmp_path: Path):
    els = [
        way(i, rect(i * 30, 0, i * 30 + 20, 12), building="house", height="9") for i in range(12)
    ]
    els.append(way(99, rect(600, 0, 640, 30), building="apartments", **{"building:levels": "6"}))
    items = run(els, aoi(-50, -50, 700, 100))
    buildings = city.from_items(items)
    assert len(buildings) == 13
    flat = next(b for b in buildings if b.id == "way/99")
    assert flat.eave_m == 18.0 and flat.roof_type == "flat"  # 6 storeys, flat roof
    out, preview = tmp_path / "objects", tmp_path / "preview"
    blocks = city.build(
        buildings,
        "central_europe",
        (items[0].geometry["coordinates"][0][0][0], items[0].geometry["coordinates"][0][0][1]),
        out,
        preview_dir=preview,
    )
    assert len(blocks) >= 2  # 600 m apart: not one block
    assert sum(b.buildings for b in blocks) == 13
    assert (out / city.ATLAS).is_file() and (out / city.ATLAS_LIT).is_file()
    walls = (out / blocks[0].objects[0]).read_text(encoding="ascii")
    assert walls.startswith("I\n800") and f"TEXTURE {city.ATLAS}" in walls
    data = json.loads((preview / blocks[0].objects[0].replace(".obj", ".json")).read_text())
    assert len(data["positions"]) > 100 and data["index"]


def test_city_mode_end_to_end(client, app_ctx, fake_xplane, dsftool_exe):
    job = create(client)
    brief = {**BRIEF, "mode": "city", "exclusions": []}
    job = client.put(f"/api/jobs/{job['id']}/brief", json=brief, headers=AUTH).json()
    approve(client, job, "A")
    job = wait_for_state(client, job["id"], {"analyzed"})
    job = approve(client, job, "B").json()
    plan = job["plan"]
    assert plan["city"]["source"] == "osm" and plan["city"]["blocks"] >= 1
    city_objs = [o for o in plan["objects"] if o["category"] == "city"]
    singles = [o for o in plan["objects"] if o["category"] != "city"]
    assert city_objs and singles  # the grandstands stay single objects
    walls = next(o for o in city_objs if o["stem"].endswith("_walls"))
    r = client.get(f"/api/jobs/{job['id']}/preview/{walls['stem']}.png", headers=AUTH)
    assert r.status_code == 200  # the shared facade atlas
    r = client.get(f"/api/jobs/{job['id']}/preview/{walls['stem']}.json", headers=AUTH)
    assert r.status_code == 200 and r.json()["positions"]
    approve(client, job, "C")
    job = wait_for_state(client, job["id"], {"built", "planned"})
    assert job["state"] == "built", job["error"]
    package = Path(job["build"]["package_dir"])
    text = (package.parent / f"{package.name}.dsf.txt").read_text(encoding="ascii")
    assert "OBJECT_DEF objects/city_001_walls.obj" in text
    assert "sim/exclude_obj" in text and "sim/exclude_fac" in text  # X-Plane's houses go
    assert (package / "objects" / "city_atlas.png").is_file()


def test_stored_official_buildings_are_read_back_and_landmarks_left_out(tmp_path: Path):
    from dataclasses import asdict

    from shapely.geometry import box as bbox
    from shapely.geometry import mapping

    from xpsb.city.model import CityBuilding
    from xpsb.jobs import pipeline
    from xpsb.jobs.models import Brief, Job
    from xpsb.scene import SceneItem

    house = CityBuilding(
        "lod2/1",
        "lod2",
        [[(11.0, 48.0), (11.0002, 48.0), (11.0002, 48.0001)]],
        ground_z=500.0,
        eave_m=7,
        ridge_m=10,
        roof_type="gabled",
        walls=[[(1.0, 2.0, 3.0), (2.0, 2.0, 3.0), (2.0, 2.0, 9.0)]],
        zone=32,
    )
    church = CityBuilding(
        "lod2/2", "lod2", [[(11.001, 48.0), (11.0013, 48.0), (11.0013, 48.0003), (11.001, 48.0003)]]
    )
    (tmp_path / "city").mkdir()
    (tmp_path / "city" / "lod2.json").write_text(json.dumps([asdict(house), asdict(church)]))
    landmark = SceneItem(
        id="way/7",
        kind="object",
        category="buildings",
        source="osm",
        name="St. Peter",
        tags={"wikidata": "Q1", "building": "church"},
        geometry=mapping(bbox(11.001, 48.0, 11.0013, 48.0003)),
    )
    job = Job.model_validate(
        {
            "id": "j",
            "aoi": {
                "type": "Polygon",
                "coordinates": [[[11, 48], [11.01, 48], [11.01, 48.01], [11, 48]]],
            },
            "brief": Brief(
                package_name="X", object_categories=["buildings"], mode="city"
            ).model_dump(),
            "aoi_area_km2": 0.8,
            "aoi_center": [11.005, 48.005],
            "analysis": {"items": [landmark.model_dump()]},
        }
    )
    buildings, source = pipeline._city_buildings(job, tmp_path)
    assert source == "lod2" and [b.id for b in buildings] == ["lod2/1"]  # the church is single
    assert buildings[0].walls[0][0] == (1.0, 2.0, 3.0) and buildings[0].zone == 32


def test_a_row_of_houses_gets_one_look_not_a_patchwork(tmp_path: Path):
    """Houses wall to wall: one style and roof colour for the row; a taller block
    and a house across the street keep their own."""
    els = [
        way(i + 1, rect(i * 7, 0, (i + 1) * 7, 10), building="house", height="9") for i in range(6)
    ]
    els.append(way(20, rect(42, 0, 60, 14), building="apartments", height="16"))
    els.append(way(21, rect(0, 40, 9, 50), building="house", height="9"))
    houses = city.from_items(run(els))
    groups = city.neighbours(houses)
    sizes = sorted(len(g) for g in groups)
    assert sizes == [1, 1, 6]
    city.build(houses, "central_europe", (houses[0].footprint[0][0]), tmp_path / "out")
    row = next(g for g in groups if len(g) == 6)
    assert len({(b.style, b.variant, b.roof_color) for b in row}) == 1


def test_houses_are_joined_into_rows_without_measured_roof_colours():
    """Outside the official aerial images (Esri: recognition only) the rows are
    joined all the same."""
    from xpsb.osm import rows

    items = run(
        [
            way(i + 1, rect(i * 6, 0, (i + 1) * 6, 10), building="house", height="9")
            for i in range(4)
        ]
    )
    assert rows.merge(items, {}) == 4


def _block_with(tmp_path: Path, els, ground=None):
    houses = city.from_items(run(els, aoi(-400, -400, 400, 400)))
    out = tmp_path / "out"
    center = houses[0].footprint[0][0]
    blocks = city.build(houses, "central_europe", center, out, ground=ground)
    return houses, blocks, out


def test_houses_get_a_soft_contact_shadow_draped_onto_the_ground(tmp_path: Path):
    _, blocks, out = _block_with(tmp_path, [way(1, rect(0, 0, 10, 8), building="house")])
    (block,) = blocks
    name = next(o for o in block.objects if o.endswith("_ground.obj"))
    text = (out / name).read_text()
    assert "TEXTURE_DRAPED city_shadow.png" in text and "ATTR_draped" in text
    verts = [ln.split() for ln in text.splitlines() if ln.startswith("VT ")]
    assert verts and all(float(v[2]) == 0.0 for v in verts)
    xs = [float(v[1]) for v in verts]
    assert max(xs) - min(xs) == pytest.approx(10 + 2 * 3.2, abs=0.3)  # reaches 3.2 m out
    from PIL import Image

    with Image.open(out / "city_shadow.png") as img:
        alpha = [img.getpixel((x, 4))[3] for x in (0, 63)]
    assert alpha[0] > 150 and alpha[1] == 0  # dark at the wall, gone at the edge


def test_the_foot_of_the_walls_is_darker():
    from xpsb.city import atlas, styles

    f = styles.CENTRAL_EUROPE.styles[0].facade
    img, _, spans = atlas.paint_cell(f, f.colors[0], seed=1)
    shade = atlas._foot_shade(f, spans, img.shape[0])
    assert shade[-1] == pytest.approx(atlas.FOOT_DARK, abs=0.05)
    assert shade[0] == 1.0 and (shade[:-1] >= shade[1:] - 1e-6).all()  # brighter upwards


def test_each_house_stands_on_its_own_ground(tmp_path: Path):
    """On a slope every house gets X-Plane's height at its place, relative to the
    ground at the block's origin (where X-Plane puts the object)."""
    from xpsb.geo import METERS_PER_DEG_LAT
    from xpsb.xplane.terrain import Grid

    lat0 = 48.0

    def slope(lon, lat):  # 10 % uphill to the north
        return 500.0 + 0.1 * (lat - lat0) * METERS_PER_DEG_LAT

    houses, blocks, out = _block_with(
        tmp_path,
        [
            way(1, rect(0, -60, 10, -50), building="house"),
            way(2, rect(0, 50, 10, 60), building="house"),
        ],
        ground=slope,
    )
    assert len(blocks) == 1
    walls = next(o for o in blocks[0].objects if o.endswith("_walls.obj"))
    lows = sorted(
        min(float(v.split()[2]) for v in grp) for grp in _walls_by_house((out / walls).read_text())
    )
    assert lows[1] - lows[0] == pytest.approx(11.0, abs=1.5)  # 110 m apart, 10 %

    # the grid kept by the analysis gives the same heights
    class Plane:
        def height(self, lon, lat):
            return slope(lon, lat)

    grid = Grid.sample(Plane(), (11.0, 47.99, 11.01, 48.01), step_m=50)
    assert Grid.from_json(grid.to_json()).height(11.005, 48.004) == pytest.approx(
        slope(11.005, 48.004), abs=0.05
    )


def _walls_by_house(text: str) -> list[list[str]]:
    """Wall vertices split into the two houses (at the widest north-south gap)."""
    verts = sorted(
        (ln for ln in text.splitlines() if ln.startswith("VT ")), key=lambda v: float(v.split()[3])
    )
    zs = [float(v.split()[3]) for v in verts]
    cut = max(range(1, len(zs)), key=lambda i: zs[i] - zs[i - 1])
    return [verts[:cut], verts[cut:]]


def test_the_aerial_image_gets_a_darker_rim_at_the_foot_of_the_houses():
    from PIL import Image
    from shapely.geometry import box

    from xpsb.imagery import retouch

    img = Image.new("RGB", (200, 200), (120, 140, 90))
    bounds = (11.0, 48.0, 11.002, 48.002)
    house = box(11.0009, 48.0009, 11.0011, 48.0011)
    out = retouch.rims(img, bounds, [house])
    near = out.getpixel((100, 89))  # just north of the house
    far = out.getpixel((10, 10))
    assert sum(near) < sum(far) * 0.9 and far == (120, 140, 90)


def test_walls_have_a_bounded_number_of_bands():
    """A plinth that lies only below the ground used an eave height of 1e9 m: a list
    of hundreds of millions of storeys, which ate all memory on a hillside
    (Kloster Andechs). The bands stay few whatever the eave height."""
    from xpsb.city import mesh
    from xpsb.city.atlas import Cell

    cell = Cell(0.0, 1.0, {}, bay_m=3.5, ground_m=3.5, storey_m=3.0)
    assert len(mesh._bands(1e9, cell)) <= mesh.MAX_FLOORS + 32
    assert len(mesh._bands(9.5, cell)) < 40


def test_an_old_town_row_has_a_colour_per_house_and_the_mapped_one(tmp_path: Path):
    """Baroque old town (Buda castle district): the houses of a row share the style
    but each has its own colour; a colour OSM gives wins."""
    els = [
        way(i + 1, rect(i * 9, 0, (i + 1) * 9, 12), building="house", height="10") for i in range(8)
    ]
    els.append(
        way(30, rect(72, 0, 81, 12), building="house", height="10", **{"building:colour": "pink"})
    )
    houses = city.from_items(run(els))
    city.build(houses, "central_europe_city", houses[0].footprint[0][0], tmp_path / "out")
    row = [b for b in houses if b.style == "baroque"]
    assert len(row) >= 8 and len({b.style for b in row}) == 1
    assert len({b.variant for b in row}) >= 4  # not one colour for all
    from xpsb.city.styles import CENTRAL_EUROPE_CITY

    baroque = next(s for s in CENTRAL_EUROPE_CITY.styles if s.facade.id == "baroque")
    pink = next(b for b in houses if b.tags.get("building:colour") == "pink")
    assert baroque.facade.colors[pink.variant] == (238, 214, 196)  # the pale rose
