"""Smoke test for a packaged backend: ready line, token auth, a real analysis
(OSM + imagery from the offline fixtures), a real build of the test scenery
(approvals A-C, DSFTool), clean exit.

Usage: python tools/smoke_backend.py <path to xpsb-backend(.exe)> [--resources-dir DIR]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "backend" / "tests" / "fixtures" / "speedway"

AOI = {
    "type": "Polygon",
    "coordinates": [
        [
            [8.563, 49.3265],
            [8.5685, 49.3265],
            [8.5685, 49.3292],
            [8.563, 49.3292],
            [8.563, 49.3265],
        ]
    ],
}


def call(base: str, method: str, path: str, body: object | None = None) -> dict:
    req = urllib.request.Request(
        base + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": "Bearer smoke-token",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def wait(base: str, job_id: str, state: str) -> dict:
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        job = call(base, "GET", f"/api/jobs/{job_id}")
        if job["state"] == state:
            return job
        if job.get("error"):
            raise AssertionError(f"job failed: {job['error']}")
        time.sleep(0.2)
    raise AssertionError(f"job never reached {state}")


def build_test_scenery(base: str) -> None:
    settings = call(base, "GET", "/api/settings")
    settings["imagery"] = {
        "source": "custom",
        "custom_url": "https://wms.example.test/dop",
        "custom_attribution": "Fixture",
        "custom_rights_confirmed": True,
    }
    call(base, "PUT", "/api/settings", settings)
    job = call(base, "POST", "/api/jobs", {"aoi": AOI})
    jid = job["id"]
    brief = {
        "object_categories": ["grandstands", "buildings"],
        "surface_types": ["grass"],
        "use_ai": False,
        "package_name": "Smoke Test",
    }
    job = call(base, "PUT", f"/api/jobs/{jid}/brief", brief)
    call(
        base,
        "POST",
        f"/api/jobs/{jid}/gates/A/approve",
        {"hash": job["pending_gate"]["hash"]},
    )
    job = wait(base, jid, "analyzed")
    analysis = job["analysis"]
    assert len(analysis["items"]) == 5, analysis["items"]
    assert analysis["imagery"] and analysis["imagery"]["width_px"] > 0, analysis
    print(
        "analysis:",
        len(analysis["items"]),
        "items, imagery",
        analysis["imagery"]["file"],
    )
    job = call(
        base,
        "POST",
        f"/api/jobs/{jid}/gates/B/approve",
        {"hash": job["pending_gate"]["hash"]},
    )
    assert not job["pending_gate"]["blocked"], job["pending_gate"]
    call(
        base,
        "POST",
        f"/api/jobs/{jid}/gates/C/approve",
        {"hash": job["pending_gate"]["hash"]},
    )
    job = wait(base, jid, "built")
    files = job["build"]["files"]
    assert any(f.endswith(".dsf") for f in files), files
    print("built:", job["build"]["dsftool_version"], files)


def main() -> None:
    exe = sys.argv[1]
    extra = sys.argv[2:]  # e.g. --resources-dir <dir with bin/DSFTool.exe>
    with tempfile.TemporaryDirectory() as data:
        # Offline answers for OSM and imagery: CI must not depend on public servers.
        env = {
            **os.environ,
            "XPSB_TOKEN": "smoke-token",
            "XPSB_FIXTURES_DIR": str(FIXTURES),
        }
        proc = subprocess.Popen(
            [
                exe,
                "--port",
                "0",
                "--watch-stdin",
                "--data-dir",
                data,
                "--log-level",
                "warning",
                *extra,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=env,
            text=True,
        )
        try:
            line = proc.stdout.readline()
            assert line.startswith("XPSB_READY "), f"no ready line: {line!r}"
            port = json.loads(line.removeprefix("XPSB_READY "))["port"]
            base = f"http://127.0.0.1:{port}"

            try:
                urllib.request.urlopen(base + "/api/health", timeout=10)
                raise AssertionError("request without token was accepted")
            except urllib.error.HTTPError as e:
                assert e.code == 401

            req = urllib.request.Request(
                base + "/api/system", headers={"Authorization": "Bearer smoke-token"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                system = json.loads(resp.read())
            print("system:", json.dumps(system, indent=1))
            assert system["dsftool"]["found"], "DSFTool not found by the packaged backend"
            build_test_scenery(base)

            proc.stdin.close()
            proc.wait(timeout=20)
            print("backend exited cleanly, code", proc.returncode)
        finally:
            if proc.poll() is None:
                proc.kill()
                raise SystemExit("backend did not exit after stdin closed")


if __name__ == "__main__":
    main()
