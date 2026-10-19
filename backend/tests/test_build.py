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


def test_building_reads_the_quick_controls():
    fp = [[0, 0], [20, 0], [20, 10], [0, 10]]  # long side east-west
    base = {"roof_shape": "gabled", "levels": 3, "wall_material": "brick", "roof_material": "solar"}
    mesh = run(DEFAULT_SCRIPTS["buildings"], fp, base)
    mats = {p.material for p in mesh.patches}
    assert {"brick", "solar"} <= mats and "windows" not in mats
    assert max(p[2] for patch in mesh.patches for p in patch.positions) > 9  # 3 storeys + roof

    def ridge(params):
        m = run(DEFAULT_SCRIPTS["buildings"], fp, params)
        top = max(p[2] for patch in m.patches for p in patch.positions)
        pts = [p for patch in m.patches for p in patch.positions if p[2] > top - 0.01]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return max(xs) - min(xs), max(ys) - min(ys)

    along = ridge(base)
    across = ridge({**base, "flip": True})
    assert along[0] > along[1] and across[1] > across[0]


def test_wide_halls_get_their_pitched_roof_too():
    fp = [[0, 0], [60, 0], [60, 40], [0, 40]]  # big building=yes: treated as a hall
    for flip in (False, True):
        m = run(
            DEFAULT_SCRIPTS["buildings"], fp, {"roof_shape": "gabled", "height": 8, "flip": flip}
        )
        assert any(p.material == "roof_metal" for p in m.patches)
        assert max(p[2] for patch in m.patches for p in patch.positions) <= 8 + 4.01


def test_building_style_follows_the_osm_type():
    hall = run(
        DEFAULT_SCRIPTS["buildings"],
        [[0, 0], [60, 0], [60, 40], [0, 40]],
        {"building": "industrial"},
    )
    assert {p.material for p in hall.patches} == {"metal_sheet", "roof_metal"}
    house = run(
        DEFAULT_SCRIPTS["buildings"], [[0, 0], [12, 0], [12, 9], [0, 9]], {"building": "house"}
    )
    assert "roof_tiles" in {p.material for p in house.patches}  # pitched by default
    top = max(p[2] for patch in house.patches for p in patch.positions)
    assert 9 < top < 11  # 6.5 m walls + roof


def test_stadium_bowl():
    import math

    oval = [
        [110 * math.cos(2 * math.pi * i / 64), 80 * math.sin(2 * math.pi * i / 64)]
        for i in range(64)
    ]
    m = run(
        DEFAULT_SCRIPTS["buildings"],
        oval,
        {"building": "stadium", "detail": "medium"},
        budget=30_000,
    )
    mats = {p.material for p in m.patches}
    assert {"seats", "membrane"} <= mats
    # stands climb towards the outside: seats near the outer wall are higher
    seats = [p for patch in m.patches if patch.material == "seats" for p in patch.positions]
    outer = [p for p in seats if math.hypot(p[0] / 110, p[1] / 80) > 0.93]
    inner = [p for p in seats if math.hypot(p[0] / 110, p[1] / 80) < 0.85]
    assert min(p[2] for p in outer) > max(p[2] for p in inner)


@pytest.mark.parametrize("kind", ["fence", "wall", "hedge", "guard_rail"])
def test_barriers_along_a_path(kind):
    m = run_script(
        DEFAULT_SCRIPTS["barriers"],
        ScriptInputs(
            footprint=None, path=[[0, 0], [20, 0], [20, 15]], params={"tags": {"barrier": kind}}
        ),
        20_000,
    )
    assert m.triangle_count > 0
    xs = [p[0] for patch in m.patches for p in patch.positions]
    assert min(xs) < 1 and max(xs) > 19


def test_historic_buildings_get_fewer_larger_windows():
    from xpsb.build.materials import MATERIAL_HELP, MATERIALS

    for name in ("windows_classic", "windows_arched", "windows_house", "stone", "rustication"):
        assert name in MATERIALS and name in MATERIAL_HELP
    palace = run(
        DEFAULT_SCRIPTS["buildings"],
        [[0, 0], [60, 0], [60, 30], [0, 30]],
        {"building": "palace", "levels": 3},
    )
    assert "windows_classic" in {p.material for p in palace.patches}
    # an old public building tagged as heritage, mapped as plain "yes"
    listed = run(
        DEFAULT_SCRIPTS["buildings"],
        [[0, 0], [40, 0], [40, 25], [0, 25]],
        {"building": "yes", "tags": {"heritage": "2"}},
    )
    assert "windows_classic" in {p.material for p in listed.patches}
    house = run(
        DEFAULT_SCRIPTS["buildings"], [[0, 0], [12, 0], [12, 9], [0, 9]], {"building": "house"}
    )
    assert "windows_house" in {p.material for p in house.patches}


def test_window_spacing_of_the_facades():
    import numpy as np

    from xpsb.build.materials import MATERIALS

    def window_columns(name: str) -> int:
        u, v = np.meshgrid(np.arange(0, 42, 0.05), np.array([1.8]))  # 42 m at window height
        dark = np.flatnonzero(MATERIALS[name](u, v, None).mean(axis=-1)[0] < 110) * 0.05
        # windows = groups of glass along the row (a mullion splits no window)
        return int(1 + np.sum(np.diff(dark) > 0.5)) if len(dark) else 0

    assert window_columns("windows") >= 15  # dense modern grid
    assert 8 <= window_columns("windows_classic") <= 11  # one every 4.2 m
