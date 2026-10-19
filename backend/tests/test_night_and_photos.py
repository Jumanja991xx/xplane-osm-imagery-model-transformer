"""Night lighting, detail levels and photo textures."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from xpsb.build import phototex
from xpsb.build.bake import bake
from xpsb.build.objexport import to_obj8, to_preview
from xpsb.build.sandbox import ScriptError, ScriptInputs, run_script
from xpsb.build.scripts import DEFAULT_SCRIPTS
from xpsb.photos import Photo
from xpsb.scene import PhotoTexture
from xpsb.xplane.obj8 import Obj8

SQUARE = [[0, 0], [30, 0], [30, 14], [0, 14]]


def run(src, footprint=SQUARE, params=None, textures=None, budget=60_000):
    inputs = ScriptInputs(
        footprint=footprint, path=None, params=params or {}, textures=textures or {}
    )
    return run_script(src, inputs, budget)


def test_windows_light_up_at_night_and_far_away_a_painted_stand_in_takes_over():
    mesh = run('facade(footprint, 0, 15, style="classic", storeys=3)')
    lods = {p.lod for p in mesh.patches}
    assert {"near", "far"} <= lods
    assert any(p.glow == "window" for p in mesh.patches)  # some rooms are lit
    baked = bake(mesh)
    assert baked.lit is not None and np.asarray(baked.lit).max() > 100
    obj = to_obj8(mesh, baked, "a.png", 3000, "a_nml.png", "a_lit.png", 400)
    text = obj.to_text()
    assert "TEXTURE_LIT a_lit.png" in text
    assert "ATTR_LOD 0 400" in text and "ATTR_LOD 400 3000" in text
    # the two levels cover all indices exactly once each
    assert sum(g.count for g in obj.groups) == len(obj.indices)
    # the preview shows the close-up version only
    data = to_preview(mesh, baked)
    assert data["lit"] is True


def test_lights_are_written_in_obj_axes_with_lights_txt_parameters():
    mesh = run(
        'extrude(footprint, 0, 5)\nlight(10, 2, 4, 0, 1, 0, color="white", intensity=5000, beam=90)'
    )
    obj = to_obj8(mesh, bake(mesh), "a.png", 2000)
    spill = next(line for line in obj.lights if line.startswith("LIGHT_PARAM spot_params_sp_pm"))
    parts = spill.split()
    # position: x east, y up, z south (local y=2 north -> z=-2)
    assert [float(v) for v in parts[2:5]] == [10.0, 4.0, -2.0]
    r, g, b, a, cd, dx, dy, dz, width = (float(v) for v in parts[5:])
    assert cd == 5000 and a == 1
    assert (dx, dy, dz) == pytest.approx((0, 0, -1))  # pointing north
    assert width == pytest.approx(np.cos(np.radians(45)), abs=1e-3)
    assert any(line.startswith("LIGHT_PARAM spot_params_bb_pm") for line in obj.lights)
    with pytest.raises(ScriptError):
        run("extrude(footprint, 0, 5)\nlight(0, 0, 1, intensity=9e9)")


def test_landmarks_are_floodlit_and_stadiums_get_floodlights_and_see_through_roofs():
    hist = run(
        DEFAULT_SCRIPTS["buildings"],
        params={"building": "museum", "height": 16, "tags": {"historic": "yes"}},
    )
    assert len(hist.lights) >= 6
    ring = [[100 * np.cos(a), 80 * np.sin(a)] for a in np.linspace(0, 2 * np.pi, 48, False)]
    stadium = run(
        DEFAULT_SCRIPTS["buildings"],
        footprint=[[float(x), float(y)] for x, y in ring],
        params={"building": "stadium", "height": 40, "detail": "medium", "tags": {}},
    )
    mats = {p.material for p in stadium.patches}
    assert {"seats", "seat_riser", "stand_fascia", "roof_clear", "lamella"} <= mats
    assert len(stadium.lights) > 40
    baked = bake(stadium)
    assert baked.image.mode == "RGBA"  # the translucent roof ring
    assert np.asarray(baked.image)[..., 3].min() < 128


def _photo_of_a_facade(path):
    """A 'photo': a striped facade seen at an angle (a perspective quadrilateral)."""
    facade = np.zeros((200, 400, 3), dtype=np.uint8)
    facade[...] = (200, 180, 150)
    facade[:, ::40] = (20, 20, 20)  # dark vertical lines every 40 px = every 2 m
    src = Image.fromarray(facade)
    photo = Image.new("RGB", (800, 600), (90, 120, 160))
    # where the facade corners land in the photo (TL, TR, BR, BL)
    quad = [(100, 150), (700, 100), (720, 520), (80, 460)]
    m = phototex._homography([(0, 0), (400, 0), (400, 200), (0, 200)], quad)
    coeffs = tuple((m / m[2, 2]).flatten()[:8])
    warped = src.transform((800, 600), Image.Transform.PERSPECTIVE, coeffs)
    mask = Image.new("L", (400, 200), 255).transform(
        (800, 600), Image.Transform.PERSPECTIVE, coeffs
    )
    photo.paste(warped, (0, 0), mask)
    photo.save(path, format="JPEG", quality=95)
    return [[x / 800, y / 600] for x, y in quad]


def test_rectify_straightens_the_marked_facade(tmp_path):
    corners = _photo_of_a_facade(tmp_path / "p1.jpg")
    tex = PhotoTexture(name="front", photo_id="p1", corners=corners, width_m=20, height_m=10)
    img = np.asarray(phototex.rectify(tmp_path / "p1.jpg", tex)).astype(int)
    h, w = img.shape[:2]
    assert abs(w / h - 2.0) < 0.05  # 20 m x 10 m
    # the dark lines are vertical again: the same columns are dark at top and bottom
    top = img[h // 5].sum(axis=-1) < 200
    bottom = img[4 * h // 5].sum(axis=-1) < 200
    assert top.sum() >= 5 and (top & bottom).sum() >= 0.6 * top.sum()


def test_photo_textures_are_painted_and_only_shareable_photos_count(tmp_path):
    corners = _photo_of_a_facade(tmp_path / "p1.jpg")
    tex = PhotoTexture(name="front", photo_id="p1", corners=corners, width_m=20, height_m=10)
    photos = [Photo(id="p1", source="user")]
    mats = phototex.load([tex], photos, tmp_path)
    assert set(mats) == {"front"}
    mesh = run(
        'quad([0, 0, 0], [20, 0, 0], [20, 0, 10], [0, 0, 10], mat="photo:front")',
        textures={"front": {"width": 20, "height": 10, "repeat": False}},
    )
    baked = bake(mesh, photos=mats)
    px = np.asarray(baked.image).reshape(-1, 3)
    assert (px.sum(axis=-1) < 150).sum() > 50  # the photo's dark lines are in the atlas
    # unknown names fail in the sandbox, with the available ones named
    with pytest.raises(ScriptError, match="front"):
        run('quad([0,0,0],[1,0,0],[1,0,1],[0,0,1], mat="photo:back")', textures={"front": {}})
    # a photo whose licence forbids sharing is never used
    nc = [Photo(id="p1", source="wikimedia", license="CC BY-NC-SA 2.0")]
    assert phototex.load([tex], nc, tmp_path) == {}
    assert (
        not nc[0].shareable and Photo(id="x", source="mapillary", license="CC-BY-SA-4.0").shareable
    )


def test_photo_texture_on_all_walls_by_the_default_script():
    mesh = run(
        DEFAULT_SCRIPTS["buildings"],
        params={"building": "office", "height": 12},
        textures={"front": {"width": 10, "height": 12, "repeat": True, "walls": True}},
    )
    assert any(p.material == "photo:front" for p in mesh.patches)
    assert not any(p.material == "glass_pane" for p in mesh.patches)  # no modelled windows


def test_obj8_groups_must_be_consistent():
    obj = Obj8(texture="a.png")
    obj.add_quad([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], (0, 0, 1), [(0, 0)] * 4)
    obj.groups = [(["ATTR_LOD 0 100"], 0, 4)]  # not a multiple of 3
    with pytest.raises(ValueError):
        obj.to_text()
