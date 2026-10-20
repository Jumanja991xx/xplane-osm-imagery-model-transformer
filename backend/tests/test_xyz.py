"""User-supplied XYZ tile sources (Web Mercator) end up on the same lon/lat grid."""

from __future__ import annotations

from conftest import AUTH, HOCKENHEIM_AOI
from fake_ai import speedway
from PIL import Image
from test_analysis import ALL, CUSTOM_IMAGERY, analyze, setup

from xpsb.imagery import wms, xyz
from xpsb.imagery.sources import custom_source

XYZ_IMAGERY = {
    **CUSTOM_IMAGERY,
    "custom_type": "xyz",
    "custom_url": "https://tiles.example.test/{z}/{x}/{y}.jpg",
    "custom_max_zoom": 19,
}


def test_tile_url_placeholders():
    assert xyz.tile_url("https://t/{z}/{x}/{y}.png", 3, 1, 2) == "https://t/3/1/2.png"
    assert xyz.tile_url("https://t/{z}/{x}/{-y}.png", 3, 1, 2) == "https://t/3/1/5.png"


def test_zoom_respects_tile_limit():
    source = custom_source("https://t/{z}/{x}/{y}", None, "", "xyz", 21)
    small = xyz.choose_zoom(source, 49.3, 400, 300)
    large = xyz.choose_zoom(source, 49.3, 5000, 5000)
    assert 19 <= small <= 21 and large < small


def test_xyz_mosaic_lines_up_with_the_map(tmp_path):
    source = custom_source("https://t/{z}/{x}/{y}", None, "Test", "xyz", 18)
    info = wms.fetch_area_image(source, wms.Bounds(8.5630, 49.3265, 8.5685, 49.3292), tmp_path)
    assert info.layer == "xyz" and 0.3 < info.gsd_m < 0.5
    with Image.open(tmp_path / "aoi.jpg") as img:
        x0, y0, x1, y1 = speedway.UNMAPPED_GRANDSTAND_M
        c = speedway.pt((x0 + x1) / 2, (y0 + y1) / 2)
        px = (c["lon"] - info.west) / (info.east - info.west) * info.width_px
        py = (info.north - c["lat"]) / (info.north - info.south) * info.height_px
        r, g, b = img.getpixel((int(px), int(py)))
    assert b > 140 and r < 100  # the blue grandstand of the fixture image


def test_xyz_needs_placeholders_and_rights(client):
    job = setup(
        client,
        imagery={**XYZ_IMAGERY, "custom_url": "https://tiles.example.test/a.jpg"},
        brief={**ALL, "use_ai": False},
    )
    keys = {i["key"] for i in job["pending_gate"]["items"]}
    assert "gate.a.imagery.custom_missing" in keys

    job = setup(
        client,
        imagery={**XYZ_IMAGERY, "custom_rights_confirmed": False},
        brief={**ALL, "use_ai": False},
    )
    assert job["pending_gate"]["blocked"] is True

    job = analyze(client, setup(client, imagery=XYZ_IMAGERY, brief={**ALL, "use_ai": False}))
    assert job["analysis"]["imagery"]["layer"] == "xyz"
    assert job["analysis"]["imagery"]["source_name"] == "Eigene XYZ-Quelle"
    assert client.get(f"/api/jobs/{job['id']}/imagery", headers=AUTH).status_code == 200


def test_esri_worldwide_with_own_key_for_recognition_only(client, app_ctx):
    from xpsb.imagery.sources import ESRI_ACCOUNT, esri_source, resolve_source
    from xpsb.settings import ImagerySettings

    budapest = (19.0995, 47.5031)
    auto = ImagerySettings(source="auto")
    assert resolve_source(auto, *budapest) == (None, "imagery.not_covered")
    src, _ = resolve_source(auto, *budapest, esri_key="KEY/1+2")
    assert src.id == "esri" and not src.package_ok and src.url.endswith("token=KEY%2F1%2B2")
    # Bavaria keeps its open source even with a key.
    assert resolve_source(auto, 11.6, 48.2, esri_key="k")[0].id == "bayern_dop40"
    assert resolve_source(ImagerySettings(source="esri"), *budapest) == (
        None,
        "imagery.esri_no_key",
    )
    assert "{z}/{y}/{x}" in esri_source("k").url

    # Through the API: key in the credential store, never shown; gate A tells the limits.
    client.put("/api/secrets/esri", json={"token": "AAPK-test-key-123"}, headers=AUTH)
    assert client.get("/api/system", headers=AUTH).json()["has_esri_key"] is True
    assert app_ctx.secrets.get(ESRI_ACCOUNT) == "AAPK-test-key-123"
    settings = client.get("/api/settings", headers=AUTH).json()
    client.put("/api/settings", json={**settings, "imagery": {"source": "esri"}}, headers=AUTH)
    job = client.post("/api/jobs", json={"aoi": HOCKENHEIM_AOI}, headers=AUTH).json()
    brief = {
        "object_categories": ["buildings"],
        "surface_types": ["grass"],
        "use_ai": False,
        "package_name": "XPSB Esri",
    }
    job = client.put(f"/api/jobs/{job['id']}/brief", json=brief, headers=AUTH).json()
    items = {i["key"]: i for i in job["pending_gate"]["items"]}
    assert items["gate.a.imagery"]["params"]["source"].startswith("Esri World Imagery")
    assert "gate.a.imagery_analysis_only" in items
    assert "AAPK" not in str(job)


def test_tile_server_errors_are_readable_and_never_show_the_key(monkeypatch):
    import io
    import urllib.error

    from xpsb.imagery import wms, xyz
    from xpsb.imagery.sources import esri_source

    monkeypatch.delenv("XPSB_FIXTURES_DIR", raising=False)
    source = esri_source("SECRET-KEY-42")
    body = b'{"error":{"code":498,"message":"Invalid token.","details":[]}}'

    def json_200(url, timeout=60):
        return body, "application/json"

    monkeypatch.setattr(xyz, "_http_get", json_200)
    try:
        xyz._get_tile(source, 18, 1, 2)
    except wms.WmsError as exc:
        assert "Invalid token" in str(exc) and "SECRET" not in str(exc)
    else:
        raise AssertionError("expected an error")

    def http_403(req, timeout=60):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(body))

    monkeypatch.setattr(wms.urllib.request, "urlopen", http_403)
    try:
        wms._http_get(xyz.tile_url(source.url, 18, 1, 2))
    except wms.WmsError as exc:
        assert "HTTP 403" in str(exc) and "Invalid token" in str(exc)
        assert "SECRET" not in str(exc) and "token=" not in str(exc)
    else:
        raise AssertionError("expected an error")


def test_long_arcgis_key_is_stored_cleaned_and_checked(client, app_ctx, monkeypatch):
    from xpsb.imagery import wms, xyz
    from xpsb.imagery.sources import ESRI_ACCOUNT

    long_key = "AAPT" + "x" * 300 + "." + "y" * 300  # new ArcGIS keys are long
    pasted = long_key[:200] + "\n  " + long_key[200:] + " "  # copied with a line break
    r = client.put("/api/secrets/esri", json={"token": pasted}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json() == {"has_esri_key": True, "ok": True, "detail": None}  # fixture tile
    assert app_ctx.secrets.get(ESRI_ACCOUNT) == long_key
    assert long_key not in r.text

    def rejected(source, z, x, y):
        raise wms.WmsError("tile 3/4/2: [498] Invalid token.")

    monkeypatch.setattr(xyz, "_get_tile", rejected)
    r = client.post("/api/secrets/esri/check", headers=AUTH).json()
    assert r["ok"] is False and "Invalid token" in r["detail"]
    client.delete("/api/secrets/esri", headers=AUTH)
    assert client.post("/api/secrets/esri/check", headers=AUTH).json() == {
        "has_esri_key": False,
        "ok": None,
        "detail": None,
    }


def test_subdomains_rotate_and_are_required_for_s():
    from xpsb.imagery.sources import xyz_template_ok

    t = "https://{s}.tiles.example.test/{z}/{x}/{y}.png"
    subs = ["a", "b", "c"]
    urls = {xyz.tile_url(t, 5, x, 7, subs) for x in range(6)}
    assert {u.split(".")[0] for u in urls} == {"https://a", "https://b", "https://c"}
    assert xyz.tile_url(t, 5, 1, 7, subs) == xyz.tile_url(
        t, 5, 1, 7, subs
    )  # same tile, same server
    assert xyz_template_ok(t, subs) and not xyz_template_ok(t, [])
    assert not xyz_template_ok("https://t/{z}/{x}.png", [])


def _png(color=(10, 200, 30)) -> bytes:
    import io as _io

    buf = _io.BytesIO()
    Image.new("RGB", (256, 256), color).save(buf, "PNG")
    return buf.getvalue()


def test_map_background_comes_through_the_backend(client, monkeypatch):
    calls = []

    def fake_get(url, timeout=60):
        calls.append(url)
        return _png(), "image/png"

    monkeypatch.setattr(xyz, "_http_get", fake_get)
    # nothing configured: no background tiles
    assert client.get("/api/basemap/3/4/2", headers=AUTH).status_code == 404
    settings = client.get("/api/settings", headers=AUTH).json()
    settings["imagery"] = {
        **XYZ_IMAGERY,
        "custom_url": "https://{s}.tiles.example.test/{z}/{x}/{y}.png",
        "custom_subdomains": "mt0, mt1,mt2,mt3",
    }
    assert client.put("/api/settings", json=settings, headers=AUTH).status_code == 200
    assert client.get("/api/basemap/3/4/2").status_code == 401  # needs the app's token
    r = client.get("/api/basemap/3/4/2", headers=AUTH)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert calls == ["https://mt2.tiles.example.test/3/4/2.png"]
    client.get("/api/basemap/3/4/2", headers=AUTH)
    assert len(calls) == 1  # second time from the disk cache
    assert client.get("/api/basemap/3/9/2", headers=AUTH).status_code == 502  # outside the world

    # the user did not confirm the rights, or does not want it on the map: nothing
    settings["imagery"]["custom_on_map"] = False
    client.put("/api/settings", json=settings, headers=AUTH)
    assert client.get("/api/basemap/3/4/2", headers=AUTH).status_code == 404
    settings["imagery"].update(custom_on_map=True, custom_rights_confirmed=False)
    client.put("/api/settings", json=settings, headers=AUTH)
    assert client.get("/api/basemap/3/4/2", headers=AUTH).status_code == 404


def test_a_server_that_answers_with_text_is_an_error(client, monkeypatch):
    monkeypatch.setattr(xyz, "_http_get", lambda url, timeout=60: (b"<html>no</html>", "text/html"))
    settings = client.get("/api/settings", headers=AUTH).json()
    settings["imagery"] = XYZ_IMAGERY
    client.put("/api/settings", json=settings, headers=AUTH)
    r = client.get("/api/basemap/3/4/2", headers=AUTH)
    assert r.status_code == 502 and r.json()["detail"]["key"] == "error.basemap"


def test_an_own_source_can_be_tried_before_saving(client, monkeypatch):
    calls = []

    def fake_get(url, timeout=60):
        calls.append(url)
        return _png(), "image/png"

    monkeypatch.setattr(xyz, "_http_get", fake_get)
    imagery = {
        **XYZ_IMAGERY,
        "custom_url": "https://{s}.example.test/ortho/{z}/{y}/{x}.jpeg",
        "custom_subdomains": "maps,maps1",
        "custom_max_zoom": 19,
    }
    r = client.post(
        "/api/imagery/test", json={"imagery": imagery, "lon": 16.37, "lat": 48.21}, headers=AUTH
    )
    assert r.json() == {"ok": True, "detail": None}
    z, y, x = (int(v) for v in calls[0].removesuffix(".jpeg").split("/")[-3:])
    assert z == 15 and 17000 < x < 18500 and 11000 < y < 11500  # Vienna, rows and columns kept
    bad = {**imagery, "custom_subdomains": ""}
    r = client.post("/api/imagery/test", json={"imagery": bad}, headers=AUTH)
    assert r.json()["ok"] is False and "subdomains" in r.json()["detail"]


def test_open_national_imagery_is_chosen_by_the_real_borders():
    from xpsb.imagery.sources import candidates
    from xpsb.settings import ImagerySettings

    auto = ImagerySettings()

    def ids(lon, lat, key=None):
        return [s.id for s in candidates(auto, lon, lat, key)]

    assert ids(11.57, 48.14) == ["bayern_dop40"]  # Munich
    assert ids(13.04, 47.80) == ["basemap_at"]  # Salzburg: inside the old Bavaria box
    assert ids(8.54, 47.37) == ["swissimage"]  # Zurich
    assert ids(4.90, 52.37) == ["pdok_nl"]
    assert ids(2.35, 48.86) == ["ign_fr"]
    assert ids(-16.5, 28.3) == ["pnoa_es"]  # Tenerife
    assert ids(-74.0, 40.7) == ["usgs_naip"]
    assert ids(-157.86, 21.3) == ["usgs_naip"]  # Honolulu
    assert ids(139.7, 35.68) == ["gsi_jp"]
    assert ids(19.04, 47.50) == []  # Budapest: nothing open built in
    assert ids(19.04, 47.50, "KEY")[-1] == "esri"
    assert ids(2.35, 48.86, "KEY") == ["ign_fr", "esri"]  # Esri as the fallback


def test_japan_is_for_recognition_only():
    from xpsb.imagery.sources import BUILTIN_SOURCES

    by_id = {s.id: s for s in BUILTIN_SOURCES}
    assert by_id["gsi_jp"].package_ok is False
    assert all(s.package_ok for s in BUILTIN_SOURCES if s.id != "gsi_jp")
    assert all(s.attribution and s.license for s in BUILTIN_SOURCES)


def test_when_a_server_fails_the_next_source_is_tried(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from xpsb.jobs import pipeline
    from xpsb.settings import Settings

    tried = []

    def fetch(source, area, out_dir, progress=None):
        tried.append(source.id)
        if source.id == "ign_fr":
            raise wms.WmsError("data.geopf.fr: HTTP 503")
        return SimpleNamespace(source_id=source.id)

    monkeypatch.setattr(pipeline.wms, "fetch_area_image", fetch)
    ring = [(2.34, 48.85), (2.36, 48.85), (2.36, 48.87), (2.34, 48.85)]
    job = SimpleNamespace(aoi_center=(2.35, 48.86), aoi=SimpleNamespace(ring=ring))
    deps = SimpleNamespace(settings=Settings(), esri_key="KEY", job_dir=tmp_path)
    log = SimpleNamespace(publish_threadsafe=lambda **kw: None)
    notes = []
    info = pipeline._imagery_stage(job, deps, log, notes)
    assert tried == ["ign_fr", "esri"] and info.source_id == "esri"
    assert notes[0].key == "imagery.failed" and "503" in notes[0].params["detail"]
