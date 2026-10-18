"""Locating and running Laminar's DSFTool.

DSFTool is a C++ command line tool; on Windows its argv uses the ANSI code page,
so paths with characters like 'ü' (C:\\Users\\Jürgen) can break it. We always run
it inside an ASCII-only work directory with relative file names and copy the
result to its real destination afterwards.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from xpsb.procutil import run_quiet

EXE_NAME = "DSFTool.exe" if sys.platform == "win32" else "DSFTool"


class DsfToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class DsfToolInfo:
    path: str
    version: str


def candidate_paths(configured: str | None, resources_dir: Path | None) -> list[Path]:
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    if os.environ.get("XPSB_DSFTOOL"):
        candidates.append(Path(os.environ["XPSB_DSFTOOL"]))
    if resources_dir:
        candidates.append(resources_dir / "bin" / EXE_NAME)
    if not getattr(sys, "frozen", False):
        # Development checkout: tools/bin is filled by tools/build_dsftool.py
        repo_root = Path(__file__).resolve().parents[4]
        candidates.append(repo_root / "tools" / "bin" / EXE_NAME)
    found = shutil.which(EXE_NAME)
    if found:
        candidates.append(Path(found))
    return candidates


def locate(configured: str | None, resources_dir: Path | None) -> Path | None:
    for c in candidate_paths(configured, resources_dir):
        if c.is_file():
            return c
    return None


def version(exe: Path) -> str:
    try:
        res = run_quiet([str(exe), "--version"], timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DsfToolError(f"cannot run DSFTool: {exc}") from exc
    text = (res.stdout + res.stderr).strip()
    if "DSFTool" not in text:
        raise DsfToolError(f"unexpected DSFTool output: {text[:200]}")
    return text.splitlines()[0]


def ascii_work_root() -> Path:
    """A writable directory whose path is pure ASCII."""
    tmp = Path(tempfile.gettempdir())
    if str(tmp).isascii():
        return tmp / "xpsb-work"
    if sys.platform == "win32":
        return (
            Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
            / "XPlaneAISceneryBuilder"
            / "work"
        )
    return Path("/tmp/xpsb-work")


def _run_in_ascii_dir(exe: Path, mode: str, src: Path, dst: Path, timeout: float) -> str:
    work = ascii_work_root() / uuid.uuid4().hex
    work.mkdir(parents=True)
    try:
        src_name = "in" + src.suffix.lower()
        dst_name = "out" + dst.suffix.lower()
        shutil.copy2(src, work / src_name)
        try:
            res = run_quiet([str(exe), mode, src_name, dst_name], timeout=timeout, cwd=str(work))
        except subprocess.TimeoutExpired as exc:
            raise DsfToolError(f"DSFTool timed out after {timeout:.0f}s") from exc
        output = (res.stdout + "\n" + res.stderr).strip()
        produced = work / dst_name
        if res.returncode != 0 or not produced.is_file():
            raise DsfToolError(f"DSFTool failed (exit {res.returncode}): {output[-2000:]}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(produced, dst)
        return output
    finally:
        shutil.rmtree(work, ignore_errors=True)


def text2dsf(exe: Path, text_file: Path, dsf_file: Path, timeout: float = 900) -> str:
    return _run_in_ascii_dir(exe, "--text2dsf", text_file, dsf_file, timeout)


def dsf2text(exe: Path, dsf_file: Path, text_file: Path, timeout: float = 900) -> str:
    return _run_in_ascii_dir(exe, "--dsf2text", dsf_file, text_file, timeout)
