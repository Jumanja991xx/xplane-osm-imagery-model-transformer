"""Locations of runtime data (never inside the read-only install directory)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import platformdirs

from xpsb import APP_DIR_NAME


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @property
    def settings_file(self) -> Path:
        return self.root / "settings.json"

    @property
    def jobs(self) -> Path:
        return self.root / "jobs"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    def ensure(self) -> AppPaths:
        for p in (self.root, self.jobs, self.cache, self.logs, self.backups):
            p.mkdir(parents=True, exist_ok=True)
        return self


def resolve_paths(override: Path | None = None) -> AppPaths:
    """Windows: %APPDATA%\\XPlaneAISceneryBuilder. Overridable for dev and tests."""
    if override is None and os.environ.get("XPSB_DATA_DIR"):
        override = Path(os.environ["XPSB_DATA_DIR"])
    root = override or Path(platformdirs.user_data_dir(APP_DIR_NAME, appauthor=False, roaming=True))
    return AppPaths(root.resolve()).ensure()
