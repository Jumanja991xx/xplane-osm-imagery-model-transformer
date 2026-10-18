"""The process protocol the desktop shell relies on."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request

import pytest
from conftest import HOCKENHEIM_AOI
from websockets.sync.client import connect


def test_ready_line_token_and_stdin_watchdog(tmp_path):
    env = {
        **os.environ,
        "XPSB_TOKEN": "sidecar-token",
        "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
    }
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "xpsb",
            "--port",
            "0",
            "--data-dir",
            str(tmp_path),
            "--watch-stdin",
            "--log-level",
            "warning",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        text=True,
    )
    try:
        line = proc.stdout.readline()
        assert line.startswith("XPSB_READY ")
        ready = json.loads(line.removeprefix("XPSB_READY "))
        assert "token" not in ready  # token came from the environment, never echoed

        req = urllib.request.Request(
            f"http://127.0.0.1:{ready['port']}/api/health",
            headers={"Authorization": "Bearer sidecar-token"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert json.loads(resp.read())["status"] == "ok"

        # The real server (not the test client) must speak WebSocket.
        create = urllib.request.Request(
            f"http://127.0.0.1:{ready['port']}/api/jobs",
            data=json.dumps({"aoi": HOCKENHEIM_AOI}).encode(),
            headers={"Authorization": "Bearer sidecar-token", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(create, timeout=10) as resp:
            job_id = json.loads(resp.read())["id"]
        url = f"ws://127.0.0.1:{ready['port']}/ws/jobs/{job_id}?token=sidecar-token"
        with connect(url, open_timeout=10) as ws:
            first = json.loads(ws.recv(timeout=10))
        assert first["type"] == "state" and first["state"] == "draft"

        proc.stdin.close()  # the desktop app went away
        assert proc.wait(timeout=15) is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            pytest.fail("backend did not exit after stdin closed")
