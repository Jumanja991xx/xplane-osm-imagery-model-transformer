"""After a build with Blender the 3D preview shows the shaded package textures -
unless the object was rebuilt in the preview since."""

from __future__ import annotations

import os
from types import SimpleNamespace

from xpsb.jobs.manager import JobManager


def test_the_preview_shows_the_shaded_texture_of_the_package(tmp_path):
    preview = tmp_path / "preview"
    objects = tmp_path / "pkg" / "objects"
    preview.mkdir()
    objects.mkdir(parents=True)
    (preview / "xpsb_obj_001.json").write_text("{}")
    (preview / "xpsb_obj_001.png").write_bytes(b"plain")
    (objects / "xpsb_obj_001.png").write_bytes(b"shaded")
    (objects / "xpsb_obj_001_nml.png").write_bytes(b"relief")
    job = SimpleNamespace(
        build=SimpleNamespace(package_dir=str(tmp_path / "pkg"), shaded_stems=["xpsb_obj_001"])
    )
    pick = JobManager._shaded_texture
    assert (
        pick(None, job, "xpsb_obj_001.png", preview / "xpsb_obj_001.png").read_bytes() == b"shaded"
    )
    assert pick(None, job, "xpsb_obj_001_nml.png", preview / "xpsb_obj_001_nml.png") is not None
    assert pick(None, job, "xpsb_obj_002.png", preview / "xpsb_obj_002.png") is None
    # rebuilt in the preview after the build: its own texture again
    later = (objects / "xpsb_obj_001.png").stat().st_mtime + 10
    os.utime(preview / "xpsb_obj_001.json", (later, later))
    assert pick(None, job, "xpsb_obj_001.png", preview / "xpsb_obj_001.png") is None
    assert pick(None, SimpleNamespace(build=None), "xpsb_obj_001.png", preview) is None
