"""A build script written by hand: read the current one, try it (views, triangles,
the error with its line), put it on the object, go back to the default one."""

from __future__ import annotations

import base64

from conftest import AUTH
from test_analysis import planned

TOWER = """
part("Turm")
c = polygon_centroid(footprint)
extrude(footprint, 0, 12, mat="stone")
cylinder(c[0], c[1], 12, 2, 20, mat="roof_metal", radius_top=0.1)
"""


def test_writing_trying_and_using_an_own_script(client):
    job = planned(client)
    jid = job["id"]
    obj = next(
        o for o in job["plan"]["objects"] if o["category"] == "buildings" and o["status"] == "ok"
    )
    url = f"/api/jobs/{jid}/script/{obj['item_id']}"
    r = client.get(url, headers=AUTH)
    assert r.status_code == 200 and not r.json()["own"] and r.json()["script"]

    # trying it saves nothing and shows the result
    r = client.post(
        f"/api/jobs/{jid}/script-test/{obj['item_id']}", json={"script": TOWER}, headers=AUTH
    )
    assert r.status_code == 200, r.text
    test = r.json()
    assert test["ok"] and test["triangles"] > 0 and test["parts"] == ["Turm"]
    assert len(test["views"]) == 3 and base64.b64decode(test["views"][0])[:4] == b"\x89PNG"
    assert not client.get(url, headers=AUTH).json()["own"]

    # a broken script tells where
    r = client.post(
        f"/api/jobs/{jid}/script-test/{obj['item_id']}",
        json={"script": "extrude(footprint, 0)\n"},
        headers=AUTH,
    )
    assert r.status_code == 200 and not r.json()["ok"] and r.json()["error"]
    r = client.put(url, json={"script": "boom(1)"}, headers=AUTH)
    assert r.status_code == 409 and r.json()["detail"]["key"] == "error.script_failed"

    # used: the object is rebuilt with it
    r = client.put(url, json={"script": TOWER}, headers=AUTH)
    assert r.status_code == 200, r.text
    after = next(o for o in r.json()["plan"]["objects"] if o["item_id"] == obj["item_id"])
    assert after["triangles"] == test["triangles"]
    item = next(i for i in r.json()["analysis"]["items"] if i["id"] == obj["item_id"])
    assert item["script"] == TOWER and item["edited"]
    assert client.get(url, headers=AUTH).json() == {"script": TOWER, "own": True}

    # back to the default one
    r = client.delete(url, headers=AUTH)
    after = next(o for o in r.json()["plan"]["objects"] if o["item_id"] == obj["item_id"])
    assert after["triangles"] == obj["triangles"]
    assert not client.get(url, headers=AUTH).json()["own"]
