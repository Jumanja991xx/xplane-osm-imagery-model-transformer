"""AI object search: finds what OSM does not know, as proposals for the user."""

from __future__ import annotations

from conftest import AUTH, wait_for_state
from fake_ai import speedway
from PIL import Image
from shapely.geometry import Polygon, shape
from test_analysis import ALL, analyze, keys, setup

from xpsb.ai import detect
from xpsb.imagery import wms
from xpsb.imagery.sources import BAYERN_DOP40

DETECT = {**ALL, "ai_detect": True}


def true_grandstand_lonlat() -> Polygon:
    return Polygon([(p["lon"], p["lat"]) for p in speedway.rect(*speedway.UNMAPPED_GRANDSTAND_M)])


def iou(a, b) -> float:
    return a.intersection(b).area / a.union(b).area


def test_tiles_cover_the_whole_image(tmp_path):
    area = wms.Bounds(8.5630, 49.3265, 8.5685, 49.3292)
    info = wms.fetch_area_image(BAYERN_DOP40, area, tmp_path)
    tiles = detect.plan_tiles(info)
    assert max(t.x1 for t in tiles) == info.width_px and max(t.y1 for t in tiles) == info.height_px
    assert min(t.x0 for t in tiles) == 0 and min(t.y0 for t in tiles) == 0
    w_m, h_m = info.width_px * info.gsd_m, info.height_px * info.gsd_m
    assert len(tiles) == detect.count_tiles(w_m, h_m)


def test_snapping_corrects_a_sloppy_outline(tmp_path):
    area = wms.Bounds(8.5630, 49.3265, 8.5685, 49.3292)
    info = wms.fetch_area_image(BAYERN_DOP40, area, tmp_path)
    with Image.open(tmp_path / "aoi.jpg") as img:
        image = img.convert("RGB")
    truth = true_grandstand_lonlat()

    def to_px(poly):
        return Polygon([detect._lonlat_to_px(info, x, y) for x, y in poly.exterior.coords])

    x0, y0, x1, y1 = speedway.UNMAPPED_GRANDSTAND_M
    sloppy = Polygon([(p["lon"], p["lat"]) for p in speedway.rect(x0 - 5, y0 + 3, x1 + 4, y1 + 4)])
    snapped = detect.snap_to_image(image, to_px(sloppy))
    assert iou(to_px(sloppy), to_px(truth)) < 0.85
    assert iou(snapped, to_px(truth)) > 0.9


def test_search_finds_unmapped_grandstand_as_proposal(client, fake_ai):
    job = setup(client, brief=DETECT)
    gate = {i["key"]: i for i in job["pending_gate"]["items"]}
    assert gate["gate.a.ai_detect"]["params"]["tiles"] >= 1
    assert gate["gate.a.ai_detect"]["params"]["estimate_usd"] > 0

    job = analyze(client, job)
    a = job["analysis"]
    searches = [c for c in fake_ai.calls if c["format"] == "AiFindings"]
    params = gate["gate.a.ai_detect"]["params"]
    assert (
        len(searches)
        == a["ai"]["tiles_searched"]
        == params["tiles"] * params["passes"]
        == (params["tiles"] * detect.PASSES)
    )
    # Second look carries the instruction; the search thinks a bit more than the rating.
    assert any(detect.SECOND_PASS in c["content"][0]["text"] for c in searches)
    assert {c["effort"] for c in searches} == {detect.SEARCH_EFFORT}
    assert "race/running track (sport: speedway)" in searches[0]["system"]
    with Image.open(
        __import__("io").BytesIO(
            __import__("base64").b64decode(searches[0]["content"][1]["source"]["data"])
        )
    ) as tile:
        assert max(tile.size) == detect.TILE_SEND_PX

    proposals = [i for i in a["items"] if i["proposal"]]
    assert a["ai"]["proposals"] == len(proposals) == 1  # duplicates and junk dropped
    p = proposals[0]
    assert p["id"] == "ai/1" and p["source"] == "ai" and p["category"] == "grandstands"
    assert p["included"] is False and p["attrs"]["height_m"] == 7 and p["name"] == "Gegengerade"
    assert iou(shape(p["geometry"]), true_grandstand_lonlat()) > 0.85
    assert len(a["items"]) == 11

    # Not in the plan until accepted.
    gate_b = {i["key"]: i for i in job["pending_gate"]["items"]}
    assert gate_b["gate.b.open_proposals"]["params"]["count"] == 1
    assert gate_b["gate.b.counts"]["params"]["excluded"] == 0
    objects_before = gate_b["gate.b.counts"]["params"]["objects"]

    job = client.patch(
        f"/api/jobs/{job['id']}/items/ai/1", json={"proposal": False}, headers=AUTH
    ).json()
    accepted = next(i for i in job["analysis"]["items"] if i["id"] == "ai/1")
    assert accepted["proposal"] is False and accepted["included"] is True
    gate_b = {i["key"]: i for i in job["pending_gate"]["items"]}
    assert "gate.b.open_proposals" not in gate_b
    assert gate_b["gate.b.counts"]["params"]["objects"] == objects_before + 1
    # Accepted stays accepted.
    job = client.patch(
        f"/api/jobs/{job['id']}/items/ai/1", json={"proposal": True}, headers=AUTH
    ).json()
    assert next(i for i in job["analysis"]["items"] if i["id"] == "ai/1")["proposal"] is False

    job = client.post(
        f"/api/jobs/{job['id']}/answers",
        json={q["id"]: q["suggested"] for q in job["analysis"]["questions"]},
        headers=AUTH,
    ).json()
    job = client.post(
        f"/api/jobs/{job['id']}/gates/B/approve",
        json={"hash": job["pending_gate"]["hash"]},
        headers=AUTH,
    ).json()
    job = wait_for_state(client, job["id"], {"planned"})  # after the automatic design
    confirmed = {
        i["label_key"]: i["count"] for i in job["plan"]["items"] if i["kind"] == "confirmed"
    }
    assert confirmed["category.grandstands"] == 2


def test_rejecting_a_proposal_deletes_it(client):
    job = analyze(client, setup(client, brief=DETECT))
    job = client.delete(f"/api/jobs/{job['id']}/items/ai/1", headers=AUTH).json()
    assert not [i for i in job["analysis"]["items"] if i["source"] == "ai"]


def test_search_respects_the_budget(client, fake_ai):
    # Room for about one call (estimates carry a safety margin for parallel calls).
    budget = detect.estimate_tile_cost("claude-sonnet-5-5") * 1.6
    job = analyze(client, setup(client, brief={**DETECT, "ai_cost_limit_usd": round(budget, 4)}))
    searches = [c for c in fake_ai.calls if c["format"] == "AiFindings"]
    tiles = len(detect.plan_tiles(wms_info(client, job)))
    assert 1 <= len(searches) < tiles * detect.PASSES
    assert job["analysis"]["ai"]["limit_reached"] is True
    assert job["analysis"]["ai"]["cost_usd"] <= budget
    assert "analysis.ai_limit_reached" in keys(job["analysis"]["notes"])


def wms_info(client, job):
    from xpsb.imagery.wms import ImageryInfo

    return ImageryInfo(**job["analysis"]["imagery"])


def test_search_off_sends_no_tiles(client, fake_ai):
    job = setup(client, brief={**DETECT, "ai_detect": False})
    assert "gate.a.ai_detect" not in keys(job["pending_gate"]["items"])
    analyze(client, job)
    assert not [c for c in fake_ai.calls if c["format"] == "AiFindings"]


def test_find_similar_asks_with_estimate_first(client, fake_ai, app_ctx):
    job = analyze(client, setup(client))  # no object search during the analysis
    assert not [i for i in job["analysis"]["items"] if i["source"] == "ai"]
    jid = job["id"]
    spent_before = job["analysis"]["ai"]["cost_usd"]

    calls_before = len(fake_ai.calls)
    est = client.get(f"/api/jobs/{jid}/similar/way/103", headers=AUTH)
    assert est.status_code == 200, est.text
    est = est.json()
    assert est["tiles"] >= 1 and 0 < est["estimate_usd"] <= est["budget_usd"]
    assert len(fake_ai.calls) == calls_before  # the estimate itself sends nothing

    r = client.post(
        f"/api/jobs/{jid}/similar/way/103", json={"max_usd": est["estimate_usd"]}, headers=AUTH
    )
    assert r.status_code == 200, r.text
    job = r.json()
    searches = [c for c in fake_ai.calls[calls_before:] if c["format"] == "AiFindings"]
    assert len(searches) == est["tiles"]
    assert searches[0]["content"][0]["text"] == "EXAMPLE:"
    assert 'category "grandstands"' in searches[0]["system"]
    found = [i for i in job["analysis"]["items"] if i["source"] == "ai"]
    assert len(found) == 1 and found[0]["proposal"] and found[0]["category"] == "grandstands"
    assert iou(shape(found[0]["geometry"]), true_grandstand_lonlat()) > 0.85
    assert job["analysis"]["ai"]["cost_usd"] > spent_before
    assert job["analysis"]["ai"]["proposals"] == 1

    # Running it again finds nothing new (already proposed).
    job = client.post(f"/api/jobs/{jid}/similar/way/103", json={"max_usd": 1}, headers=AUTH).json()
    assert len([i for i in job["analysis"]["items"] if i["source"] == "ai"]) == 1

    # Only objects can serve as example.
    r = client.get(f"/api/jobs/{jid}/similar/way/102", headers=AUTH)
    assert r.status_code == 409 and r.json()["detail"]["key"] == "error.similar_objects_only"


def test_bulk_accept_with_new_category(client):
    job = analyze(client, setup(client, brief=DETECT))
    jid = job["id"]
    job = client.patch(
        f"/api/jobs/{jid}/items/ai/1",
        json={"proposal": False, "category": "canopies"},
        headers=AUTH,
    ).json()
    item = next(i for i in job["analysis"]["items"] if i["id"] == "ai/1")
    assert item["category"] == "canopies" and item["included"] and not item["proposal"]


def test_finds_bring_roof_and_seats(client):
    job = analyze(client, setup(client, brief=DETECT))
    p = next(i for i in job["analysis"]["items"] if i["proposal"])
    assert p["attrs"]["covered"] is True and p["attrs"]["material"] == "roof:solar"
    assert p["attrs"]["seat_color"] == "#c03030"


def test_small_area_goes_as_one_picture(tmp_path):
    area = wms.Bounds(8.5650, 49.3270, 8.5665, 49.3282)  # ~110 x 130 m
    info = wms.fetch_area_image(BAYERN_DOP40, area, tmp_path)
    tiles = detect.plan_tiles(info)
    assert len(tiles) == 1 and tiles[0].overview
    assert detect.count_tiles(170, 190) == 1


def test_similar_takes_over_the_example_attributes(client):
    job = analyze(client, setup(client))
    jid = job["id"]
    client.patch(
        f"/api/jobs/{jid}/items/way/103",
        json={"attrs": {"height_m": 11, "covered": True, "material": "roof:solar"}},
        headers=AUTH,
    )
    est = client.get(f"/api/jobs/{jid}/similar/way/103", headers=AUTH).json()
    job = client.post(
        f"/api/jobs/{jid}/similar/way/103", json={"max_usd": est["estimate_usd"]}, headers=AUTH
    ).json()
    found = next(i for i in job["analysis"]["items"] if i["source"] == "ai")
    assert found["attrs"]["height_m"] == 11 and found["attrs"]["covered"] is True


def test_memory_brings_back_earlier_decisions(client, fake_ai):
    first = analyze(client, setup(client, brief=DETECT))
    jid = first["id"]
    client.patch(f"/api/jobs/{jid}/items/ai/1", json={"proposal": False}, headers=AUTH)
    client.patch(f"/api/jobs/{jid}/items/way/103", json={"attrs": {"height_m": 14}}, headers=AUTH)
    job = client.get(f"/api/jobs/{jid}", headers=AUTH).json()
    job = client.post(
        f"/api/jobs/{jid}/answers",
        json={q["id"]: q["suggested"] for q in job["analysis"]["questions"]},
        headers=AUTH,
    ).json()
    assert (
        client.post(
            f"/api/jobs/{jid}/gates/B/approve",
            json={"hash": job["pending_gate"]["hash"]},
            headers=AUTH,
        ).status_code
        == 200
    )

    calls_before = len(fake_ai.calls)
    second = analyze(client, setup(client, brief=DETECT))
    items = {i["id"]: i for i in second["analysis"]["items"]}
    assert items["way/103"]["attrs"]["height_m"] == 14 and items["way/103"]["remembered"]
    mem = [i for i in items.values() if i["source"] == "memory"]
    assert len(mem) == 1 and mem[0]["category"] == "grandstands" and mem[0]["included"]
    # nothing new to propose: the stand is known from the first job
    assert not [i for i in items.values() if i["proposal"]]
    assert "analysis.remembered" in keys(second["analysis"]["notes"])
    rated = [c for c in fake_ai.calls[calls_before:] if c["format"] == "AiBatchResult"]
    rated_ids = {
        b["text"].split("\n")[0] for c in rated for b in c["content"] if b["type"] == "text"
    }
    assert "item_id: way/103" not in rated_ids
