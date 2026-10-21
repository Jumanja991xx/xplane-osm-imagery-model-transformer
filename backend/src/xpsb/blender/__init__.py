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
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from xpsb.build.bake import BakedMesh
from xpsb.build.mesh import Mesh

SCRIPT = Path(__file__).with_name("ao_bake.py")
# Blender is stopped when no object finishes for this long - or for 4x the time
# the current object should take judging by the ones before (big stadiums on a
# slow CPU can take minutes; stopping them would lose everything).
OBJECT_TIMEOUT_S = 600
LAUNCH_TIMEOUT_S = 180  # Blender itself does not answer
GPU_TIMEOUT_S = 90  # the graphics card search hangs: start again on the CPU
_GPU_SEARCH_HANGS: set[str] = set()
SAMPLES = 16
DISTANCE_M = 4.0
MAX_BAKE_PX = 512  # shading is soft and blurry: a small bake is enough and much faster
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


def _work(npz: Path) -> float:
    """Rough amount of work for one object (pixels to bake and geometry)."""
    try:
        with np.load(npz) as d:
            w, h = (int(v) for v in d["size"])
            tris = len(d["receivers"]) + len(d["occluders"])
            if "high_tris" in d:
                dw, dh = (int(v) for v in d["detail_size"])
                tris += len(d["high_tris"])
                w, h = w + dw // 2, h + dh // 2  # the relief bake is cheaper per pixel
    except (OSError, KeyError, ValueError):
        return 1.0
    return w * h * (1.0 + tris / 50_000)


@dataclass
class Status:
    """What Blender is doing right now (for the progress line)."""

    phase: str  # launch | gpu | object | relief | retry_cpu
    done: int
    total: int
    device: str = ""
    current: int = 0  # 1-based number of the object being baked
    left: float | None = None  # seconds


def _start(exe: str, list_file: Path, cpu: bool, popen):
    cmd = [
        exe,
        "--background",
        "--factory-startup",
        "--python-exit-code",
        "1",
        "--python",
        str(SCRIPT),
        "--",
        *(["--cpu"] if cpu else []),
        str(list_file),
    ]
    proc = popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",  # Blender's messages must never stop the reading
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        try:
            for line in proc.stdout:
                lines.put(line)
        finally:
            lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    return proc, lines


def bake_batch(
    exe: str,
    jobs: list[tuple[Path, Path, Path]],
    on_progress: Callable[[Status], None] = lambda s: None,
    popen=subprocess.Popen,
    should_stop: Callable[[], bool] = lambda: False,
    log: list[str] | None = None,
    log_file: Path | None = None,
    cpu: bool = False,
) -> set[int]:
    """One Blender run for all objects: (input .npz, AO .png, normal .png) per object.
    Returns the indices that were baked. Small objects go first, so the time left
    (estimated from the work done so far) is known after a few seconds. Stops
    Blender if it hangs or `should_stop()` says so (the rest stays unshaded); if
    only the search for a graphics card hangs, Blender starts again without it.
    Blender's output goes into `log` and, line by line as it comes, `log_file`
    (to look at while it runs, and for the reason when nothing worked)."""
    log = log if log is not None else []
    if not jobs:
        return set()
    sink = None
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        sink = log_file.open("w", encoding="utf-8", buffering=1)

    def note(line: str) -> None:
        log.append(line)
        if sink is not None:
            sink.write(line + "\n")

    work = [_work(job[0]) for job in jobs]
    order = sorted(range(len(jobs)), key=lambda i: work[i])  # line number -> job index
    list_file = jobs[0][0].parent / "jobs.txt"
    list_file.write_text(
        "".join("\t".join(map(str, jobs[i])) + "\n" for i in order), encoding="utf-8"
    )
    done: set[int] = set()
    failed: set[int] = set()
    try:
        cpu = cpu or exe in _GPU_SEARCH_HANGS
        while True:
            retry = _run(
                exe,
                list_file,
                cpu,
                popen,
                jobs,
                work,
                order,
                done,
                failed,
                on_progress,
                should_stop,
                note,
            )
            if not retry:
                break
            _GPU_SEARCH_HANGS.add(exe)  # not again in this session
            cpu = True
    finally:
        if sink is not None:
            sink.close()
    return {i for i in done - failed if jobs[i][1].is_file()}


def _run(
    exe, list_file, cpu, popen, jobs, work, order, done, failed, on_progress, should_stop, note
) -> bool:
    """One Blender process. True: start again without the graphics card search."""
    total = len(jobs)
    try:
        proc, lines = _start(exe, list_file, cpu, popen)
    except OSError as exc:
        note(f"Blender could not be started: {exc}")
        return False
    status = Status("launch", len(done), total)
    on_progress(replace(status))
    started = time.monotonic()
    quiet = 0.0
    skip = len(done)  # objects already baked by a run before (the list starts again)

    def left() -> float | None:
        new = [i for i in done if i not in before]
        if not new:
            return None
        spent = sum(work[i] for i in new)
        rest = sum(work[i] for i in range(total) if i not in done)
        return (time.monotonic() - started) / max(spent, 1e-9) * rest

    before = set(done)

    def limit() -> float:
        if status.phase == "launch":
            return LAUNCH_TIMEOUT_S
        if status.phase == "gpu":
            return GPU_TIMEOUT_S
        new = [i for i in done if i not in before]
        if not new or len(done) >= total:
            return OBJECT_TIMEOUT_S
        rate = (time.monotonic() - started) / max(sum(work[i] for i in new), 1e-9)
        current = work[order[min(len(done), total - 1)]]
        return max(OBJECT_TIMEOUT_S, 4 * rate * current)

    retry = False
    while True:
        if should_stop():
            note("XPSB_STOPPED by the user")
            proc.kill()
            break
        try:
            line = lines.get(timeout=1.0)
        except queue.Empty:
            quiet += 1.0
            if quiet >= limit():
                if status.phase == "gpu" and not cpu:
                    note(f"XPSB_TIMEOUT the graphics card search did not answer for {quiet:.0f} s")
                    retry = True
                    on_progress(Status("retry_cpu", len(done), total))
                else:
                    note(f"XPSB_TIMEOUT no answer from Blender for {quiet:.0f} s ({status.phase})")
                proc.kill()
                break
            continue
        if line is None:
            break
        note(line.rstrip("\n"))
        # Our markers may share a line with Blender's own output.
        m = re.search(r"XPSB_(START|DEVICE|BEGIN|RELIEF|DONE|FAILED) ?(.*)", line)
        if not m:
            continue  # Blender's own messages do not count as progress
        quiet = 0.0
        kind, rest = m.group(1), m.group(2).strip()
        n = int(rest.split()[0]) if kind in ("BEGIN", "RELIEF", "DONE", "FAILED") else -1
        if kind == "START":
            status.phase = "gpu"
        elif kind == "DEVICE":
            status.device = rest
            started = time.monotonic()  # Blender's own start-up does not count per object
        elif n < skip:
            continue  # baked in the run before
        elif kind in ("BEGIN", "RELIEF"):
            status.phase = "object" if kind == "BEGIN" else "relief"
            status.current = n + 1
        elif kind == "FAILED":
            failed.add(order[n])
            continue
        elif kind == "DONE":
            done.add(order[n])
        status.done, status.left = len(done), left()
        on_progress(replace(status))
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
    if proc.returncode not in (0, None) and not retry:
        note(f"XPSB_EXIT {proc.returncode}")
    return retry


def problem(log: list[str]) -> str:
    """The most telling line of a Blender run that went wrong (for the user)."""
    for key in ("XPSB_FAILED", "Error", "Traceback", "XPSB_TIMEOUT", "XPSB_EXIT"):
        for line in reversed(log):
            if key in line:
                return line.strip()[:300]
    return (log[-1].strip() if log else "no output")[:300]


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


@dataclass
class SelfTest:
    ok: bool
    device: str  # what Blender used, with the reason when it is the CPU
    start_s: float | None  # until Blender runs our script (its own start-up)
    gpu_search_s: float | None  # time from Blender's start to the device decision
    bake_s: float | None  # one palace-like test object (walls, windows, relief)
    total_s: float
    gpu_search_hung: bool
    problem: str | None


def self_test(exe: str, work_dir: Path, cpu: bool = False, popen=subprocess.Popen) -> SelfTest:
    """Bakes one small test object the way a build does and times the steps, so the
    user sees whether Blender works on this PC (and how fast) without a build."""
    from xpsb.build.bake import bake
    from xpsb.build.sandbox import ScriptInputs, run_script
    from xpsb.build.scripts import DEFAULT_SCRIPTS

    shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True)
    footprint = [[0, 0], [24, 0], [24, 14], [0, 14]]
    params = {"building": "palace", "height": 16, "levels": 3, "roof_shape": "hipped"}
    mesh = run_script(
        DEFAULT_SCRIPTS["buildings"],
        ScriptInputs(
            footprint=footprint,
            path=None,
            params={**params, "detail": "medium", "tags": {"historic": "yes"}},
        ),
        40_000,
    )
    npz = work_dir / "test.npz"
    prepare(mesh, bake(mesh, 1024, 24.0), npz)
    marks: dict[str, float] = {}
    hung = False
    t0 = time.monotonic()
    device = ""

    def seen(s: Status) -> None:
        nonlocal device, hung
        now = time.monotonic() - t0
        if s.phase == "retry_cpu":
            hung = True
            marks.pop("start", None)
        if s.phase == "gpu" and "start" not in marks:
            marks["start"] = now
        if s.device and "device" not in marks:
            marks["device"] = now
        if s.phase == "retry_cpu":
            marks.pop("device", None)
        if s.done and "done" not in marks:
            marks["done"] = now
        device = s.device or device

    log: list[str] = []
    done = bake_batch(
        exe,
        [(npz, work_dir / "test_ao.png", work_dir / "test_detail.png")],
        seen,
        popen=popen,
        log=log,
        log_file=work_dir / "blender-test.log",
        cpu=cpu,
    )
    total = time.monotonic() - t0
    gpu = marks["device"] - marks["start"] if "device" in marks and "start" in marks else None
    baked = marks["done"] - marks["device"] if "done" in marks and "device" in marks else None
    ok = 0 in done
    return SelfTest(
        ok=ok,
        device=device,
        start_s=marks.get("start"),
        gpu_search_s=None if cpu else gpu,
        bake_s=baked,
        total_s=total,
        gpu_search_hung=hung,
        problem=None if ok else problem(log),
    )
