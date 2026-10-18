"""Find and validate X-Plane 12 installations.

X-Plane's installer records install folders (one per line) in
x-plane_install_12.txt. The detected folders are only suggestions: the user
always confirms the path before the app uses it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel

from xpsb.procutil import run_quiet

INSTALL_LIST_NAME = "x-plane_install_12.txt"


class XPlaneCheck(BaseModel):
    path: str
    valid: bool
    problems: list[str] = []


def install_list_files() -> list[Path]:
    home = Path.home()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        return [Path(base) / INSTALL_LIST_NAME] if base else []
    if sys.platform == "darwin":
        return [home / "Library" / "Preferences" / INSTALL_LIST_NAME]
    return [home / ".x-plane" / INSTALL_LIST_NAME]


def check_xplane_path(path: str) -> XPlaneCheck:
    root = Path(path)
    problems: list[str] = []
    if not root.is_dir():
        problems.append("not_a_directory")
    else:
        if not (root / "Custom Scenery").is_dir():
            problems.append("custom_scenery_missing")
        if not (root / "Resources").is_dir():
            problems.append("resources_missing")
    return XPlaneCheck(path=str(root), valid=not problems, problems=problems)


def detect_xplane_installs() -> list[XPlaneCheck]:
    seen: set[str] = set()
    results: list[XPlaneCheck] = []
    for list_file in install_list_files():
        try:
            lines = list_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            candidate = line.strip().rstrip("/\\")
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            results.append(check_xplane_path(candidate))
    return results


def is_xplane_running() -> bool | None:
    """True/False if known, None if it cannot be determined."""
    try:
        if sys.platform == "win32":
            out = run_quiet(
                ["tasklist", "/FI", "IMAGENAME eq X-Plane.exe", "/NH", "/FO", "CSV"], timeout=10
            )
            return "x-plane.exe" in out.stdout.lower()
        out = run_quiet(["pgrep", "-f", "X-Plane"], timeout=10)
        return out.returncode == 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
