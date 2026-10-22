from __future__ import annotations

import time
from pathlib import Path

import pytest
from fake_ai import FakeAi
from fastapi.testclient import TestClient

from xpsb.api.app import AppContext, create_app
from xpsb.jobs.manager import JobManager
from xpsb.paths import resolve_paths
from xpsb.secrets import MemorySecretStore
from xpsb.settings import SettingsStore
from xpsb.xplane import dsftool

FIXTURES = Path(__file__).parent / "fixtures" / "speedway"

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

# ~400 m x 300 m around the Hockenheimring infield.
HOCKENHEIM_AOI = {
    "type": "Polygon",
    "coordinates": [
        [
            [8.5630, 49.3265],
            [8.5685, 49.3265],
            [8.5685, 49.3292],
            [8.5630, 49.3292],
            [8.5630, 49.3265],
        ]
    ],
}


class XPlaneState:
    running: bool | None = False


@pytest.fixture
def xplane_state() -> XPlaneState:
    return XPlaneState()


@pytest.fixture
def fake_ai() -> FakeAi:
    return FakeAi()


@pytest.fixture(autouse=True)
def offline_fixtures(monkeypatch: pytest.MonkeyPatch) -> None:
    # OSM and imagery answers come from files; tests never touch the network.
    monkeypatch.setenv("XPSB_FIXTURES_DIR", str(FIXTURES))
    # Objects built one after another (fast to start); test_parallel.py covers the rest.
    monkeypatch.setenv("XPSB_BUILD_WORKERS", "1")


@pytest.fixture
def app_ctx(tmp_path: Path, xplane_state: XPlaneState, fake_ai: FakeAi) -> AppContext:
    paths = resolve_paths(tmp_path / "data")
    settings = SettingsStore(paths.settings_file)
    secrets = MemorySecretStore()
    jobs = JobManager(
        paths,
        settings,
        secrets,
        resources_dir=None,
        xplane_running=lambda: xplane_state.running,
        ai_transport_factory=fake_ai.factory,
    )
    return AppContext(
        token=TOKEN, paths=paths, settings=settings, secrets=secrets, jobs=jobs, resources_dir=None
    )


@pytest.fixture
def client(app_ctx: AppContext):
    with TestClient(create_app(app_ctx)) as c:
        yield c


@pytest.fixture
def fake_xplane(tmp_path: Path) -> Path:
    root = tmp_path / "X-Plane 12"
    (root / "Custom Scenery").mkdir(parents=True)
    (root / "Resources").mkdir()
    return root


@pytest.fixture
def dsftool_exe() -> Path:
    exe = dsftool.locate(None, None)
    if exe is None:
        pytest.skip("DSFTool not built (python tools/build_dsftool.py)")
    return exe


def wait_for_state(client: TestClient, job_id: str, states: set[str], timeout: float = 60) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}", headers=AUTH).json()
        if job["state"] in states:
            return job
        time.sleep(0.05)
    raise AssertionError(
        f"job did not reach {states}, last state {job['state']}: {job.get('error')}"
    )
