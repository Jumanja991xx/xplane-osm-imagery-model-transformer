"""Blender bridge: finding it, the batch run (fake Blender) and applying the result."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from xpsb import blender
from xpsb.build.bake import bake
from xpsb.build.sandbox import ScriptInputs, run_script


def _mesh():
    src = 'facade(footprint, 0, 12, style="classic", storeys=2)\nflag(0, -20, 8)'
    fp = [[0, 0], [20, 0], [20, 10], [0, 10]]
    return run_script(src, ScriptInputs(footprint=fp, path=None, params={}), 40_000)


class FakeProc:
    """Stands in for the Blender process: bakes 'everything occluded' images."""

    def __init__(self, cmd, hang_after=None, **kw):
        assert "--python" in cmd and Path(cmd[cmd.index("--python") + 1]) == blender.SCRIPT
        assert "--factory-startup" in cmd
        out = ["XPSB_DEVICE OPTIX NVIDIA RTX\n"]
        jobs = Path(cmd[-1]).read_text(encoding="utf-8").splitlines()
        for n, line in enumerate(jobs):
            if hang_after is not None and n >= hang_after:
                break
            src, dst = line.split("\t")
            with np.load(src) as d:  # closed again: Windows cannot delete open files
                w, h = (int(v) for v in d["size"])
            Image.new("L", (w, h), 64).save(dst)
            out.append(f"XPSB_DONE {n}\n")
        self.stdout = io.StringIO("".join(out))

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
        jobs.append((npz, tmp_path / ".blender" / f"o{k}_ao.png"))
    calls, seen = [], []

    def popen(cmd, **kw):
        calls.append(cmd)
        return FakeProc(cmd, **kw)

    done = blender.bake_batch("blender", jobs, lambda *a: seen.append(a), popen=popen)
    assert done == {0, 1, 2} and len(calls) == 1  # Blender started once
    assert seen[-1][:3] == (3, 3, "OPTIX NVIDIA RTX") and seen[-1][3] is not None
    ao = blender.ao_from(*jobs[0])
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
        jobs.append((npz, tmp_path / f"o{k}_ao.png"))
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
