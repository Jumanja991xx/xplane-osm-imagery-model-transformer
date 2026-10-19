"""Milestone 2: real analysis (OSM + imagery + AI), editor and plan from the list."""

from __future__ import annotations

import io

from conftest import AUTH, FIXTURES, HOCKENHEIM_AOI, wait_for_state
from PIL import Image

from xpsb.ai import analyze as ai
from xpsb.imagery import wms
from xpsb.imagery.sources import BAYERN_DOP40, custom_source
from xpsb.osm import classify, overpass

ALL = {
    "object_categories": ["buildings", "grandstands", "canopies", "barriers", "masts"],
    "surface_types": ["grass", "asphalt", "dirt"],
    "use_ai": True,
    "ai_model": "claude-sonnet-5-5",
    "ai_cost_limit_usd": 5.0,
    "ai_detect": False,  # object search has its own tests
    "package_name": "XPSB Speedway",
}
CUSTOM_IMAGERY = {
    "source": "custom",
    "custom_url": "https://wms.example.test/dop",
    "custom_layer": "",
    "custom_attribution": "Test-Luftbild",
    "custom_rights_confirmed": True,
}


def setup(client, imagery=CUSTOM_IMAGERY, key=True, brief=ALL) -> dict:
    settings = client.get("/api/settings", headers=AUTH).json()
    client.put("/api/settings", json={**settings, "imagery": imagery}, headers=AUTH)
    if key:
        client.put("/api/secrets/anthropic", json={"api_key": "sk-ant-test"}, headers=AUTH)
    job = client.post("/api/jobs", json={"aoi": HOCKENHEIM_AOI}, headers=AUTH).json()
    return client.put(f"/api/jobs/{job['id']}/brief", json=brief, headers=AUTH).json()


def analyze(client, job: dict) -> dict:
    r = client.post(
        f"/api/jobs/{job['id']}/gates/A/approve",
        json={"hash": job["pending_gate"]["hash"]},
        headers=AUTH,
    )
    assert r.status_code == 200, r.text
    job = wait_for_state(client, job["id"], {"analyzed", "clarified"})
    assert job["state"] == "analyzed", job["error"]
    return job


def wait_until_rated(client, jid: str) -> dict:
    import time

    for _ in range(100):
        job = client.get(f"/api/jobs/{jid}", headers=AUTH).json()
        if not any(i.get("rating") for i in job["analysis"]["items"]):
            return job
        time.sleep(0.05)
    raise AssertionError("rating did not finish")


def keys(items) -> set[str]:
    return {i["key"] for i in items}


# ---- units -----------------------------------------------------------------------


def test_classifier_on_fixture():
    data = overpass.fetch(overpass.build_query([(0, 0), (1, 0), (1, 1), (0, 0)]), FIXTURES)
    from shapely.geometry import Polygon

    items = classify.classify(
        data["elements"],
        Polygon(HOCKENHEIM_AOI["coordinates"][0]),
        {"buildings", "grandstands", "canopies", "barriers", "masts"},
        {"grass", "asphalt", "dirt"},
    )
    by_ref = {i.osm_ref: i for i in items}
    assert by_ref["way/101"].category == "dirt"
    assert by_ref["way/102"].category == "grass"
    assert by_ref["way/103"].category == "grandstands" and by_ref["way/103"].attrs.height_m == 9
    assert by_ref["way/104"].attrs.height_m == 6.0  # 2 levels
    assert by_ref["way/106"].category == "canopies"
    assert by_ref["way/107"].category == "barriers"
    assert by_ref["node/201"].category == "masts"
    assert by_ref["relation/301"].geometry["type"] == "Polygon"
    assert len(by_ref["relation/301"].geometry["coordinates"]) == 2  # with courtyard
    assert "way/109" not in by_ref and "way/110" not in by_ref
    assert items[0].kind == "surface"  # surfaces first


def test_overpass_query_uses_polygon_filter():
    q = overpass.build_query([(8.5, 49.0), (8.6, 49.0), (8.6, 49.1), (8.5, 49.0)])
    assert 'poly:"49.0000000 8.5000000 49.0000000 8.6000000 49.1000000 8.6000000"' in q
    assert q.rstrip().endswith("out geom tags;")


def test_wms_layer_choice_and_tiling(tmp_path):
    layers = wms.get_capabilities(BAYERN_DOP40)
    assert wms.choose_layer(BAYERN_DOP40, layers) == "by_dop40c"
    other = custom_source("https://x.test/wms", "missing", "")
    assert wms.choose_layer(other, ["metadaten", "dop_rgb"]) == "dop_rgb"

    area = wms.Bounds(8.5630, 49.3265, 8.5685, 49.3292)
    info = wms.fetch_area_image(BAYERN_DOP40, area, tmp_path)
    assert info.gsd_m == 0.4 and info.west < area.west and info.north > area.north
    with Image.open(tmp_path / "aoi.jpg") as im:
        assert im.size == (info.width_px, info.height_px)


def test_crop_has_outline_and_sensible_size(tmp_path):
    area = wms.Bounds(8.5630, 49.3265, 8.5685, 49.3292)
    info = wms.fetch_area_image(BAYERN_DOP40, area, tmp_path)
    data = overpass.fetch("x", FIXTURES)
    from shapely.geometry import Polygon

    items = classify.classify(
        data["elements"], Polygon(HOCKENHEIM_AOI["coordinates"][0]), {"grandstands", "masts"}, set()
    )
    with Image.open(tmp_path / "aoi.jpg") as img:
        image = img.convert("RGB")
    for item in items:
        png, gsd = ai.render_crop(image, info, item)
        with Image.open(io.BytesIO(png)) as crop:
            assert ai.CROP_MIN_PX <= max(crop.size) <= ai.CROP_MAX_PX
            assert ai.OUTLINE in {c for _, c in crop.getcolors(1 << 20)}
        assert 0 < gsd <= info.gsd_m


def test_apply_result_keeps_osm_facts():
    from xpsb.scene import ItemAttrs, SceneItem

    item = SceneItem(
        id="way/1",
        kind="object",
        category="buildings",
        geometry={"type": "Point", "coordinates": [8.5, 49.3]},
        source="osm",
        tags={"height": "12"},
        attrs=ItemAttrs(height_m=12),
    )
    r = ai.AiItemResult(
        item_id="way/1",
        category="grandstands",
        height_m=30,
        levels=None,
        roof_shape="unknown",
        roof_color="#FFAA00",
        wall_color=None,
        seat_color=None,
        material=None,
        covered=None,
        confidence=0.7,
        summary="x",
        question=None,
    )
    assert ai.apply_result(item, r, "m") is None
    assert item.attrs.height_m == 12  # OSM wins
    assert item.category == "buildings"  # 0.7 < threshold
    assert item.attrs.roof_color == "#ffaa00" and item.attrs.roof_shape is None


# ---- API flow --------------------------------------------------------------------


def test_gate_a_lists_sources_cost_and_upload(client):
    job = setup(client)
    items = job["pending_gate"]["items"]
    assert {
        "gate.a.osm",
        "gate.a.imagery",
        "gate.a.ai_cost",
        "gate.a.ai_month",
        "gate.a.ai_upload",
        "gate.a.workdir",
    } <= keys(items)
    imagery = next(i for i in items if i["key"] == "gate.a.imagery")
    assert imagery["params"]["attribution"] == "Test-Luftbild"
    assert job["pending_gate"]["blocked"] is False


def test_custom_source_needs_confirmed_rights(client):
    job = setup(client, imagery={**CUSTOM_IMAGERY, "custom_rights_confirmed": False})
    assert job["pending_gate"]["blocked"] is True
    assert "gate.a.custom_rights" in keys(job["pending_gate"]["items"])


def test_area_outside_open_sources_is_announced(client):
    job = setup(client, imagery={"source": "auto"})
    assert "gate.a.imagery.not_covered" in keys(job["pending_gate"]["items"])
    assert "gate.a.ai_needs_imagery" in keys(job["pending_gate"]["items"])


def test_analysis_with_ai_questions_and_editor(client, fake_ai, app_ctx):
    job = analyze(client, setup(client))
    a = job["analysis"]
    assert a["imagery"]["attribution"] == "Test-Luftbild"
    assert len(a["items"]) == 10
    assert a["ai"]["analyzed"] == 10 and a["ai"]["models_used"] == ["claude-sonnet-5-5"]
    # 2 calls à 3000 in / 1500 out on Sonnet ($2/$10 per MTok)
    assert len(fake_ai.calls) == 2
    assert abs(a["ai"]["cost_usd"] - 2 * 0.021) < 1e-9
    assert fake_ai.keys == ["sk-ant-test"]
    assert app_ctx.jobs.ledger.month_spent() > 0

    # Key never ends up in job files.
    job_dir = app_ctx.jobs.job_dir(job["id"])
    for f in job_dir.rglob("*"):
        if f.is_file() and f.suffix in (".json", ".ndjson", ".geojson"):
            assert "sk-ant-test" not in f.read_text(encoding="utf-8")

    items = {i["id"]: i for i in a["items"]}
    assert items["way/103"]["attrs"]["height_m"] == 9  # OSM value kept
    assert items["way/105"]["attrs"]["height_m"] == 7.5  # AI filled the gap
    assert items["way/105"]["attrs"]["roof_color"] == "#aa3322"
    assert items["way/105"]["attrs"]["wall_color"] is None  # invalid colour dropped
    assert items["way/105"]["ai"]["summary"].startswith("Fake")

    # Unsure buildings without height produce questions; invalid options are dropped.
    questions = {q["id"]: q for q in a["questions"]}
    assert set(questions) == {"ai:way/104:height_m", "ai:relation/301:height_m"}
    q = questions["ai:relation/301:height_m"]
    assert [o["value"] for o in q["options"]] == ["6", "9"]
    assert job["pending_gate"]["blocked"] is True

    # Answering applies the value to the item.
    job = client.post(f"/api/jobs/{job['id']}/answers", json={q["id"]: "9"}, headers=AUTH).json()
    item = next(i for i in job["analysis"]["items"] if i["id"] == "relation/301")
    assert item["attrs"]["height_m"] == 9.0
    assert items["way/104"]["attrs"]["height_m"] == 6.0  # levels from OSM win

    # Editing an item settles its open question too.
    for qid in [x for x in questions if x != q["id"]]:
        item_id = questions[qid]["item_id"]
        r = client.patch(
            f"/api/jobs/{job['id']}/items/{item_id}", json={"attrs": {"height_m": 5}}, headers=AUTH
        )
        assert r.status_code == 200, r.text
        job = r.json()
    assert job["pending_gate"]["blocked"] is False

    # Exclude, reclassify, add, delete.
    jid = job["id"]
    job = client.patch(
        f"/api/jobs/{jid}/items/way/107", json={"included": False}, headers=AUTH
    ).json()
    bad = client.patch(f"/api/jobs/{jid}/items/way/106", json={"category": "grass"}, headers=AUTH)
    assert bad.status_code == 409 and bad.json()["detail"]["key"] == "error.invalid_item"
    job = client.patch(
        f"/api/jobs/{jid}/items/way/106", json={"category": "grandstands"}, headers=AUTH
    ).json()
    new = {
        "kind": "object",
        "category": "scaffolding",
        "name": "Kamerapodest",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [8.5650, 49.3270],
                    [8.5652, 49.3270],
                    [8.5652, 49.3271],
                    [8.5650, 49.3271],
                    [8.5650, 49.3270],
                ]
            ],
        },
        "attrs": {"height_m": 6},
    }
    r = client.post(f"/api/jobs/{jid}/items", json=new, headers=AUTH)
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["analysis"]["items"][-1]["id"] == "user/1"
    job = client.delete(f"/api/jobs/{jid}/items/node/201", headers=AUTH).json()
    assert client.delete(f"/api/jobs/{jid}/items/node/201", headers=AUTH).status_code == 409

    counts = next(i for i in job["pending_gate"]["items"] if i["key"] == "gate.b.counts")
    assert counts["params"] == {"objects": 6, "surfaces": 3, "excluded": 1}

    # Imagery is served with auth only.
    assert client.get(f"/api/jobs/{jid}/imagery").status_code == 401
    r = client.get(f"/api/jobs/{jid}/imagery", headers=AUTH)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"

    # The drawn scaffolding is rated by the AI in the background (height, roof ...).
    job = wait_until_rated(client, jid)
    drawn = next(i for i in job["analysis"]["items"] if i["id"] == "user/1")
    assert drawn["ai"] is not None and drawn["attrs"]["height_m"] == 6  # user value kept
    assert drawn["category"] == "scaffolding"
    job = client.post(
        f"/api/jobs/{jid}/gates/B/approve", json={"hash": job["pending_gate"]["hash"]}, headers=AUTH
    ).json()
    assert job["state"] == "planned"
    confirmed = {
        i["label_key"]: i["count"] for i in job["plan"]["items"] if i["kind"] == "confirmed"
    }
    assert confirmed["category.grandstands"] == 2
    assert confirmed["category.scaffolding"] == 1
    assert "category.barriers" not in confirmed and "category.masts" not in confirmed
    # No more edits once planned.
    r = client.patch(f"/api/jobs/{jid}/items/way/103", json={"included": False}, headers=AUTH)
    assert r.status_code == 409


def test_cost_limit_stops_before_spending(client, fake_ai):
    # First call costs more than estimated ($0.056): the next estimate no longer fits.
    fake_ai.tokens = (3000, 5000)
    job = analyze(client, setup(client, brief={**ALL, "ai_cost_limit_usd": 0.07}))
    a = job["analysis"]
    assert len(fake_ai.calls) == 1  # second batch would exceed the limit
    assert a["ai"]["limit_reached"] is True and a["ai"]["skipped"] == 4
    assert "analysis.ai_limit_reached" in keys(a["notes"])


def test_monthly_limit_is_part_of_the_budget(client, fake_ai, app_ctx):
    settings = client.get("/api/settings", headers=AUTH).json()
    settings["ai"]["monthly_limit_usd"] = 1.0
    client.put("/api/settings", json=settings, headers=AUTH)
    app_ctx.jobs.ledger.add(1.0)
    job = setup(client)
    assert "gate.a.ai_month_exhausted" in keys(job["pending_gate"]["items"])
    job = analyze(client, job)
    assert fake_ai.calls == [] and job["analysis"]["ai"]["limit_reached"] is True


def test_auth_failure_keeps_osm_result(client, fake_ai):
    fake_ai.fail_auth = True
    job = analyze(client, setup(client))
    assert len(job["analysis"]["items"]) == 10
    assert "analysis.ai_auth_failed" in keys(job["analysis"]["notes"])
    assert job["analysis"]["ai"]["analyzed"] == 0


def test_server_side_fallback_is_reported(client, fake_ai):
    fake_ai.served_model = "claude-opus-5-5"
    job = analyze(client, setup(client))
    note = next(n for n in job["analysis"]["notes"] if n["key"] == "analysis.ai_fallback_model")
    assert note["params"]["model"] == "claude-opus-5-5"


def test_without_ai_nothing_is_sent(client, fake_ai):
    job = analyze(client, setup(client, key=False, brief={**ALL, "use_ai": False}))
    assert fake_ai.calls == [] and job["analysis"]["ai"] is None
    assert "analysis.ai_off" in keys(job["analysis"]["notes"])


def test_api_error_detail_reaches_the_user(client, fake_ai):
    fake_ai.fail_call = "HTTP 529 Overloaded"
    job = analyze(client, setup(client))
    note = next(n for n in job["analysis"]["notes"] if n["key"] == "analysis.ai_call_failed")
    assert note["params"]["detail"] == "HTTP 529 Overloaded"
    assert len(job["analysis"]["items"]) == 10


def test_rating_knows_the_area(client, fake_ai):
    analyze(client, setup(client))
    ratings = [c for c in fake_ai.calls if c["format"] == "AiBatchResult"]
    assert "race/running track (sport: speedway)" in ratings[0]["system"]
    assert "grandstands" in ratings[0]["system"] and "solar panels" in ratings[0]["system"]


# ---- AI architect ------------------------------------------------------------------


def planned(client) -> dict:
    job = analyze(client, setup(client))
    # Open questions block approval B: answer them.
    answers = {q["id"]: "9" for q in job["analysis"]["questions"] if not q.get("answer")}
    if answers:
        client.post(f"/api/jobs/{job['id']}/answers", json=answers, headers=AUTH)
    job = client.get(f"/api/jobs/{job['id']}", headers=AUTH).json()
    r = client.post(
        f"/api/jobs/{job['id']}/gates/B/approve",
        json={"hash": job["pending_gate"]["hash"]},
        headers=AUTH,
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_architect_writes_fixes_and_checks_a_script(client, fake_ai, app_ctx):
    job = planned(client)
    jid = job["id"]
    obj = next(o for o in job["plan"]["objects"] if o["category"] == "buildings")
    url = f"/api/jobs/{jid}/design/{obj['item_id']}"

    est = client.get(url, headers=AUTH).json()
    assert 0 < est["estimate_usd"] <= est["max_usd"] <= est["budget_usd"] + 5
    fake_ai.calls.clear()

    # The first script is broken ("kaputt"): the sandbox error goes back to the model.
    r = client.post(
        url,
        json={"request": "kaputt: mach eine Glashalle mit Turm", "max_usd": est["max_usd"]},
        headers=AUTH,
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["summary"] == "Passt so." and out["cost_usd"] > 0
    formats = [c["format"] for c in fake_ai.calls]
    assert formats == ["ArchitectScript", "ArchitectScript", "ArchitectReview"]
    first = fake_ai.calls[0]["content"]
    assert any(b["type"] == "image" for b in first)  # aerial crop and current views
    assert "Current script:" in "".join(b.get("text", "") for b in first)
    assert "failed in the sandbox" in "".join(
        b.get("text", "") for b in fake_ai.calls[1]["content"]
    )
    assert "extrude(" in fake_ai.calls[0]["system"]  # tool reference

    job = out["job"]
    item = next(i for i in job["analysis"]["items"] if i["id"] == obj["item_id"])
    assert "cylinder(" in item["script"] and item["script_request"].startswith("kaputt")
    plan_obj = next(o for o in job["plan"]["objects"] if o["item_id"] == obj["item_id"])
    assert plan_obj["status"] == "ok" and plan_obj["triangles"] > obj["triangles"]
    assert "gate.c.custom_scripts" in keys(job["pending_gate"]["items"])

    # Back to the default script, free of charge.
    fake_ai.calls.clear()
    job = client.delete(url, headers=AUTH).json()
    item = next(i for i in job["analysis"]["items"] if i["id"] == obj["item_id"])
    assert item["script"] is None and not fake_ai.calls
    assert "gate.c.custom_scripts" not in keys(job["pending_gate"]["items"])


def test_architect_never_exceeds_the_confirmed_maximum(client, fake_ai):
    job = planned(client)
    obj = next(o for o in job["plan"]["objects"] if o["category"] == "buildings")
    fake_ai.calls.clear()
    r = client.post(
        f"/api/jobs/{job['id']}/design/{obj['item_id']}",
        json={"request": "einen Turm bitte", "max_usd": 0.0001},
        headers=AUTH,
    )
    assert r.status_code == 409 and r.json()["detail"]["key"] == "error.ai_budget_exhausted"
    assert not fake_ai.calls
