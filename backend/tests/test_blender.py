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
    src = 'facade(footprint, 0, 15, style="classic", storeys=3)\nflag(0, -20, 8)'
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
            src, dst = line.split("\t")[:2]
            with np.load(src) as d:  # closed again: Windows cannot delete open files
                w, h = (int(v) for v in d["size"])
            Image.new("L", (w, h), 64).save(dst)
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

    done = blender.bake_batch("blender", jobs, lambda *a: seen.append(a), popen=popen)
    assert done == {0, 1, 2} and len(calls) == 1  # Blender started once
    assert seen[-1][:3] == (3, 3, "OPTIX NVIDIA RTX") and seen[-1][3] is not None
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
