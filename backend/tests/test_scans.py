"""Free 3D scans: STL parsing, reduction, standing up, licence check, placing."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from xpsb import scans
from xpsb.build import figure
from xpsb.build.mesh import Mesh
from xpsb.build.sandbox import ScriptError, ScriptInputs, run_script
from xpsb.build.scripts import DEFAULT_SCRIPTS


def _statue_stl(y_up: bool = False, ascii_: bool = False) -> bytes:
    """A detailed figure as a 'scanned' STL (scanner units: millimetres)."""
    mesh = Mesh(max_triangles=100_000)
    figure.add_figure(mesh, 0, 0, 0, 2.0, "arm_raised", 0, "bronze", None, False, "torch")
    tris = []
    for p in mesh.patches:
        if p.lod != "near":
            continue
        for a, b, c in p.tris:
            tri = [p.positions[i] for i in (a, b, c)]
            if y_up:
                tri = [(x, z, -y) for x, y, z in tri]
            tris.append([[v * 1000 for v in pt] for pt in tri])
    if ascii_:
        lines = ["solid statue"]
        for t in tris:
            lines += ["facet normal 0 0 0", "outer loop"]
            lines += [f"vertex {x} {y} {z}" for x, y, z in t]
            lines += ["endloop", "endfacet"]
        return ("\n".join(lines + ["endsolid"]) + "\n").encode()
    out = bytearray(b"\0" * 80 + struct.pack("<I", len(tris)))
    for t in tris:
        out += struct.pack("<12fH", 0, 0, 0, *[c for pt in t for c in pt], 0)
    return bytes(out)


def test_binary_and_ascii_stl_read_the_same_surface():
    vb, tb = scans.parse_stl(_statue_stl())
    va, ta = scans.parse_stl(_statue_stl(ascii_=True))
    assert len(tb) == len(ta) > 1000
    assert np.allclose(np.ptp(vb, axis=0), np.ptp(va, axis=0), rtol=1e-3)
    assert len(vb) < len(tb)  # corners shared by neighbouring triangles are welded


def test_reduction_keeps_the_silhouette():
    verts, tris = scans.parse_stl(_statue_stl())
    v2, t2 = scans.decimate(verts, tris, 400)
    assert 100 <= len(t2) <= 400
    assert np.allclose(np.ptp(v2, axis=0), np.ptp(verts, axis=0), rtol=0.15)


def test_y_up_scans_are_stood_upright_with_the_foot_at_zero():
    verts, _ = scans.parse_stl(_statue_stl(y_up=True))
    up, axis = scans.upright(verts)
    assert axis == "y"
    assert up[:, 2].min() == pytest.approx(0) and up[:, 2].max() == pytest.approx(1)
    # the raised arm points up, not down
    assert up[:, 2][np.argmax(np.abs(up[:, 0]))] > 0.3


def test_only_shareable_scans_are_fetched(tmp_path, monkeypatch):
    monkeypatch.setattr(scans, "_fixtures", lambda: None)
    monkeypatch.setattr(scans, "_download", lambda url: _statue_stl())
    licences = {
        "Free.stl": "CC BY-SA 4.0",
        "Closed.stl": "All rights reserved",
        "Nc.stl": "CC BY-NC 4.0",
    }

    def info(name):
        return {
            "url": "u",
            "size": 1000,
            "page_url": f"p/{name}",
            "author": "A",
            "license": licences[name],
        }

    monkeypatch.setattr(scans, "file_info", info)

    def entity(*names):
        return {"claims": {"P4896": [{"mainsnak": {"datavalue": {"value": n}}} for n in names]}}

    assert scans.fetch(entity("Closed.stl", "Nc.stl"), tmp_path) is None
    scan = scans.fetch(entity("Closed.stl", "Free.stl"), tmp_path)
    assert scan is not None and scan.title == "Free.stl" and scan.license == "CC BY-SA 4.0"
    assert 1000 <= scan.triangles <= scans.NEAR_TRIANGLES
    assert (tmp_path.parent / tmp_path.name / scan.file.split("/")[-1]).is_file()


def test_scan_tool_places_the_scan_at_its_height_and_the_default_script_uses_it(tmp_path):
    path = tmp_path / "s.npz"
    scans.prepare(_statue_stl(), path)
    inputs = ScriptInputs(None, None, {}, scan=str(path))
    mesh = run_script("scan(10, 5, 2, 14, heading=90)", inputs, 30_000)
    (x0, _, z0), (x1, _, z1) = mesh.bounds()
    assert z0 == pytest.approx(2) and z1 == pytest.approx(16)
    assert mesh.used_scan and {p.lod for p in mesh.patches} == {"near", "far"}
    params = {"height": 9.0, "detail": "medium", "monument": {"kind": "statue"}}
    mesh = run_script(
        DEFAULT_SCRIPTS["monuments"], ScriptInputs(None, None, params, scan=str(path)), 30_000
    )
    assert mesh.used_scan and mesh.bounds()[1][2] == pytest.approx(9)
    with pytest.raises(ScriptError, match="no 3D scan"):
        run_script("scan(0, 0)", ScriptInputs(None, None, {}), 30_000)
