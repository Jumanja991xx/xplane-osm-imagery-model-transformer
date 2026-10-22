"""Building parts: the script names them, the preview can pick them, the user moves,
turns, resizes or hides one or several, takes it back, or asks the AI to change only
those."""

from __future__ import annotations

import pytest
from conftest import AUTH
from test_analysis import planned

from xpsb.build import parts
from xpsb.build.sandbox import ScriptInputs, run_script
from xpsb.scene import PartOp

SQUARE = [[-5, -5], [5, -5], [5, 5], [-5, 5]]
SCRIPT = """
part("Haus")
extrude(footprint, 0, 10)
part("Turm")
cylinder(0, 0, 10, 2, 25)
part()
for p in footprint:
    lamp_post(p[0] + 3, p[1])
box(20, 0, 0, 2, 2, 2)
"""


def _mesh():
    return run_script(SCRIPT, ScriptInputs(SQUARE, None, {}), 50_000)


def _box(mesh, pid):
    pts = [q for p in mesh.patches if p.part == pid for q in p.positions]
    xs, ys, zs = zip(*pts, strict=True)
    return min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)


def test_parts_are_named_by_the_script_or_after_the_tool():
    mesh = _mesh()
    assert [p.id for p in mesh.parts] == ["Haus", "Turm", "lamp_post-0", "box-0"]
    # four lamp posts in a row are one part, with their lights
    assert {li.part for li in mesh.lights} == {"lamp_post-0"}
    assert all(p.part for p in mesh.patches)
    assert mesh.parts[1].call.startswith("cylinder(0, 0, 10, 2, 25")
    with pytest.raises(Exception, match="part"):
        run_script("part(5)\nbox(0,0,0,1,1,1)", ScriptInputs(SQUARE, None, {}), 1000)


def test_moving_turning_resizing_and_hiding():
    mesh = _mesh()
    before = _box(mesh, "Turm")
    parts.apply_ops(mesh, [PartOp(parts=["Turm"], move=(3, 0, 0))])
    after = _box(mesh, "Turm")
    assert after[0] == pytest.approx(before[0] + 3) and after[4] == pytest.approx(before[4])
    # 50 % higher: grows upwards from its base
    parts.apply_ops(mesh, [PartOp(parts=["Turm"], scale=(1, 1, 1.5))])
    tall = _box(mesh, "Turm")
    assert tall[4] == pytest.approx(10) and tall[5] == pytest.approx(10 + 25 * 1.5)
    # turned 90° clockwise about its middle: the box at x=20 swings to the south
    parts.apply_ops(mesh, [PartOp(parts=["box-0", "Haus"], turn_deg=90)])
    x0, x1, y0, y1, *_ = _box(mesh, "box-0")
    assert y1 < 0 and x0 > 0
    n = len(mesh.patches)
    parts.apply_ops(mesh, [PartOp(parts=["lamp_post-0"], hide=True)])
    assert len(mesh.patches) < n and not mesh.lights
    # parts the script no longer makes are skipped
    parts.apply_ops(mesh, [PartOp(parts=["gone"], move=(1, 1, 1))])


def _preview(client, jid, stem):
    r = client.get(f"/api/jobs/{jid}/preview/{stem}.json", headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def test_changing_parts_in_the_preview(client, fake_ai):
    job = planned(client)
    jid = job["id"]
    # a building with a pitched roof: walls and roof are parts of their own
    obj = next(
        o for o in job["plan"]["objects"] if o["category"] == "buildings" and o["status"] == "ok"
    )
    r = client.patch(
        f"/api/jobs/{jid}/objects/{obj['item_id']}",
        json={"attrs": {"roof_shape": "gabled"}},
        headers=AUTH,
    )
    assert r.status_code == 200, r.text
    obj = next(o for o in r.json()["plan"]["objects"] if o["item_id"] == obj["item_id"])
    data = _preview(client, jid, obj["stem"])
    assert {"walls", "roof"} <= {p["id"] for p in data["parts"]}, data["parts"]
    walls = next(p for p in data["parts"] if p["id"] == "walls")
    assert walls["count"] > 0 and walls["start"] % 3 == 0
    tris = obj["triangles"]

    # Hide the roof, then make walls and roof together 2 m higher... undo, reset.
    url = f"/api/jobs/{jid}/parts/{obj['item_id']}"
    r = client.post(url, json={"parts": ["roof"], "hide": True}, headers=AUTH)
    assert r.status_code == 200, r.text
    after = next(o for o in r.json()["plan"]["objects"] if o["item_id"] == obj["item_id"])
    assert after["triangles"] < tris
    assert "roof" not in [p["id"] for p in _preview(client, jid, after["stem"])["parts"]]
    r = client.post(url, json={"parts": ["walls"], "move": [0, 0, 2]}, headers=AUTH)
    item = next(i for i in r.json()["analysis"]["items"] if i["id"] == obj["item_id"])
    assert len(item["part_ops"]) == 2 and item["edited"]
    r = client.delete(url, headers=AUTH)
    item = next(i for i in r.json()["analysis"]["items"] if i["id"] == obj["item_id"])
    assert [o["hide"] for o in item["part_ops"]] == [True]
    r = client.delete(url, params={"all": True}, headers=AUTH)
    after = next(o for o in r.json()["plan"]["objects"] if o["item_id"] == obj["item_id"])
    assert after["triangles"] == tris
    r = client.post(url, json={"parts": ["walls"], "scale": [20, 1, 1]}, headers=AUTH)
    assert r.status_code == 422

    # The AI changes only the picked part: it is told which one and where it is.
    est = client.get(f"/api/jobs/{jid}/design/{obj['item_id']}", headers=AUTH).json()
    fake_ai.calls.clear()
    r = client.post(
        f"/api/jobs/{jid}/design/{obj['item_id']}",
        json={"request": "steiler", "max_usd": est["max_usd"], "parts": ["roof"]},
        headers=AUTH,
    )
    assert r.status_code == 200, r.text
    text = "".join(b.get("text", "") for b in fake_ai.calls[0]["content"])
    assert "Change ONLY these parts" in text and '"roof" (made by ' in text
    item = next(i for i in r.json()["job"]["analysis"]["items"] if i["id"] == obj["item_id"])
    assert item["script_request"].endswith("steiler")  # the user's words, not the prompt
