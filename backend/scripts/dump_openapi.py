"""Writes the backend's OpenAPI schema; the frontend generates its types from it.

Usage: uv run python scripts/dump_openapi.py ../apps/frontend/src/api/openapi.json
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from xpsb.api.app import AppContext, create_app
from xpsb.jobs.manager import JobManager
from xpsb.paths import resolve_paths
from xpsb.secrets import MemorySecretStore
from xpsb.settings import SettingsStore


def main() -> None:
    out = Path(sys.argv[1])
    with tempfile.TemporaryDirectory() as tmp:
        paths = resolve_paths(Path(tmp))
        settings = SettingsStore(paths.settings_file)
        secrets = MemorySecretStore()
        jobs = JobManager(paths, settings, secrets, None)
        app = create_app(AppContext("x", paths, settings, secrets, jobs, None))
        out.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
