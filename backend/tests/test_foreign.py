"""Objects of other sceneries on our objects' places get exact exclusion zones."""

from __future__ import annotations

from pathlib import Path

from shapely.geometry import box

from xpsb.xplane import foreign

DSF = """PROPERTY sim/west 11
OBJECT_DEF objects/angel.obj
OBJECT_DEF lib/g10/tree.obj
POLYGON_DEF facades/wall.fac
POLYGON_DEF forests/park.for
OBJECT 0 11.6010 48.1420 90.0
OBJECT 1 11.6100 48.1500 0.0
BEGIN_POLYGON 0 10 2
POLYGON_POINT 11.6005 48.1418
POLYGON_POINT 11.6007 48.1418
POLYGON_POINT 11.6007 48.1419
END_POLYGON
BEGIN_POLYGON 1 255 2
POLYGON_POINT 11.70 48.20
POLYGON_POINT 11.71 48.20
POLYGON_POINT 11.71 48.21
END_POLYGON
"""
OBJ = """I
800
OBJ
VT -3 0 -3 0 1 0 0 0
VT 3 38 3 0 1 0 1 1
"""


def test_their_landmark_on_ours_gets_a_zone_holding_its_origin(tmp_path: Path):
    pack = tmp_path / "Old Angel"
    (pack / "objects").mkdir(parents=True)
    (pack / "objects/angel.obj").write_text(OBJ)
    placed = foreign.parse(DSF.splitlines(), "Old Angel", pack, (11.59, 48.13, 11.62, 48.16))
    kinds = sorted((p.kind, p.path) for p in placed)
    assert kinds == [
        ("facades", "facades/wall.fac"),
        ("objects", "lib/g10/tree.obj"),
        ("objects", "objects/angel.obj"),
    ]  # the forest far away is not read
    angel = next(p for p in placed if p.path == "objects/angel.obj")
    xs = [q[0] for q in angel.footprint]
    assert max(xs) - min(xs) > 0.00005  # its real size from the OBJ (6 m)

    # Our new angel stands a few metres east of their origin, overlapping their model.
    ours = [box(11.60102, 48.14195, 11.60122, 48.14205)]
    hits = foreign.overlapping(placed, ours)
    assert [p.path for p in hits] == ["objects/angel.obj"]
    w, s, e, n = foreign.zone(hits[0])
    assert w <= angel.lon <= e and s <= angel.lat <= n  # X-Plane needs the origin inside
