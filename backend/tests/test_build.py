"""M3 build: sandboxed build scripts, geometry orientation, baking, OBJ8 export."""

from __future__ import annotations

import pytest

from xpsb.build.bake import bake
from xpsb.build.mesh import dot, sub
from xpsb.build.objexport import to_obj8
from xpsb.build.sandbox import ScriptError, ScriptInputs, run_script
from xpsb.build.scripts import DEFAULT_SCRIPTS

SQUARE = [[0, 0], [20, 0], [20, 10], [0, 10]]


def run(src: str, footprint=SQUARE, params=None, budget=50_000):
    return run_script(
        src, ScriptInputs(footprint=footprint, path=None, params=params or {}), budget
    )


def outward(mesh, center):
    """Every face normal of a convex solid points away from its centre."""
    for patch in mesh.patches:
        if patch.two_sided:
            continue
        for a, b, c in patch.tris:
            pa, pb, pc = (patch.positions[i] for i in (a, b, c))
            mid = tuple((pa[k] + pb[k] + pc[k]) / 3 for k in range(3))
            # winding normal (counter-clockwise = front) must match the stored normal
            e1, e2 = sub(pb, pa), sub(pc, pa)
            wn = (
                e1[1] * e2[2] - e1[2] * e2[1],
                e1[2] * e2[0] - e1[0] * e2[2],
                e1[0] * e2[1] - e1[1] * e2[0],
            )
            assert dot(wn, patch.normals[a]) > 0, patch.material
            assert dot(patch.normals[a], sub(mid, center)) > 0, patch.material


@pytest.mark.parametrize(
    "src, center",
    [
        ("extrude(footprint, 0, 6)", (10, 5, 3)),
        ("extrude([[0,0],[0,10],[20,10],[20,0]], 0, 6)", (10, 5, 3)),  # clockwise input
        ("box(0, 0, 0, 8, 4, 3, heading=30)", (0, 0, 1.5)),
        ("cylinder(0, 0, 0, 3, 10, segments=16)", (0, 0, 5)),
        ("sweep([[0,0,0],[30,0,0]], [[0,0],[0,2],[3,2],[3,0]], caps=False)", (15, 1.5, 1)),
        ("sweep([[0,0,0],[30,0,0]], [[0,0],[3,0],[3,2],[0,2]], caps=False)", None),
        ("revolve(0, 0, [[5,0],[5,4],[0,6]], segments=16)", (0, 0, 2)),
    ],
)
def test_solids_face_outwards(src, center):
    mesh = run(src)
    if center is not None:
        outward(mesh, center)


def test_closed_sweep_faces_outwards_whatever_the_winding():
    for prof in ("[[0,0],[0,2],[3,2],[3,0]]", "[[0,0],[3,0],[3,2],[0,2]]"):
        mesh = run(f"sweep([[0,0,0],[30,0,0]], {prof}, caps=True)")
        outward(mesh, (15, 1.5, 1))


def test_tube_faces_outwards():
    outward(run("tube([0,0,0],[0,0,10], 0.5, segments=8)"), (0, 0, 5))


def test_sandbox_blocks_everything_but_geometry():
    for src, expected in (
        ("load('os.star', 'x')", "load"),
        ("while True:\n  pass", "while"),
        ("def f(n):\n  return f(n + 1)\nf(1)", ""),
        ("open('/etc/passwd')", "open"),
        ("x = [i for i in range(100000000)]", "longer"),
        ("box(0,0,0,5,5,5, mat='gold')", "unknown material"),
        ("extrude([[0,0],[1,1]], 0, 1)", "at least 3"),
        ("x = 1", "no geometry"),
    ):
        with pytest.raises(ScriptError) as err:
            run_script(src, ScriptInputs(None, None, {}), 1000, timeout_s=0.5)
        assert expected in str(err.value), (src, str(err.value))


def test_triangle_budget():
    with pytest.raises(ScriptError, match="budget"):
        run("for i in range(100):\n  cylinder(i, 0, 0, 1, 1, segments=64)", budget=500)


@pytest.mark.parametrize("category", sorted(DEFAULT_SCRIPTS))
def test_default_scripts_build(category):
    footprint = None if category == "masts" else [[-30, -5], [30, -5], [30, 5], [-30, 5]]
    params = {
        "height": 9,
        "covered": True,
        "roof_material": "solar",
        "facing": [0, -1],
        "detail": "medium",
    }
    mesh = run(DEFAULT_SCRIPTS[category], footprint, params)
    (x0, y0, z0), (x1, y1, z1) = mesh.bounds()
    assert z0 >= 0 and 2 < z1 <= 30
    baked = bake(mesh, 1024)
    obj = to_obj8(mesh, baked, "t.png", 2000).to_text()
    assert obj.startswith("I\n800") and "TRIS 0 " in obj


def test_grandstand_faces_the_field():
    params = {"facing": [0, -1], "covered": False, "height": 6}
    mesh = run(DEFAULT_SCRIPTS["grandstands"], [[-30, -5], [30, -5], [30, 5], [-30, 5]], params)
    # the low front wall stands at the south edge, the top row at the north
    low = [p for patch in mesh.patches for p in patch.positions if p[2] < 1.2 and p[2] > 0.5]
    high = [p for patch in mesh.patches for p in patch.positions if p[2] > 5.5]
    assert sum(p[1] for p in low) / len(low) < sum(p[1] for p in high) / len(high)


def test_grandstand_roof_clears_the_rows_and_corner_stands_face_the_field():
    # 12 m wide, 20 m deep stand in a curve: the field lies to the south, i.e. in
    # front of a short side. Given height 6 m is too low for 20 m of rows.
    params = {"facing": [0, -1], "covered": True, "roof_material": "solar", "height": 6}
    mesh = run(DEFAULT_SCRIPTS["grandstands"], [[0, 0], [12, 0], [12, 20], [0, 20]], params)
    seats = [p for patch in mesh.patches if patch.material == "seats" for p in patch.positions]
    roof = [p for patch in mesh.patches if patch.material == "solar" for p in patch.positions]
    assert max(p[2] for p in seats) + 2.0 < min(p[2] for p in roof)
    # rows climb away from the field (south low, north high)
    low = min(seats, key=lambda p: p[2])
    high = max(seats, key=lambda p: p[2])
    assert low[1] < high[1]


def test_obj8_winding_and_skirt():
    mesh = run("extrude(footprint, 0, 6, top=None)")
    obj = to_obj8(mesh, bake(mesh, 512), "t.png", 2000)
    assert min(v.y for v in obj.vertices) == -1.0  # walls reach into the ground
    # south wall (local y=0 → OBJ z=0) faces +Z; X-Plane needs clockwise as seen from there
    tri = None
    for i in range(0, len(obj.indices), 3):
        a, b, c = (obj.vertices[j] for j in obj.indices[i : i + 3])
        if a.nz > 0.9:
            tri = (a, b, c)
            break
    a, b, c = tri
    signed = (b.x - a.x) * (c.y - a.y) - (c.x - a.x) * (b.y - a.y)
    assert signed < 0  # clockwise seen from +Z
