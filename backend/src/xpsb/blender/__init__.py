"""Blender as an optional finishing tool (the user installs it; it is not shipped).

Only fixed scripts of this folder run in Blender - never code from the AI. What
it adds today: ambient occlusion baked into each object's texture, the soft
shading in corners and under ledges that makes hand-made sceneries look solid.
Without Blender everything else works the same.
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from xpsb.build.bake import BakedMesh
from xpsb.build.mesh import Mesh

SCRIPT = Path(__file__).with_name("ao_bake.py")
OBJECT_TIMEOUT_S = 240  # no object finished for this long: Blender is stopped
SAMPLES = 16
DISTANCE_M = 4.0
MAX_BAKE_PX = 1024  # shading is soft: a smaller bake is enough and much faster
MAX_DETAIL_PX = 2048  # fine relief needs the texture's own resolution
DETAIL_STRENGTH = 0.75  # how strongly the baked relief tilts the surface
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


def prepare(mesh: Mesh, baked: BakedMesh, path: Path) -> bool:
    """Writes what Blender needs for one object (geometry and atlas layout).
    False if nothing of it can take shading."""
    w, h = baked.image.size
    scale = min(1.0, MAX_BAKE_PX / max(w, h))
    positions, uvs, rec, occ = [], [], [], []
    high_pos, high_tris = [], []
    for patch, puv, receive in zip(mesh.patches, baked.uvs, _receivers(mesh), strict=True):
        if patch.lod == "bake":  # fine detail: baked onto the walls, never drawn
            base = len(high_pos)
            high_pos += patch.positions
            high_tris += [(base + a, base + b, base + c) for a, b, c in patch.tris]
            continue
        if patch.anim is not None or patch.lod == "far":  # far stand-ins overlap the walls
            continue
        base = len(positions)
        positions += patch.positions
        uvs += puv
        tris = [(base + a, base + b, base + c) for a, b, c in patch.tris]
        (rec if receive else occ).extend(tris)
    if not rec:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    extra = {}
    if high_tris:
        extra = {
            "high_positions": np.array(high_pos, dtype=np.float32),
            "high_tris": np.array(high_tris, dtype=np.int32),
        }
    np.savez(
        path,
        **extra,
        positions=np.array(positions, dtype=np.float32),
        uvs=np.array(uvs, dtype=np.float32),
        receivers=np.array(rec, dtype=np.int32),
        occluders=np.array(occ, dtype=np.int32).reshape(-1, 3),
        size=np.array([max(64, int(w * scale)), max(64, int(h * scale))]),
        detail_size=np.array(
            [
                max(64, int(w * min(1.0, MAX_DETAIL_PX / max(w, h)))),
                max(64, int(h * min(1.0, MAX_DETAIL_PX / max(w, h)))),
            ]
        ),
        atlas=np.array([w, h]),
        samples=np.array(SAMPLES),
        distance=np.array(DISTANCE_M),
    )
    return True


def bake_batch(
    exe: str,
    jobs: list[tuple[Path, Path, Path]],
    on_progress: Callable[[int, int, str, float | None], None] = lambda *a: None,
    popen=subprocess.Popen,
) -> set[int]:
    """One Blender run for all objects: (input .npz, AO .png, normal .png) per object.
    Returns the indices that were baked. Stops Blender if it hangs. on_progress gets
    (done, total, device, seconds left or None) - estimated from the objects so far."""
    if not jobs:
        return set()
    list_file = jobs[0][0].parent / "jobs.txt"
    list_file.write_text("".join("\t".join(map(str, job)) + "\n" for job in jobs), encoding="utf-8")
    cmd = [
        exe,
        "--background",
        "--factory-startup",
        "--python-exit-code",
        "1",
        "--python",
        str(SCRIPT),
        "--",
        str(list_file),
    ]
    try:
        proc = popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        return set()
    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    device, done, failed = "CPU", set(), set()
    started = time.monotonic()

    def report() -> None:
        nonlocal started
        left = None
        if done:
            per = (time.monotonic() - started) / len(done)
            left = per * (len(jobs) - len(done))
        on_progress(len(done), len(jobs), device, left)

    report()
    while True:
        try:
            line = lines.get(timeout=OBJECT_TIMEOUT_S)
        except queue.Empty:
            proc.kill()
            break
        if line is None:
            break
        if line.startswith("XPSB_DEVICE "):
            device = line.split(" ", 1)[1].strip()
            started = time.monotonic()  # Blender's own start-up does not count per object
            report()
        elif line.startswith("XPSB_FAILED "):
            failed.add(int(line.split()[1]))
        elif line.startswith("XPSB_DONE "):
            done.add(int(line.split()[1]))
            report()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
    return {i for i in done - failed if jobs[i][1].is_file()}


def ao_from(npz: Path, png: Path) -> np.ndarray:
    """The baked shading in the atlas layout (1 = open), only where receivers are."""
    with np.load(npz) as d:
        w, h = (int(v) for v in d["atlas"])
        uv = d["uvs"].astype(np.float64)
        rec = d["receivers"]
    with Image.open(png) as img:
        ao = np.asarray(img.convert("L").resize((w, h), Image.Resampling.BILINEAR))
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    for a, b, c in rec:
        draw.polygon(
            [(uv[i, 0] * w, (1.0 - uv[i, 1]) * h) for i in (a, b, c)], fill=255, outline=255
        )
    m = np.asarray(mask) > 0
    out = np.ones((h, w), dtype=np.float32)
    out[m] = ao[m] / 255.0
    return out


def shade_texture(texture: Path, ao: np.ndarray) -> None:
    """Darkens a colour texture file by the occlusion (the night texture stays)."""
    with Image.open(texture) as img:
        img.load()
        mode = img.mode
        rgb = np.asarray(img).astype(np.float32)
    # Spots hidden behind other parts are fully dark; keep them from bleeding in.
    shade = 1.0 - STRENGTH * (1.0 - np.clip(ao, 0.25, 1.0))
    rgb[..., :3] *= shade[..., None]
    Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), mode).save(texture)


def detail_normals(npz: Path, png: Path, normal_texture: Path) -> bool:
    """Blends the baked relief of the fine detail into the object's normal map (only
    where detail was hit; the gloss in the alpha channel stays). False if none."""
    if not png.is_file():
        return False
    with np.load(npz) as d:
        w, h = (int(v) for v in d["atlas"])
    with Image.open(png) as img:
        baked = np.asarray(img.convert("RGB").resize((w, h), Image.Resampling.BILINEAR))
    baked = baked.astype(np.float32) / 127.5 - 1.0
    baked[..., :2] *= DETAIL_STRENGTH
    hit = (np.abs(baked[..., 0]) > 0.04) | (np.abs(baked[..., 1]) > 0.04)
    if not hit.any():
        return False
    with Image.open(normal_texture) as img:
        img.load()
        nml = np.asarray(img.convert("RGBA")).astype(np.float32)
    base = nml[..., :3] / 127.5 - 1.0
    # "whiteout" blend: the detail's tilt added to the material's own relief
    mixed = np.stack(
        [base[..., 0] + baked[..., 0], base[..., 1] + baked[..., 1], base[..., 2] * baked[..., 2]],
        axis=-1,
    )
    mixed /= np.linalg.norm(mixed, axis=-1, keepdims=True).clip(1e-6)
    nml[hit, :3] = (mixed[hit] + 1.0) * 127.5
    Image.fromarray(np.clip(nml, 0, 255).astype(np.uint8), "RGBA").save(normal_texture)
    return True
