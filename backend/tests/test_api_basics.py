from __future__ import annotations

from conftest import AUTH


def test_requests_without_valid_token_are_rejected(client):
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/health", headers=AUTH).json()["status"] == "ok"


def test_settings_roundtrip(client):
    s = client.get("/api/settings", headers=AUTH).json()
    assert s["language"] == "de"
    assert s["update_check"] is False  # no network traffic without consent
    s["language"] = "en"
    s["ai"]["model"] = "claude-sonnet-5-5"
    assert client.put("/api/settings", json=s, headers=AUTH).status_code == 200
    again = client.get("/api/settings", headers=AUTH).json()
    assert again["language"] == "en"
    assert again["ai"]["model"] == "claude-sonnet-5-5"


def test_unknown_model_is_rejected(client):
    s = client.get("/api/settings", headers=AUTH).json()
    s["ai"]["model"] = "gpt-whatever"
    assert client.put("/api/settings", json=s, headers=AUTH).status_code == 422


def test_api_key_is_stored_but_never_returned(client):
    secret = "sk-ant-test-1234567890"
    r = client.put("/api/secrets/anthropic", json={"api_key": f"  {secret}  "}, headers=AUTH)
    assert r.json() == {"has_api_key": True}
    system = client.get("/api/system", headers=AUTH)
    assert system.json()["has_api_key"] is True
    assert secret not in system.text
    assert secret not in client.get("/api/settings", headers=AUTH).text
    assert client.delete("/api/secrets/anthropic", headers=AUTH).json() == {"has_api_key": False}


def test_invalid_api_key_is_rejected(client):
    r = client.put("/api/secrets/anthropic", json={"api_key": "has space"}, headers=AUTH)
    assert r.status_code == 422


def test_xplane_check(client, fake_xplane, tmp_path):
    ok = client.post("/api/xplane/check", json={"path": str(fake_xplane)}, headers=AUTH).json()
    assert ok["valid"] is True
    bad = client.post("/api/xplane/check", json={"path": str(tmp_path)}, headers=AUTH).json()
    assert bad["valid"] is False
    assert "custom_scenery_missing" in bad["problems"]
