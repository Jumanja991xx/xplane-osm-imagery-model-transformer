"""X-Plane library: catalog from library.txt files, place() in build scripts,
library objects as plain references in the DSF."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import AUTH, wait_for_state
from test_analysis import planned

from xpsb.build.sandbox import ScriptError, ScriptInputs, run_script
from xpsb.xplane.library import load, measure, parse_library

LAMP_OBJ = (
    "I\n800\nOBJ\n\nPOINT_COUNTS 2 0 0 0\nVT -0.5 0 -0.25 0 1 0 0 0\nVT 0.5 9 0.25 0 1 0 0 0\n"
)


def make_library(xplane: Path) -> None:
    """A tiny X-Plane library: one default pack, one add-on pack in Custom Scenery."""
    default = xplane / "Resources/default scenery/1000 roads"
    (default / "objects").mkdir(parents=True)
    (default / "objects/lamp.obj").write_text(LAMP_OBJ)
    (default / "library.txt").write_text(
        "A\n800\nLIBRARY\n\n"
        "EXPORT lib/street/lights/street_lamp.obj objects/lamp.obj\n"
        "EXPORT lib/street/lights/lamp.fac objects/lamp.fac\n"  # not an object
        "PRIVATE\n"
        "EXPORT lib/street/lights/secret_lamp.obj objects/lamp.obj\n"
        "PUBLIC\n"
        "EXPORT_RATIO 0.5 lib/vegetation/tree_oak.obj objects/lamp.obj\n"
        "DEPRECATED\n"
        "EXPORT lib/street/old_bench.obj objects/lamp.obj\n"
    )
    addon = xplane / "Custom Scenery/OpenSceneryX"
    (addon / "objects").mkdir(parents=True)
    (addon / "objects/fence.obj").write_text(LAMP_OBJ)
    (addon / "library.txt").write_text(
        "A\n800\nLIBRARY\n\nEXPORT opensceneryx/fences/wire_fence.obj objects/fence.obj\n"
    )


def test_parse_and_catalog(tmp_path):
    xp = tmp_path / "X-Plane 12"
    make_library(xp)
    text = (xp / "Resources/default scenery/1000 roads/library.txt").read_text()
    paths = [o.path for o in parse_library(text, Path("."), "p", True)]
    assert paths == ["lib/street/lights/street_lamp.obj", "lib/vegetation/tree_oak.obj"]
    assert measure(str(xp / "Resources/default scenery/1000 roads/objects/lamp.obj")) == (
        1.0,
        0.5,
        9.0,
    )

    cache = tmp_path / "cache.json"
    cat = load(str(xp), cache)
    assert set(cat.objects) == {
        "lib/street/lights/street_lamp.obj",
        "lib/vegetation/tree_oak.obj",
        "opensceneryx/fences/wire_fence.obj",
    }
    lamp = cat.objects["lib/street/lights/street_lamp.obj"]
    assert lamp.default and lamp.size == (1.0, 0.5, 9.0)
    assert not cat.objects["opensceneryx/fences/wire_fence.obj"].default
    assert {o.path for o in cat.offer("Flutlicht")} >= {"lib/street/lights/street_lamp.obj"}
    # Second load comes from the cache, same content.
    assert set(load(str(xp), cache).objects) == set(cat.objects)
    assert len(load(None, cache)) == 0


def test_place_only_known_library_objects():
    inputs = ScriptInputs(footprint=[[0, 0], [10, 0], [10, 5], [0, 5]], path=None, params={})
    lib = frozenset({"lib/street/lights/street_lamp.obj"})
    mesh = run_script(
        "place('lib/street/lights/street_lamp.obj', 1, 2, 90)", inputs, 100, library=lib
    )
    assert not mesh.patches and [(p.path, p.x, p.y, p.heading) for p in mesh.placements] == [
        ("lib/street/lights/street_lamp.obj", 1.0, 2.0, 90.0)
    ]
    with pytest.raises(ScriptError, match="not in the X-Plane library"):
        run_script("place('lib/made/up.obj', 0, 0)", inputs, 100, library=lib)


def test_architect_places_library_objects_into_the_dsf(
    client, fake_ai, app_ctx, fake_xplane, dsftool_exe
):
    make_library(fake_xplane)
    settings = client.get("/api/settings", headers=AUTH).json()
    client.put("/api/settings", json={**settings, "xplane_path": str(fake_xplane)}, headers=AUTH)
    job = planned(client)
    jid = job["id"]
    obj = next(o for o in job["plan"]["objects"] if o["category"] == "buildings")
    url = f"/api/jobs/{jid}/design/{obj['item_id']}"
    est = client.get(url, headers=AUTH).json()
    fake_ai.calls.clear()
    r = client.post(
        url, json={"request": "Laternen an die Ecken", "max_usd": est["max_usd"]}, headers=AUTH
    )
    assert r.status_code == 200, r.text
    first = "".join(b.get("text", "") for b in fake_ai.calls[0]["content"])
    assert "lib/street/lights/street_lamp.obj  (1 x 0.5 x 9)" in first
    assert "place(" in fake_ai.calls[0]["system"]
    job = r.json()["job"]
    plan_obj = next(o for o in job["plan"]["objects"] if o["item_id"] == obj["item_id"])
    assert len(plan_obj["library"]) >= 4 and plan_obj["library"][0]["size"] == [1.0, 0.5, 9.0]
    assert "plan.library" in {i["label_key"] for i in job["plan"]["items"]}

    # Build: the lamps are referenced by their virtual path, not copied.
    r = client.post(
        f"/api/jobs/{jid}/gates/C/approve", json={"hash": job["pending_gate"]["hash"]}, headers=AUTH
    )
    assert r.status_code == 200, r.text
    job = wait_for_state(client, jid, {"built", "planned"})
    assert job["state"] == "built", job["error"]
    package = Path(job["build"]["package_dir"])
    text = (package.parent / f"{package.name}.dsf.txt").read_text()
    lamp = [line for line in text.splitlines() if line.startswith("OBJECT_DEF lib/")]
    assert lamp == [f"OBJECT_DEF {plan_obj['library'][0]['path']}"]
    assert not (package / "lib").exists()
    assert "Requires these library packs" not in (package / "README.txt").read_text()
