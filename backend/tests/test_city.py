"""City mode: ordinary buildings in blocks, landmarks single, one DSF placement each."""

from __future__ import annotations

import json
from pathlib import Path

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
