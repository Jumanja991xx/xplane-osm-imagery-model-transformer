"""AI object search: finds what OSM does not know, as proposals for the user."""

from __future__ import annotations

from conftest import AUTH
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
    assert len(searches) == a["ai"]["tiles_searched"] == gate["gate.a.ai_detect"]["params"]["tiles"]
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
    confirmed = {
        i["label_key"]: i["count"] for i in job["plan"]["items"] if i["kind"] == "confirmed"
    }
    assert confirmed["category.grandstands"] == 2


def test_rejecting_a_proposal_deletes_it(client):
    job = analyze(client, setup(client, brief=DETECT))
    job = client.delete(f"/api/jobs/{job['id']}/items/ai/1", headers=AUTH).json()
    assert not [i for i in job["analysis"]["items"] if i["source"] == "ai"]


def test_search_respects_the_budget(client, fake_ai):
    budget = detect.estimate_tile_cost("claude-sonnet-5-5") * 1.2
    job = analyze(client, setup(client, brief={**DETECT, "ai_cost_limit_usd": round(budget, 4)}))
    searches = [c for c in fake_ai.calls if c["format"] == "AiFindings"]
    assert len(searches) == 1
    assert job["analysis"]["ai"]["limit_reached"] is True
    assert "analysis.ai_limit_reached" in keys(job["analysis"]["notes"])


def test_search_off_sends_no_tiles(client, fake_ai):
    job = setup(client, brief={**DETECT, "ai_detect": False})
    assert "gate.a.ai_detect" not in keys(job["pending_gate"]["items"])
    analyze(client, job)
    assert not [c for c in fake_ai.calls if c["format"] == "AiFindings"]
