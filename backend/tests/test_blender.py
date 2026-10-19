"""Blender bridge: finding it, the bake call (fake Blender) and applying the result."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import numpy as np
from PIL import Image

from xpsb import blender
from xpsb.build.bake import bake
from xpsb.build.sandbox import ScriptInputs, run_script


def _mesh():
    src = 'facade(footprint, 0, 12, style="classic", storeys=2)\nflag(0, -20, 8)'
    fp = [[0, 0], [20, 0], [20, 10], [0, 10]]
    return run_script(src, ScriptInputs(footprint=fp, path=None, params={}), 40_000)


def test_bake_runs_the_fixed_script_and_shades_only_own_texture_pieces():
    mesh = _mesh()
    baked = bake(mesh)
    seen = {}

    def fake_run(cmd, **kw):
        # what the app hands to Blender: our own script, never anything else
        assert "--python" in cmd and Path(cmd[cmd.index("--python") + 1]) == blender.SCRIPT
        assert "--factory-startup" in cmd
        src, dst = cmd[-2], cmd[-1]
        with np.load(src) as d:  # closed again: Windows cannot delete open files
            seen["rec"], seen["occ"] = len(d["receivers"]), len(d["occluders"])
            w, h = (int(v) for v in d["size"])
        Image.new("L", (w, h), 64).save(dst)  # "everything occluded"
        return subprocess.CompletedProcess(cmd, 0, "", "")

    ao = blender.bake_ao("blender", mesh, baked, run=fake_run)
    assert ao is not None and seen["rec"] > 0 and seen["occ"] > 0
    assert ao.min() < 0.3 and ao.max() == 1.0  # dark only where receivers are
    before = np.asarray(baked.image).astype(int).sum()
    blender.apply_ao(baked, ao)
    assert np.asarray(baked.image).astype(int).sum() < before


def test_failed_blender_leaves_the_object_alone():
    mesh = _mesh()
    baked = bake(mesh)

    def broken(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "error")

    assert blender.bake_ao("blender", mesh, baked, run=broken) is None


@pytest.mark.skipif(sys.platform == "win32", reason="uses a shell script as fake Blender")
def test_find_ignores_missing_or_odd_programs(tmp_path):
    assert blender.find(str(tmp_path / "nope.exe")) is None
    fake = tmp_path / "blender"
    fake.write_text("#!/bin/sh\necho 'Blender 4.2.1'\n")
    fake.chmod(0o755)
    info = blender.find(str(fake))
    assert info is not None and info.version == "4.2.1"
