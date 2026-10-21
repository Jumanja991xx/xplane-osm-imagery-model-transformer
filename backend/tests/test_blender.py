"""Blender bridge: finding it, the batch run (fake Blender) and applying the result."""

from __future__ import annotations

import io
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from xpsb import blender
from xpsb.build.bake import bake
from xpsb.build.sandbox import ScriptInputs, run_script


def _mesh():
    src = 'facade(footprint, 0, 15, style="classic", storeys=3)\nflag(0, -20, 8)'
    fp = [[0, 0], [20, 0], [20, 10], [0, 10]]
    return run_script(src, ScriptInputs(footprint=fp, path=None, params={}), 40_000)


class FakeProc:
    """Stands in for the Blender process: bakes 'everything occluded' images."""

    def __init__(self, cmd, hang_after=None, **kw):
        assert "--python" in cmd and Path(cmd[cmd.index("--python") + 1]) == blender.SCRIPT
        assert "--factory-startup" in cmd
        out = ["XPSB_START 5.2.2\n", "XPSB_DEVICE OPTIX NVIDIA RTX\n"]
        jobs = Path(cmd[-1]).read_text(encoding="utf-8").splitlines()
        for n, line in enumerate(jobs):
            if hang_after is not None and n >= hang_after:
                break
            src, dst = line.split("\t")[:2]
            with np.load(src) as d:  # closed again: Windows cannot delete open files
                w, h = (int(v) for v in d["size"])
            Image.new("L", (w, h), 64).save(dst)
            out.append(f"XPSB_BEGIN {n}\n")
            out.append(f"XPSB_DONE {n}\n")
        self.stdout = io.StringIO("".join(out))
        self.returncode = 0

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


def test_one_run_for_all_objects_shades_only_own_texture_pieces(tmp_path):
    mesh = _mesh()
    baked = bake(mesh)
    texture = tmp_path / "obj.png"
    baked.image.save(texture)
    jobs = []
    for k in range(3):
        npz = tmp_path / ".blender" / f"o{k}.npz"
        assert blender.prepare(mesh, baked, npz)
        jobs.append((npz, tmp_path / ".blender" / f"o{k}_ao.png", tmp_path / f"o{k}_d.png"))
    calls, seen = [], []

    def popen(cmd, **kw):
        calls.append(cmd)
        return FakeProc(cmd, **kw)

    log_file = tmp_path / "blender.log"
    done = blender.bake_batch("blender", jobs, seen.append, popen=popen, log_file=log_file)
    assert done == {0, 1, 2} and len(calls) == 1  # Blender started once
    assert [s.phase for s in seen[:3]] == ["launch", "gpu", "gpu"]
    last = seen[-1]
    assert (last.done, last.total, last.device) == (3, 3, "OPTIX NVIDIA RTX")
    assert last.left is not None
    assert any(s.phase == "object" and s.current == 2 for s in seen)
    assert "XPSB_DONE 2" in log_file.read_text(encoding="utf-8")
    ao = blender.ao_from(*jobs[0][:2])
    assert ao.min() < 0.3 and ao.max() == 1.0  # dark only where receivers are
    before = np.asarray(Image.open(texture)).astype(int).sum()
    blender.shade_texture(texture, ao)
    assert np.asarray(Image.open(texture)).astype(int).sum() < before


def test_a_hanging_blender_keeps_what_was_done(tmp_path, monkeypatch):
    mesh = _mesh()
    baked = bake(mesh)
    jobs = []
    for k in range(3):
        npz = tmp_path / f"o{k}.npz"
        blender.prepare(mesh, baked, npz)
        jobs.append((npz, tmp_path / f"o{k}_ao.png", tmp_path / f"o{k}_d.png"))
    done = blender.bake_batch(
        "blender", jobs, popen=lambda cmd, **kw: FakeProc(cmd, hang_after=1, **kw)
    )
    assert done == {0}


@pytest.mark.skipif(sys.platform == "win32", reason="uses a shell script as fake Blender")
def test_find_ignores_missing_or_odd_programs(tmp_path):
    assert blender.find(str(tmp_path / "nope.exe")) is None
    fake = tmp_path / "blender"
    fake.write_text("#!/bin/sh\necho 'Blender 4.2.1'\n")
    fake.chmod(0o755)
    info = blender.find(str(fake))
    assert info is not None and info.version == "4.2.1"


def test_fine_detail_is_never_drawn_but_goes_to_blender(tmp_path):
    from xpsb.build.objexport import to_obj8, to_preview

    mesh = _mesh()
    assert mesh.bake_triangle_count > 500  # surrounds, sills, pediments, dentils
    baked = bake(mesh)
    obj = to_obj8(mesh, baked, "a.png", 3000, near_m=400)
    drawn = [p for p in mesh.patches if p.lod != "bake"]
    without = sum(len(p.tris) for p in drawn)
    assert len(to_preview(mesh, baked)["index"]) // 3 <= without + sum(
        len(p.tris) for p in drawn if p.two_sided
    )
    # nothing of it in X-Plane: exactly the vertices of the drawn parts are written
    assert len(obj.vertices) == sum(len(p.positions) * (1 + p.two_sided) for p in drawn)
    npz = tmp_path / "o.npz"
    assert blender.prepare(mesh, baked, npz)
    with np.load(npz) as d:
        assert len(d["high_tris"]) == mesh.bake_triangle_count
        w, h = (int(v) for v in d["detail_size"])
    # A baked relief (tilted normals in a corner) is blended into the normal map.
    relief = np.zeros((h, w, 3), dtype=np.uint8)
    relief[...] = (128, 128, 255)
    relief[: h // 4, : w // 4] = (200, 128, 220)
    detail = tmp_path / "detail.png"
    Image.fromarray(relief).save(detail)
    nml = tmp_path / "nml.png"
    baked.normal.save(nml)
    before = np.asarray(Image.open(nml)).copy()
    assert blender.detail_normals(npz, detail, nml)
    after = np.asarray(Image.open(nml))
    assert (after[..., 0] > before[..., 0] + 20).any()  # tilted where detail was hit
    assert (after[..., 3] == before[..., 3]).all()  # gloss untouched


def test_small_objects_first_and_the_user_can_stop(tmp_path):
    small = run_script(
        "extrude(footprint, 0, 3)",
        ScriptInputs(footprint=[[0, 0], [4, 0], [4, 4], [0, 4]], path=None, params={}),
        1000,
    )
    big = _mesh()
    jobs = []
    for name, mesh in (("big", big), ("small", small)):
        npz = tmp_path / f"{name}.npz"
        blender.prepare(mesh, bake(mesh), npz)
        jobs.append((npz, tmp_path / f"{name}_ao.png", tmp_path / f"{name}_d.png"))
    done = blender.bake_batch("blender", jobs, popen=lambda cmd, **kw: FakeProc(cmd, **kw))
    assert done == {0, 1}
    first = (tmp_path / "jobs.txt").read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith(str(jobs[1][0]))  # the small one is baked first
    for _, ao, _ in jobs:
        ao.unlink()
    stopped = blender.bake_batch(
        "blender", jobs, popen=lambda cmd, **kw: FakeProc(cmd, **kw), should_stop=lambda: True
    )
    assert stopped == set()


class HangingGpuSearch(FakeProc):
    """Blender whose graphics card search never answers (unless started with --cpu)."""

    def __init__(self, cmd, **kw):
        super().__init__(cmd, **kw)
        if "--cpu" not in cmd:
            self.killed = threading.Event()

            def lines():
                yield "XPSB_START 5.2.2\n"
                self.killed.wait(30)

            self.stdout = lines()

    def kill(self):
        if hasattr(self, "killed"):
            self.killed.set()


def test_a_hanging_graphics_card_search_starts_blender_again_on_the_cpu(tmp_path, monkeypatch):
    monkeypatch.setattr(blender, "GPU_TIMEOUT_S", 2)
    mesh = _mesh()
    npz = tmp_path / "o.npz"
    blender.prepare(mesh, bake(mesh), npz)
    jobs = [(npz, tmp_path / "o_ao.png", tmp_path / "o_d.png")]
    calls, seen = [], []

    def popen(cmd, **kw):
        calls.append(cmd)
        return HangingGpuSearch(cmd, **kw)

    exe = str(tmp_path / "blender-gpu-hangs")
    assert blender.bake_batch(exe, jobs, seen.append, popen=popen) == {0}
    assert len(calls) == 2 and "--cpu" in calls[1] and "--cpu" not in calls[0]
    assert "retry_cpu" in [s.phase for s in seen]
    # remembered: the next build skips the search right away
    blender.bake_batch(exe, jobs, popen=popen)
    assert "--cpu" in calls[2]


def test_self_test_times_the_steps_of_a_test_object(tmp_path):
    result = blender.self_test(
        "blender", tmp_path / "t", popen=lambda cmd, **kw: FakeProc(cmd, **kw)
    )
    assert result.ok and result.problem is None
    assert result.device == "OPTIX NVIDIA RTX"
    assert result.bake_s is not None and result.gpu_search_s is not None
    assert (tmp_path / "t" / "blender-test.log").is_file()
    cpu = blender.self_test(
        "blender", tmp_path / "t", cpu=True, popen=lambda cmd, **kw: FakeProc(cmd, **kw)
    )
    assert cpu.ok and cpu.gpu_search_s is None


def test_blender_gets_the_boot_script_and_no_input_handle(tmp_path):
    seen = {}

    def popen(cmd, **kw):
        seen.update(kw)
        return FakeProc(cmd, **kw)

    mesh = _mesh()
    npz = tmp_path / "o.npz"
    blender.prepare(mesh, bake(mesh), npz)
    blender.bake_batch("blender", [(npz, tmp_path / "a.png", tmp_path / "d.png")], popen=popen)
    boot = Path(seen["env"]["BLENDER_SYSTEM_SCRIPTS"]) / "startup" / "xpsb_boot.py"
    assert boot.is_file()
    assert seen["stdin"] is subprocess.DEVNULL


def test_boot_script_turns_off_wmi_on_windows(monkeypatch, capsys):
    import platform

    boot = blender.BOOT_SCRIPTS / "startup" / "xpsb_boot.py"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(platform, "_wmi", object(), raising=False)
    exec(compile(boot.read_text(encoding="utf-8"), str(boot), "exec"), {"__name__": "xpsb_boot"})
    assert platform._wmi is None
    assert "XPSB_BOOT" in capsys.readouterr().out
