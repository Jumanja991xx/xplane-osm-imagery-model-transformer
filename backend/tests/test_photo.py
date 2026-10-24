"""The Blender photo of a built scenery, and the "fine" Blender quality."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from xpsb import blender
from xpsb.jobs.manager import JobConflict, JobManager


def _manager(tmp_path, objects):
    files = {}
    for o in objects:
        for name in (f"{o['stem']}.json", f"{o['stem']}.png"):
            files[name] = tmp_path / name
            files[name].write_text("{}")
    return SimpleNamespace(
        preview=lambda job_id: {"objects": objects},
        preview_file=lambda job_id, name: files.get(name),
    )


def _obj(stem, item, x, y, top, status="ok"):
    return {
        "stem": stem,
        "item_id": item,
        "status": status,
        "offset": [x, y],
        "heading": 30.0,
        "extent": [-20.0, -10.0, 20.0, 10.0],
        "top_m": top,
    }


def test_the_photo_shows_every_built_object_and_aims_at_the_chosen_one(tmp_path):
    objs = [
        _obj("a", "way/1", 0.0, 0.0, 30.0),
        _obj("b", "way/2", 500.0, 0.0, 90.0),
        _obj("c", "way/3", 900.0, 0.0, 10.0, status="error"),
    ]
    jm = _manager(tmp_path, objs)
    m = JobManager.photo_manifest(jm, "job", item_id="way/2", azimuth=250.0, elevation=18.0)
    assert [o["texture"] is not None for o in m["objects"]] == [True, True]
    assert m["objects"][1]["x"] == 500.0 and m["objects"][1]["heading"] == 30.0
    assert m["objects"][0]["normal"] is None
    cam = m["camera"]
    assert cam["target"][:2] == [500.0, 0.0]
    assert cam["azimuth"] == 250.0 and cam["elevation"] == 18.0
    assert cam["distance"] == pytest.approx(90.0 * 1.25 + 40.0)  # the tall object fits
    # the whole scene: the camera takes in all objects
    whole = JobManager.photo_manifest(jm, "job")
    assert whole["camera"]["target"][0] == pytest.approx(250.0)
    assert whole["camera"]["distance"] > cam["distance"]


def test_a_photo_needs_built_objects_and_an_existing_item(tmp_path):
    jm = _manager(tmp_path, [_obj("a", "way/1", 0.0, 0.0, 30.0)])
    with pytest.raises(JobConflict) as e:
        JobManager.photo_manifest(jm, "job", item_id="way/9")
    assert e.value.key == "error.item_not_found"
    empty = _manager(tmp_path, [])
    with pytest.raises(JobConflict) as e:
        JobManager.photo_manifest(empty, "job")
    assert e.value.key == "error.no_preview"


def test_a_photo_without_blender_says_so(tmp_path):
    jm = SimpleNamespace(blender=lambda: None)
    with pytest.raises(JobConflict) as e:
        JobManager.photo(jm, "job")
    assert e.value.key == "error.blender_missing"


def test_fine_quality_bakes_finer_with_more_samples(tmp_path):
    npz = tmp_path / "obj.npz"
    np.savez(
        npz,
        atlas=np.array([4096, 2048]),
        size=np.array([512, 256]),
        detail_size=np.array([2048, 1024]),
        samples=np.array(16),
    )
    blender.refine(npz)
    with np.load(npz) as d:
        assert list(d["size"]) == [1024, 512]
        assert list(d["detail_size"]) == [4096, 2048]
        assert int(d["samples"]) == blender.FINE_SAMPLES


def test_fine_quality_goes_to_the_landmarks():
    from xpsb.build.assemble import fine_bake
    from xpsb.jobs.gates import blender_minutes

    assert fine_bake("buildings", True, 500)  # own design
    assert fine_bake("buildings", False, 30_000)  # a big detailed building
    assert not fine_bake("buildings", False, 2_000)  # an ordinary house
    assert not fine_bake("barriers", False, 30_000)
    assert not fine_bake("city", False, 12_000)  # blocks of houses
    town = [SimpleNamespace(category="buildings", designed=False, triangles=2_000)] * 500
    town += [SimpleNamespace(category="buildings", designed=True, triangles=30_000)] * 3
    fine = blender_minutes(town, SimpleNamespace(blender_quality="fine"))
    standard = blender_minutes(town, SimpleNamespace(blender_quality="standard"))
    assert standard < fine < standard * 2  # a few landmarks, not half a day
