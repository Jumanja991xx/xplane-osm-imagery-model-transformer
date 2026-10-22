"""The core promise: nothing with side effects happens without an approval."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest
from conftest import AUTH, HOCKENHEIM_AOI, wait_for_state

BRIEF = {
    "object_categories": ["grandstands", "buildings"],
    "surface_types": ["grass"],
    "use_ai": False,
    "package_name": "XPSB Test Hockenheim",
}


def create(client) -> dict:
    r = client.post("/api/jobs", json={"aoi": HOCKENHEIM_AOI}, headers=AUTH)
    assert r.status_code == 201, r.text
    return r.json()


def approve(client, job: dict, gate: str, hash_: str | None = None):
    h = hash_ if hash_ is not None else job["pending_gate"]["hash"]
    return client.post(
        f"/api/jobs/{job['id']}/gates/{gate}/approve", json={"hash": h}, headers=AUTH
    )


def to_analyzed(client) -> dict:
    job = create(client)
    job = client.put(f"/api/jobs/{job['id']}/brief", json=BRIEF, headers=AUTH).json()
    assert approve(client, job, "A").status_code == 200
    return wait_for_state(client, job["id"], {"analyzed"})


def test_new_job_has_no_gate_and_cannot_be_approved(client):
    job = create(client)
    assert job["state"] == "draft"
    assert job["pending_gate"] is None
    r = approve(client, job, "A", "anything")
    assert r.status_code == 409
    assert r.json()["detail"]["key"] == "error.wrong_state"


def test_gate_a_requires_the_exact_hash(client):
    job = create(client)
    job = client.put(f"/api/jobs/{job['id']}/brief", json=BRIEF, headers=AUTH).json()
    gate = job["pending_gate"]
    assert gate["gate"] == "A" and not gate["blocked"]
    assert {i["key"] for i in gate["items"]} >= {"gate.a.osm", "gate.a.no_ai", "gate.a.workdir"}

    r = approve(client, job, "A", "0" * 64)
    assert r.status_code == 409
    assert r.json()["detail"]["key"] == "error.gate_stale"

    # Changing the brief after viewing the gate invalidates the old hash.
    old_hash = gate["hash"]
    client.put(f"/api/jobs/{job['id']}/brief", json={**BRIEF, "surface_types": []}, headers=AUTH)
    assert approve(client, job, "A", old_hash).json()["detail"]["key"] == "error.gate_stale"


def test_ai_without_key_blocks_gate_a(client):
    job = create(client)
    job = client.put(
        f"/api/jobs/{job['id']}/brief", json={**BRIEF, "use_ai": True}, headers=AUTH
    ).json()
    assert job["pending_gate"]["blocked"] is True
    assert "gate.a.ai_no_key" in {i["key"] for i in job["pending_gate"]["items"]}
    assert approve(client, job, "A").json()["detail"]["key"] == "error.gate_blocked"

    client.put("/api/secrets/anthropic", json={"api_key": "sk-ant-x"}, headers=AUTH)
    job = client.get(f"/api/jobs/{job['id']}", headers=AUTH).json()
    assert job["pending_gate"]["blocked"] is False


def test_gate_b_shows_counts_and_plan_lists_confirmed_items(client, app_ctx):
    job = to_analyzed(client)
    gate = job["pending_gate"]
    assert gate["gate"] == "B" and gate["blocked"] is False
    counts = next(i for i in gate["items"] if i["key"] == "gate.b.counts")["params"]
    # grandstand + clubhouse + 2 buildings (canopy category not wanted) / pitch only
    assert counts == {"objects": 4, "surfaces": 1, "excluded": 0}
    job = approve(client, job, "B").json()
    assert job["state"] == "planned"
    assert job["plan"]["tile"] == "Earth nav data/+40+000/+49+008.dsf"
    confirmed = {
        i["label_key"]: i["count"] for i in job["plan"]["items"] if i["kind"] == "confirmed"
    }
    assert confirmed == {"category.buildings": 3, "category.grandstands": 1, "surface.grass": 1}
    scene = app_ctx.jobs.job_dir(job["id"]) / "scene.geojson"
    assert len(__import__("json").loads(scene.read_text())["features"]) == 5


def test_new_brief_resets_analysis_only_when_needed(client):
    job = to_analyzed(client)
    # Same analysis settings (only the package name changes): the list stays.
    job = client.put(
        f"/api/jobs/{job['id']}/brief",
        json={**BRIEF, "package_name": "Anderer Name"},
        headers=AUTH,
    ).json()
    assert job["state"] == "analyzed" and job["analysis"] is not None
    # Different categories: analysis has to run again.
    job = client.put(
        f"/api/jobs/{job['id']}/brief", json={**BRIEF, "surface_types": []}, headers=AUTH
    ).json()
    assert job["state"] == "clarified"
    assert job["analysis"] is None and job["answers"] == {}


def test_step_back_keeps_the_list(client):
    job = to_analyzed(client)
    client.patch(
        f"/api/jobs/{job['id']}/items/way/103", json={"attrs": {"height_m": 14}}, headers=AUTH
    )
    job = client.get(f"/api/jobs/{job['id']}", headers=AUTH).json()
    job = approve(client, job, "B").json()
    assert job["state"] == "planned"
    job = client.post(f"/api/jobs/{job['id']}/reopen", json={"to": "analyzed"}, headers=AUTH).json()
    assert job["state"] == "analyzed" and job["plan"] is None
    item = next(i for i in job["analysis"]["items"] if i["id"] == "way/103")
    assert item["attrs"]["height_m"] == 14
    # A cancelled job can be resumed as well.
    job = client.post(f"/api/jobs/{job['id']}/cancel", headers=AUTH).json()
    job = client.post(f"/api/jobs/{job['id']}/reopen", json={"to": "analyzed"}, headers=AUTH).json()
    assert job["state"] == "analyzed"
    jobs = client.get("/api/jobs", headers=AUTH).json()
    assert jobs[0]["id"] == job["id"] and jobs[0]["objects"] == 4


def test_too_large_area_is_rejected(client):
    big = {
        "type": "Polygon",
        "coordinates": [[[8, 49], [8.2, 49], [8.2, 49.2], [8, 49.2], [8, 49]]],
    }
    r = client.post("/api/jobs", json={"aoi": big}, headers=AUTH)
    assert r.status_code == 409
    assert r.json()["detail"]["key"] == "error.aoi_too_large"


def test_full_flow_builds_and_installs_with_backup(
    client, app_ctx, fake_xplane, dsftool_exe, xplane_state
):
    job = to_analyzed(client)
    job = approve(client, job, "B").json()

    # Gate C: build into the job folder only.
    assert job["pending_gate"]["gate"] == "C"
    assert approve(client, job, "C").status_code == 200
    job = wait_for_state(client, job["id"], {"built", "planned"})
    assert job["state"] == "built", job["error"]
    package = Path(job["build"]["package_dir"])
    assert (package / "Earth nav data/+40+000/+49+008.dsf").is_file()
    assert "README.txt" in job["build"]["files"]
    assert any(f.endswith("_nml.png") for f in job["build"]["files"])  # normal maps
    assert not any((fake_xplane / "Custom Scenery").iterdir())  # nothing installed yet

    # Gate D is blocked until an X-Plane folder is configured.
    assert job["pending_gate"]["gate"] == "D" and job["pending_gate"]["blocked"] is True
    settings = client.get("/api/settings", headers=AUTH).json()
    client.put("/api/settings", json={**settings, "xplane_path": str(fake_xplane)}, headers=AUTH)

    # A running X-Plane shows up as warning; if it changes, the old approval is stale.
    xplane_state.running = True
    job = client.get(f"/api/jobs/{job['id']}", headers=AUTH).json()
    assert "gate.d.xplane_running" in {i["key"] for i in job["pending_gate"]["items"]}
    stale_hash = job["pending_gate"]["hash"]
    xplane_state.running = False
    assert approve(client, job, "D", stale_hash).json()["detail"]["key"] == "error.gate_stale"

    job = client.get(f"/api/jobs/{job['id']}", headers=AUTH).json()
    assert approve(client, job, "D").status_code == 200
    job = wait_for_state(client, job["id"], {"installed", "built"})
    assert job["state"] == "installed", job["error"]
    target = fake_xplane / "Custom Scenery" / "XPSB Test Hockenheim"
    assert (target / "Earth nav data/+40+000/+49+008.dsf").is_file()
    assert job["install"]["backup_dir"] is None

    # Second run over an existing package: backup goes outside Custom Scenery.
    job = client.post(f"/api/jobs/{job['id']}/reopen", json={"to": "analyzed"}, headers=AUTH).json()
    job = approve(client, job, "B").json()
    approve(client, job, "C")
    job = wait_for_state(client, job["id"], {"built"})
    assert "gate.d.backup" in {i["key"] for i in job["pending_gate"]["items"]}
    approve(client, job, "D")
    job = wait_for_state(client, job["id"], {"installed"})
    backup = Path(job["install"]["backup_dir"])
    assert backup.is_dir() and backup.parent == app_ctx.paths.backups
    assert sorted(p.name for p in (fake_xplane / "Custom Scenery").iterdir()) == [
        "XPSB Test Hockenheim"
    ]


def test_rename_the_folder_after_the_build(client, app_ctx, fake_xplane, dsftool_exe):
    settings = client.get("/api/settings", headers=AUTH).json()
    client.put("/api/settings", json={**settings, "xplane_path": str(fake_xplane)}, headers=AUTH)
    job = to_analyzed(client)
    r = client.post(f"/api/jobs/{job['id']}/package-name", json={"name": "Zu früh"}, headers=AUTH)
    assert r.status_code == 409  # only between build and install
    job = approve(client, job, "B").json()
    approve(client, job, "C")
    job = wait_for_state(client, job["id"], {"built", "planned"})
    assert job["state"] == "built", job["error"]
    old = Path(job["build"]["package_dir"])

    for bad in ("Puskás Aréna", " ", "../evil", "x" * 80):
        r = client.post(f"/api/jobs/{job['id']}/package-name", json={"name": bad}, headers=AUTH)
        assert r.status_code == 409 and r.json()["detail"]["key"] == "error.invalid_package_name"
    r = client.post(
        f"/api/jobs/{job['id']}/package-name", json={"name": "Puskas Arena"}, headers=AUTH
    )
    assert r.status_code == 200, r.text
    job = r.json()
    package = Path(job["build"]["package_dir"])
    assert package.name == "Puskas Arena" and package.is_dir() and not old.exists()
    assert (package / "README.txt").read_text(encoding="utf-8").startswith("Puskas Arena\n")
    assert job["brief"]["package_name"] == job["plan"]["package_name"] == "Puskas Arena"
    target = {i["key"]: i for i in job["pending_gate"]["items"]}["gate.d.target"]
    assert target["params"]["path"].endswith("Puskas Arena")

    approve(client, job, "D")
    job = wait_for_state(client, job["id"], {"installed", "built"})
    assert job["state"] == "installed", job["error"]
    assert (fake_xplane / "Custom Scenery" / "Puskas Arena" / "README.txt").is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="uses a shell script as fake DSFTool")
def test_build_failure_rolls_back_to_planned(client, monkeypatch, tmp_path):
    fake = tmp_path / "DSFTool"
    fake.write_text("#!/bin/sh\necho 'not really'\nexit 3\n")
    fake.chmod(0o755)
    monkeypatch.setenv("XPSB_DSFTOOL", str(fake))
    settings = client.get("/api/settings", headers=AUTH).json()
    client.put("/api/settings", json={**settings, "dsftool_path": str(fake)}, headers=AUTH)

    job = to_analyzed(client)
    job = approve(client, job, "B").json()
    approve(client, job, "C")
    job = wait_for_state(client, job["id"], {"planned", "built"})
    assert job["state"] == "planned"
    assert job["error"]["key"] == "error.building_failed"
    assert job["pending_gate"]["gate"] == "C"  # user can fix the cause and approve again


def test_plan_generates_3d_preview_of_every_object(client, app_ctx):
    job = to_analyzed(client)
    job = approve(client, job, "B").json()
    plan = job["plan"]
    assert plan["test_scenery"] is False and plan["surfaces"] == 1
    objects = {o["item_id"]: o for o in plan["objects"]}
    assert objects["way/103"]["status"] == "ok" and objects["way/103"]["triangles"] > 50
    assert all(o["status"] == "ok" for o in objects.values()), objects
    assert any(f.startswith("objects/xpsb_obj_") and f.endswith(".obj") for f in plan["files"])

    preview = client.get(f"/api/jobs/{job['id']}/preview", headers=AUTH).json()
    assert len(preview["objects"]) == len(objects) and len(preview["surfaces"]) == 1
    stem = preview["objects"][0]["stem"]
    mesh = client.get(f"/api/jobs/{job['id']}/preview/{stem}.json", headers=AUTH).json()
    assert len(mesh["positions"]) == len(mesh["normals"]) and mesh["index"]
    png = client.get(f"/api/jobs/{job['id']}/preview/{stem}.png", headers=AUTH)
    assert png.headers["content-type"] == "image/png"
    assert (
        client.get(f"/api/jobs/{job['id']}/preview/..%2Fjob.json", headers=AUTH).status_code == 404
    )
    gate = {i["key"] for i in job["pending_gate"]["items"]}
    assert {"plan.objects", "plan.surfaces"} <= gate


def test_exclusions_under_objects_and_aerial_ground(client, app_ctx, dsftool_exe):
    brief = {
        **BRIEF,
        "exclusions": ["facades", "objects"],
        "surface_mode": "imagery",
    }
    settings = client.get("/api/settings", headers=AUTH).json()
    imagery = {
        "source": "custom",
        "custom_url": "https://wms.example.test/dop",
        "custom_attribution": "Test-Luftbild",
        "custom_rights_confirmed": True,
    }
    client.put("/api/settings", json={**settings, "imagery": imagery}, headers=AUTH)
    job = create(client)
    job = client.put(f"/api/jobs/{job['id']}/brief", json=brief, headers=AUTH).json()
    approve(client, job, "A")
    job = wait_for_state(client, job["id"], {"analyzed"})
    assert job["analysis"]["imagery"] is not None
    job = approve(client, job, "B").json()
    plan = job["plan"]
    built = sum(1 for o in plan["objects"] if o["status"] == "ok")
    assert built and plan["exclusion_rects"] == built
    keys = {i["key"] for i in job["pending_gate"]["items"]}
    assert {"gate.c.exclude_objects", "gate.c.ortho_in_package"} <= keys
    assert "gate.c.no_exclusions" not in keys
    assert "polygons/xpsb_ortho.pol" in plan["files"]

    approve(client, job, "C")
    job = wait_for_state(client, job["id"], {"built", "planned"})
    assert job["state"] == "built", job["error"]
    package = Path(job["build"]["package_dir"])
    text = (package.parent / f"{package.name}.dsf.txt").read_text()
    # One small rectangle per new object, not the whole area.
    assert text.count("PROPERTY sim/exclude_fac ") == built
    assert text.count("PROPERTY sim/exclude_obj ") == built
    assert "BEGIN_POLYGON 0 65535 4" in text
    assert "TEXTURE_NOWRAP xpsb_ortho.png" in (package / "polygons/xpsb_ortho.pol").read_text()
    # The texture is cropped to the surfaces: their points span (almost) all of it.
    uvs = [
        tuple(map(float, line.split()[3:5]))
        for line in text.splitlines()
        if line.startswith("POLYGON_POINT")
    ]
    us, vs = [u for u, _ in uvs], [v for _, v in uvs]
    assert 0 <= min(us) < 0.2 and 0.8 < max(us) <= 1 and 0 <= min(vs) < 0.2 and 0.8 < max(vs) <= 1
    from PIL import Image

    with Image.open(package / "polygons/xpsb_ortho.png") as tex:
        assert all(n & (n - 1) == 0 for n in tex.size)
    # Every object is shown regardless of the user's object density.
    assert "PROPERTY sim/require_object 1/0" in text
    roundtrip = (package.parent / "roundtrip.txt").read_text()
    assert "sim/require_object 1/0" in roundtrip
    assert roundtrip.count("sim/exclude_fac") == built


def test_no_exclusions_warns_at_gate_c(client):
    job = to_analyzed(client)
    job = approve(client, job, "B").json()
    keys = {i["key"] for i in job["pending_gate"]["items"]}
    assert "gate.c.no_exclusions" in keys


def test_packs_above_finds_sceneries_that_hide_our_removals(tmp_path):
    from xpsb.jobs.gates import packs_above

    cs = tmp_path / "Custom Scenery"
    tile = "Earth nav data/+40+010/+48+012.dsf"
    for name in ("EDML Landshut", "Other Tile", "XPSB Test", "simHeaven"):
        (cs / name).mkdir(parents=True)
    for name in ("EDML Landshut", "simHeaven"):
        (cs / name / tile).parent.mkdir(parents=True)
        (cs / name / tile).write_bytes(b"")
    target = cs / "XPSB Test"
    ini = cs / "scenery_packs.ini"
    ini.write_text(
        "I\n1000 Version\nSCENERY\n\n"
        "SCENERY_PACK Custom Scenery/EDML Landshut/\n"
        "SCENERY_PACK Custom Scenery/Other Tile/\n"
        "SCENERY_PACK Custom Scenery/XPSB Test/\n"
        "SCENERY_PACK Custom Scenery/simHeaven/\n"
    )
    assert packs_above(target, tile) == ["EDML Landshut"]
    ini.write_text(
        ini.read_text().replace(
            "SCENERY_PACK Custom Scenery/EDML", "SCENERY_PACK_DISABLED Custom Scenery/EDML"
        )
    )
    assert packs_above(target, tile) == []
    # Not listed yet (first install): X-Plane puts new packs at the top.
    assert packs_above(cs / "New Pack", tile) == []


def test_adjust_one_object_in_the_plan(client, app_ctx):
    job = to_analyzed(client)
    job = approve(client, job, "B").json()
    assert job["state"] == "planned"
    stand = next(o for o in job["plan"]["objects"] if o["category"] == "grandstands")
    old_hash = job["pending_gate"]["hash"]
    preview = app_ctx.jobs.job_dir(job["id"]) / "preview" / f"{stand['stem']}.json"
    before = preview.read_text()

    r = client.patch(
        f"/api/jobs/{job['id']}/objects/{stand['item_id']}",
        json={"attrs": {"flip": True, "rows": 4, "covered": True}},
        headers=AUTH,
    )
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["state"] == "planned"
    item = next(i for i in job["analysis"]["items"] if i["id"] == stand["item_id"])
    assert item["attrs"]["rows"] == 4 and item["attrs"]["flip"] is True and item["edited"]
    # only this object was rebuilt, and approval C reflects it
    assert preview.read_text() != before
    assert job["pending_gate"]["hash"] != old_hash

    # Moved 10 m east and turned by 90 degrees in the preview: no rebuild, new placement.
    before = preview.read_text()
    anchor = next(o for o in job["plan"]["objects"] if o["item_id"] == stand["item_id"])["anchor"]
    r = client.patch(
        f"/api/jobs/{job['id']}/objects/{stand['item_id']}",
        json={"attrs": {"shift_x_m": 10.0, "turn_deg": -90}},
        headers=AUTH,
    )
    assert r.status_code == 200, r.text
    moved = next(o for o in r.json()["plan"]["objects"] if o["item_id"] == stand["item_id"])
    assert preview.read_text() == before
    assert moved["heading"] == 270.0
    east_m = (moved["anchor"][0] - anchor[0]) * 111320 * math.cos(math.radians(anchor[1]))
    assert abs(east_m - 10.0) < 0.1 and abs(moved["anchor"][1] - anchor[1]) < 1e-9
    scene = client.get(f"/api/jobs/{job['id']}/preview", headers=AUTH)
    if scene.status_code == 200:
        obj = next(o for o in scene.json()["objects"] if o["item_id"] == stand["item_id"])
        assert obj["heading"] == 270.0

    bad = client.patch(
        f"/api/jobs/{job['id']}/objects/{stand['item_id']}",
        json={"attrs": {"rows": 0}},
        headers=AUTH,
    )
    assert bad.status_code == 409 and bad.json()["detail"]["key"] == "error.invalid_item"

    # Deleted in the preview: out of the plan and off the list (not built).
    old_hash = client.get(f"/api/jobs/{job['id']}", headers=AUTH).json()["pending_gate"]["hash"]
    r = client.delete(f"/api/jobs/{job['id']}/objects/{stand['item_id']}", headers=AUTH)
    assert r.status_code == 200, r.text
    job = r.json()
    assert all(o["item_id"] != stand["item_id"] for o in job["plan"]["objects"])
    item = next(i for i in job["analysis"]["items"] if i["id"] == stand["item_id"])
    assert item["included"] is False and item["edited"]
    assert job["pending_gate"]["hash"] != old_hash

    client.post(f"/api/jobs/{job['id']}/reopen", json={"to": "analyzed"}, headers=AUTH)
    late = client.patch(
        f"/api/jobs/{job['id']}/objects/{stand['item_id']}", json={"attrs": {}}, headers=AUTH
    )
    assert late.status_code == 409


def test_no_surfaces_keeps_the_ground_and_exclusions_cover_what_is_built(client, app_ctx):
    brief = {**BRIEF, "surface_mode": "none", "exclusions": ["objects"]}
    job = create(client)
    job = client.put(f"/api/jobs/{job['id']}/brief", json=brief, headers=AUTH).json()
    approve(client, job, "A")
    job = wait_for_state(client, job["id"], {"analyzed"})
    job = approve(client, job, "B").json()
    plan = job["plan"]
    assert plan["surface_mode"] == "none" and plan["surfaces"] == 0
    assert not any(f.startswith("polygons/") for f in plan["files"])
    preview = client.get(f"/api/jobs/{job['id']}/preview", headers=AUTH).json()
    assert preview["surfaces"] == []
    # Every built object knows what it covers; the exclusion zone takes all of it in.
    from xpsb.jobs import pipeline

    stored = app_ctx.jobs.get(job["id"])
    ok = [o for o in stored.plan.objects if o.status == "ok"]
    assert all(o.extent is not None for o in ok)
    rects = pipeline._exclusion_rects(stored, ok)
    for o, (w, s, e, n) in zip(ok, rects, strict=True):
        bw, bs, be, bn = pipeline._object_bounds(o)
        assert w <= bw and s <= bs and e >= be and n >= bn


def test_other_sceneries_for_the_same_tile(tmp_path):
    from xpsb.xplane import neighbors
    from xpsb.xplane.dsf_text import Tile

    tile = Tile(11, 48)
    custom = tmp_path / "Custom Scenery"
    for name, size in (("zOrtho4XP_+48+011", 30_000_000), ("Friedensengel", 2000), ("Mine", 10)):
        dsf = custom / name / tile.dsf_relative_path
        dsf.parent.mkdir(parents=True)
        with dsf.open("wb") as f:
            f.truncate(size)
    other = custom / "Elsewhere" / Tile(8, 49).dsf_relative_path
    other.parent.mkdir(parents=True)
    other.write_bytes(b"x")
    (custom / "scenery_packs.ini").write_text(
        "I\n1000 Version\nSCENERY\n\n"
        "SCENERY_PACK Custom Scenery/Friedensengel/\n"
        "SCENERY_PACK_DISABLED Custom Scenery/zOrtho4XP_+48+011/\n",
        encoding="utf-8",
    )
    found = neighbors.for_tile(str(tmp_path), tile, own="Mine")
    assert [(s.name, s.kind, s.enabled, s.order) for s in found] == [
        ("Friedensengel", "objects", True, 0),
        ("zOrtho4XP_+48+011", "ortho", False, 1),
    ]
    assert neighbors.for_tile(None, tile) == []


def test_objects_can_be_moved_after_the_build_and_install(
    client, app_ctx, fake_xplane, dsftool_exe
):
    settings = client.get("/api/settings", headers=AUTH).json()
    client.put("/api/settings", json={**settings, "xplane_path": str(fake_xplane)}, headers=AUTH)
    job = to_analyzed(client)
    r = client.patch(
        f"/api/jobs/{job['id']}/placement/way/103", json={"attrs": {"turn_deg": 90}}, headers=AUTH
    )
    assert r.status_code == 409  # not built yet: the plan's own tools are for that
    job = approve(client, job, "B").json()
    approve(client, job, "C")
    job = wait_for_state(client, job["id"], {"built", "planned"})
    assert job["state"] == "built", job["error"]
    package = Path(job["build"]["package_dir"])
    obj = next(o for o in job["plan"]["objects"] if o["item_id"] == "way/103")
    stem = obj["stem"]
    model = (package / "objects" / f"{stem}.obj").read_bytes()
    text = package.parent / f"{package.name}.dsf.txt"

    def placed() -> list[float]:
        lines = text.read_text(encoding="ascii").splitlines()
        defs = [ln.split()[1] for ln in lines if ln.startswith("OBJECT_DEF ")]
        idx = defs.index(f"objects/{stem}.obj")
        return [
            float(v)
            for v in next(ln for ln in lines if ln.startswith(f"OBJECT {idx} ")).split()[2:]
        ]

    lon, lat, _ = placed()
    r = client.patch(
        f"/api/jobs/{job['id']}/placement/way/103",
        json={"attrs": {"shift_x_m": 10, "turn_deg": 45}},
        headers=AUTH,
    )
    assert r.status_code == 200, r.text
    lon2, lat2, heading = placed()
    assert heading == pytest.approx(45) and lat2 == pytest.approx(lat, abs=1e-9)
    assert (lon2 - lon) * 111_320 * math.cos(math.radians(lat)) == pytest.approx(10, abs=0.1)
    assert (package / "objects" / f"{stem}.obj").read_bytes() == model  # not built again
    r = client.patch(
        f"/api/jobs/{job['id']}/placement/way/103", json={"attrs": {"height_m": 3}}, headers=AUTH
    )
    assert r.status_code == 409  # only where it stands can change now

    approve(client, client.get(f"/api/jobs/{job['id']}", headers=AUTH).json(), "D")
    job = wait_for_state(client, job["id"], {"installed", "built"})
    installed = fake_xplane / "Custom Scenery" / "XPSB Test Hockenheim" / job["plan"]["tile"]
    before = installed.read_bytes()
    r = client.patch(
        f"/api/jobs/{job['id']}/placement/way/103", json={"attrs": {"turn_deg": 90}}, headers=AUTH
    )
    assert r.status_code == 200, r.text
    assert installed.read_bytes() != before  # the installed copy follows

    # Deleted in the 3D view after the install: no longer placed, the model stays.
    before = installed.read_bytes()
    r = client.delete(f"/api/jobs/{job['id']}/objects/way/103", headers=AUTH)
    assert r.status_code == 200, r.text
    job = r.json()
    assert installed.read_bytes() != before
    with pytest.raises(StopIteration):
        placed()  # still defined (harmless), but no longer placed
    assert all(o["item_id"] != "way/103" for o in job["plan"]["objects"])
    item = next(i for i in job["analysis"]["items"] if i["id"] == "way/103")
    assert item["included"] is False
    r = client.delete(f"/api/jobs/{job['id']}/objects/way/103", headers=AUTH)
    assert r.status_code == 409  # already gone


def test_back_from_the_built_scenery_to_its_plan(client, app_ctx):
    """From the build result one step back: the plan and its preview stay, only the
    build is made again (gate C)."""
    job = to_analyzed(client)
    job = approve(client, job, "B").json()
    approve(client, job, "C")
    job = wait_for_state(client, job["id"], {"built", "planned"})
    assert job["state"] == "built", job["error"]
    stems = [o["stem"] for o in job["plan"]["objects"]]
    r = client.post(f"/api/jobs/{job['id']}/reopen", json={"to": "planned"}, headers=AUTH)
    assert r.status_code == 200, r.text
    back = r.json()
    assert back["state"] == "planned" and back["build"] is None
    assert [o["stem"] for o in back["plan"]["objects"]] == stems
    assert back["pending_gate"]["gate"] == "C"
