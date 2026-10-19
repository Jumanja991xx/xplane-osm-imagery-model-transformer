"""The core promise: nothing with side effects happens without an approval."""

from __future__ import annotations

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
