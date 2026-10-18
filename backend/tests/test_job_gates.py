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


def test_open_questions_block_gate_b(client):
    job = to_analyzed(client)
    assert job["analysis"]["placeholder"] is True
    assert job["pending_gate"]["gate"] == "B" and job["pending_gate"]["blocked"] is True
    assert approve(client, job, "B").json()["detail"]["key"] == "error.gate_blocked"

    bad = client.post(
        f"/api/jobs/{job['id']}/answers", json={"test_cube_height": "999"}, headers=AUTH
    )
    assert bad.status_code == 409

    job = client.post(
        f"/api/jobs/{job['id']}/answers", json={"test_cube_height": "40"}, headers=AUTH
    ).json()
    assert job["pending_gate"]["blocked"] is False
    job = approve(client, job, "B").json()
    assert job["state"] == "planned"
    assert job["plan"]["cube_height_m"] == 40.0
    assert job["plan"]["tile"] == "Earth nav data/+40+000/+49+008.dsf"


def test_new_brief_resets_analysis(client):
    job = to_analyzed(client)
    job = client.put(f"/api/jobs/{job['id']}/brief", json=BRIEF, headers=AUTH).json()
    assert job["state"] == "clarified"
    assert job["analysis"] is None and job["answers"] == {}


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
    job = client.post(
        f"/api/jobs/{job['id']}/answers", json={"test_cube_height": "20"}, headers=AUTH
    ).json()
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
    job = client.put(f"/api/jobs/{job['id']}/brief", json=BRIEF, headers=AUTH).json()
    assert approve(client, job, "A").status_code == 200
    job = wait_for_state(client, job["id"], {"analyzed"})
    job = client.post(
        f"/api/jobs/{job['id']}/answers", json={"test_cube_height": "10"}, headers=AUTH
    ).json()
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
    job = client.post(
        f"/api/jobs/{job['id']}/answers", json={"test_cube_height": "20"}, headers=AUTH
    ).json()
    job = approve(client, job, "B").json()
    approve(client, job, "C")
    job = wait_for_state(client, job["id"], {"planned", "built"})
    assert job["state"] == "planned"
    assert job["error"]["key"] == "error.building_failed"
    assert job["pending_gate"]["gate"] == "C"  # user can fix the cause and approve again
