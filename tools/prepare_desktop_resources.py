"""Copies the packaged backend, DSFTool and license notices into the Tauri
resource folder (apps/desktop/src-tauri/resources), which the installer ships.

Run after:
    python tools/build_dsftool.py --fetch-zlib
    cd backend && uv run --group build pyinstaller packaging/xpsb-backend.spec --noconfirm \
        --distpath build/dist --workpath build/work
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / "apps" / "desktop" / "src-tauri" / "resources"
EXE = ".exe" if sys.platform == "win32" else ""


def replace_dir(src: Path, dst: Path) -> None:
    if not src.is_dir():
        raise SystemExit(f"missing: {src}")
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    (dst / ".gitkeep").touch()


def main() -> None:
    replace_dir(ROOT / "backend" / "build" / "dist" / "xpsb-backend", RESOURCES / "backend")

    bin_dir = RESOURCES / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    dsftool = ROOT / "tools" / "bin" / f"DSFTool{EXE}"
    if not dsftool.is_file():
        raise SystemExit(f"missing: {dsftool} (run tools/build_dsftool.py)")
    shutil.copy2(dsftool, bin_dir / dsftool.name)

    licenses = RESOURCES / "bin" / "LICENSES"
    if licenses.exists():
        shutil.rmtree(licenses)
    shutil.copytree(ROOT / "third_party" / "LICENSES", licenses)
    shutil.copy2(ROOT / "LICENSE", RESOURCES / "bin" / "LICENSE.txt")
    print(f"resources ready in {RESOURCES}")


if __name__ == "__main__":
    main()
