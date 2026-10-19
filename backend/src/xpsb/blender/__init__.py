"""Blender as an optional finishing tool (the user installs it; it is not shipped).

Only fixed scripts of this folder run in Blender - never code from the AI. What
it adds today: ambient occlusion baked into each object's texture, the soft
shading in corners and under ledges that makes hand-made sceneries look solid.
Without Blender everything else works the same.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from xpsb.build.bake import BakedMesh
from xpsb.build.mesh import Mesh

SCRIPT = Path(__file__).with_name("ao_bake.py")
TIMEOUT_S = 240
SAMPLES = 24
DISTANCE_M = 4.0
MAX_BAKE_PX = 2048
STRENGTH = 0.7  # how dark a fully occluded spot gets (1 = black)


@dataclass
class BlenderInfo:
    path: str
    version: str


def _candidates() -> list[Path]:
    found: list[Path] = []
    if sys.platform == "win32":
        for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")):
            if base:
                found += sorted(Path(base, "Blender Foundation").glob("Blender*/blender.exe"))
        steam = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        found.append(steam / "Steam/steamapps/common/Blender/blender.exe")
    elif sys.platform == "darwin":
        found.append(Path("/Applications/Blender.app/Contents/MacOS/Blender"))
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if d:
            found.append(Path(d) / ("blender.exe" if sys.platform == "win32" else "blender"))
    # newest version first (folder names like "Blender 4.2")
    return sorted(
        {p for p in found if p.is_file()},
        key=lambda p: [int(n) for n in re.findall(r"\d+", str(p.parent.name))] or [0],
        reverse=True,
    )


def version(exe: Path) -> str | None:
    try:
        out = subprocess.run(
            [str(exe), "--version"],
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"Blender\s+(\d+\.\d+(?:\.\d+)?)", out)
    return m.group(1) if m else None


def find(configured: str | None = None) -> BlenderInfo | None:
    """The user's Blender (set in the settings, or installed in the usual places)."""
    paths = [Path(configured)] if configured else _candidates()
    for exe in paths:
        v = version(exe) if exe.is_file() else None
        if v and int(v.split(".")[0]) >= 3:
            return BlenderInfo(str(exe), v)
    return None


def _receivers(mesh: Mesh) -> list[bool]:
    """Patches with a piece of texture of their own (shared trim/window pieces and
    animated frames would get the shading of many places at once)."""
    return [not p.shared_uv and p.anim is None and p.lod != "far" for p in mesh.patches]


def bake_ao(exe: str, mesh: Mesh, baked: BakedMesh, run=subprocess.run) -> np.ndarray | None:
    """Ambient occlusion in the atlas layout (h x w, 1 = open) where the receivers
    are, else 1. None if Blender failed (the object stays as it is)."""
    w, h = baked.image.size
    scale = min(1.0, MAX_BAKE_PX / max(w, h))
    bw, bh = max(64, int(w * scale)), max(64, int(h * scale))
    positions, uvs, rec, occ = [], [], [], []
    for patch, puv, receive in zip(mesh.patches, baked.uvs, _receivers(mesh), strict=True):
        if patch.anim is not None or patch.lod == "far":  # far stand-ins overlap the walls
            continue
        base = len(positions)
        positions += patch.positions
        uvs += puv
        tris = [(base + a, base + b, base + c) for a, b, c in patch.tris]
        (rec if receive else occ).extend(tris)
    if not rec:
        return None
    with tempfile.TemporaryDirectory(prefix="xpsb_blender_") as tmp:
        src, dst = Path(tmp, "in.npz"), Path(tmp, "ao.png")
        np.savez(
            src,
            positions=np.array(positions, dtype=np.float32),
            uvs=np.array(uvs, dtype=np.float32),
            receivers=np.array(rec, dtype=np.int32),
            occluders=np.array(occ, dtype=np.int32).reshape(-1, 3),
            size=np.array([bw, bh]),
            samples=np.array(SAMPLES),
            distance=np.array(DISTANCE_M),
        )
        try:
            proc = run(
                [
                    exe,
                    "--background",
                    "--factory-startup",
                    "--python-exit-code",
                    "1",
                    "--python",
                    str(SCRIPT),
                    "--",
                    str(src),
                    str(dst),
                ],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_S,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0 or not dst.is_file():
            return None
        with Image.open(dst) as img:
            ao = np.asarray(img.convert("L").resize((w, h), Image.Resampling.BILINEAR))
    # Only where receivers are (Blender's bake margin spills a little around them).
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    uv = np.array(uvs, dtype=np.float64)
    for a, b, c in rec:
        draw.polygon(
            [(uv[i, 0] * w, (1.0 - uv[i, 1]) * h) for i in (a, b, c)], fill=255, outline=255
        )
    m = np.asarray(mask) > 0
    out = np.ones((h, w), dtype=np.float32)
    out[m] = ao[m] / 255.0
    return out


def apply_ao(baked: BakedMesh, ao: np.ndarray) -> None:
    """Darkens the colour texture by the occlusion (night texture stays as it is)."""
    rgb = np.asarray(baked.image).astype(np.float32)
    # Spots hidden behind other parts are fully dark; keep them from bleeding in.
    shade = 1.0 - STRENGTH * (1.0 - np.clip(ao, 0.25, 1.0))
    rgb[..., :3] *= shade[..., None]
    mode = baked.image.mode
    baked.image = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), mode)
