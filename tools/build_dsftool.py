"""Build DSFTool from Laminar Research's xptools sources (MIT) at a pinned commit.

Usage:  python tools/build_dsftool.py [--fetch-zlib]

Result: tools/bin/DSFTool(.exe). Works on Linux (tests) and Windows (release, MSVC).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

XPTOOLS_REPO = "https://github.com/X-Plane/xptools.git"
# Pinned for reproducible builds; bump deliberately and re-run the format tests.
XPTOOLS_COMMIT = "0137e80cdf03e8bb4cb58edb081f4ddf4a32c042"
SPARSE_DIRS = [
    "src/DSF",
    "src/DSFTools",
    "src/Utils",
    "src/GUI",
    "src/Obj",
    "src/lzma19",
    "src/XPTools",
]

HERE = Path(__file__).resolve().parent
SRC_DIR = HERE / "dsftool" / "xptools-src"
BUILD_DIR = HERE / "dsftool" / "build"
BIN_DIR = HERE / "bin"


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def checkout() -> None:
    if (SRC_DIR / ".git").exists():
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=SRC_DIR, capture_output=True, text=True
        ).stdout.strip()
        if head == XPTOOLS_COMMIT:
            run(["git", "sparse-checkout", "set", *SPARSE_DIRS], cwd=SRC_DIR)
            return
        shutil.rmtree(SRC_DIR)
    SRC_DIR.mkdir(parents=True)
    run(["git", "init", "-q"], cwd=SRC_DIR)
    run(["git", "remote", "add", "origin", XPTOOLS_REPO], cwd=SRC_DIR)
    run(["git", "sparse-checkout", "set", *SPARSE_DIRS], cwd=SRC_DIR)
    run(
        ["git", "fetch", "--depth", "1", "--filter=blob:none", "origin", XPTOOLS_COMMIT],
        cwd=SRC_DIR,
    )
    run(["git", "checkout", "-q", "FETCH_HEAD"], cwd=SRC_DIR)


def build(fetch_zlib: bool) -> Path:
    configure = [
        "cmake",
        "-S",
        str(HERE / "dsftool"),
        "-B",
        str(BUILD_DIR),
        f"-DXPTOOLS_DIR={SRC_DIR.as_posix()}",
        "-DCMAKE_BUILD_TYPE=Release",
    ]
    if fetch_zlib:
        configure.append("-DXPSB_FETCH_ZLIB=ON")
    run(configure)
    run(["cmake", "--build", str(BUILD_DIR), "--config", "Release", "--parallel"])

    exe = "DSFTool.exe" if sys.platform == "win32" else "DSFTool"
    for candidate in (BUILD_DIR / "Release" / exe, BUILD_DIR / exe):
        if candidate.exists():
            BIN_DIR.mkdir(exist_ok=True)
            target = BIN_DIR / exe
            shutil.copy2(candidate, target)
            return target
    raise SystemExit(f"Build finished but {exe} was not found in {BUILD_DIR}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch-zlib", action="store_true", help="build zlib from source")
    args = parser.parse_args()
    checkout()
    target = build(args.fetch_zlib)
    print(f"DSFTool ready: {target}")


if __name__ == "__main__":
    main()
