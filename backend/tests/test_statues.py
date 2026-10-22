"""Statues and monuments: OSM classification, the figure kit, the default script."""

from __future__ import annotations

import pytest
from shapely.geometry import Point, box

from xpsb.build import figure
from xpsb.build.bake import bake
from xpsb.build.mesh import Mesh, cross, dot, sub
from xpsb.build.objexport import to_obj8
from xpsb.build.sandbox import ScriptError, ScriptInputs, run_script
from xpsb.build.scripts import DEFAULT_SCRIPTS
from xpsb.osm.classify import classify_tags, monument_kind


@pytest.mark.parametrize(
    ("tags", "kind"),
    [
        ({"historic": "memorial", "memorial": "statue", "name": "Szent István"}, "statue"),
        ({"historic": "memorial", "memorial": "statue", "name": "Reiterstandbild"}, "rider"),
        ({"historic": "memorial", "memorial": "bust"}, "bust"),
        ({"tourism": "artwork", "artwork_type": "bust"}, "bust"),
        ({"tourism": "artwork", "artwork_type": "statue"}, "statue"),
        ({"man_made": "obelisk"}, "obelisk"),
        ({"historic": "memorial", "memorial": "column"}, "column"),
        ({"historic": "monument", "name": "Szabadság-szobor"}, "statue"),
        ({"historic": "memorial", "memorial": "plaque"}, None),
        ({"tourism": "artwork", "artwork_type": "mural"}, None),
    ],
)
def test_monument_kinds_from_osm(tags, kind):
    assert monument_kind(tags) == kind


def test_statues_are_monuments_but_memorial_parks_and_buildings_are_not():
    statue = {"historic": "memorial", "memorial": "statue"}
    assert classify_tags(statue, Point(19.04, 47.49)) == ("object", "monuments")
    small = box(19.04, 47.49, 19.0401, 47.4901)  # about 7 x 11 m
    assert classify_tags(statue, small) == ("object", "monuments")
    park = box(19.04, 47.49, 19.042, 47.491)  # about 150 x 110 m
    assert classify_tags({"historic": "memorial", "memorial": "statue"}, park) is None
    hall = {"building": "yes", "historic": "monument"}
    assert classify_tags(hall, small) == ("object", "buildings")


@pytest.mark.parametrize("pose", figure.POSES)
def test_every_pose_is_a_closed_figure_of_the_asked_height_facing_outwards(pose):
    mesh = Mesh(max_triangles=20_000)
    figure.add_figure(mesh, 0, 0, 0, 4.0, pose, 0, "bronze", None, False, "torch")
    near = sum(len(p.tris) for p in mesh.patches if p.lod == "near")
    far = sum(len(p.tris) for p in mesh.patches if p.lod == "far")
    assert 150 <= near <= 3_000 and far < near / 2.5  # detailed close up, cheap far away
    top = max(p[2] for patch in mesh.patches for p in patch.positions)
    assert 3.6 <= top <= 4.4
    # faces point outwards: the triangle's winding agrees with its vertex normals
    agree = disagree = 0
    for patch in mesh.patches:
        for a, b, c in patch.tris:
            fn = cross(
                sub(patch.positions[b], patch.positions[a]),
                sub(patch.positions[c], patch.positions[a]),
            )
            vn = [sum(patch.normals[i][k] for i in (a, b, c)) for k in range(3)]
            d = dot(fn, vn)
            agree += d > 1e-12
            disagree += d < -1e-12
    assert disagree <= agree * 0.01


def test_figure_rejects_unknown_poses():
    with pytest.raises(ScriptError, match="pose"):
        run_script('figure(0, 0, pose="dancing")', ScriptInputs(None, None, {}), 10_000)
    with pytest.raises(ScriptError, match="holding"):
        run_script('figure(0, 0, holding="phone")', ScriptInputs(None, None, {}), 10_000)


@pytest.mark.parametrize("kind", ["statue", "rider", "bust", "obelisk", "column", "stone"])
def test_default_monument_script_builds_every_kind(kind):
    params = {"height": 12.0, "detail": "medium", "monument": {"kind": kind, "material": "bronze"}}
    mesh = run_script(DEFAULT_SCRIPTS["monuments"], ScriptInputs(None, None, params), 30_000)
    (_, _, z0), (_, _, z1) = mesh.bounds()
    assert z0 >= -0.01 and 11.0 <= z1 <= 13.0
    obj = to_obj8(mesh, bake(mesh, 512), "t.png", 2000).to_text()
    assert "TRIS 0 " in obj


def test_liberty_statue_pose_from_the_architect():
    src = """
top = pedestal(0, 0, 26, 8, round=False)
figure(0, 0, top, 14, pose="arms_raised", holding="palm", robe=True, color="#5E8C77")
"""
    mesh = run_script(src, ScriptInputs(None, None, {}), 30_000)
    assert 39.0 <= mesh.bounds()[1][2] <= 41.0
    assert {p.material for p in mesh.patches} == {"stone", "bronze"}
