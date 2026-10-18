"""Subprocess helpers: never open a console window on Windows."""

from __future__ import annotations

import subprocess
import sys
from typing import Any


def no_window_kwargs() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def run_quiet(
    cmd: list[str], timeout: float, cwd: str | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        **no_window_kwargs(),
    )
