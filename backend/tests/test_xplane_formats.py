from __future__ import annotations

import pytest

from xpsb.xplane.dsf_text import DsfText, Tile
from xpsb.xplane.obj8 import Obj8
from xpsb.xplane.pol import DrapedPolygonDef, LoadCenter


@pytest.mark.parametrize(
    ("lon", "lat", "path"),
    [
        (11.4, 47.3, "Earth nav data/+40+010/+47+011.dsf"),
        (8.56, 49.32, "Earth nav data/+40+000/+49+008.dsf"),
        (-118.2, 33.9, "Earth nav data/+30-120/+33-119.dsf"),
        (-0.5, -0.5, "Earth nav data/-10-010/-01-001.dsf"),
        (151.2, -33.8, "Earth nav data/-40+150/-34+151.dsf"),
    ],
)
def test_tile_paths(lon, lat, path):
    assert Tile.containing(lon, lat).dsf_relative_path == path


def test_bounds_properties_come_last():
    dsf = DsfText(tile=Tile(8, 49))
    dsf.add_exclusion("forests", 8.1, 49.1, 8.2, 49.2)
    props = [line for line in dsf.to_text().splitlines() if line.startswith("PROPERTY")]
    assert props[-4:] == [
        "PROPERTY sim/west 8",
        "PROPERTY sim/east 9",
        "PROPERTY sim/north 50",
        "PROPERTY sim/south 49",
    ]
    assert "PROPERTY sim/overlay 1" in props
    assert "PROPERTY sim/exclude_for 8.100000/49.100000/8.200000/49.200000" in props


def test_points_outside_the_tile_are_rejected():
    dsf = DsfText(tile=Tile(8, 49))
    obj = dsf.add_object_def("objects/a.obj")
    with pytest.raises(ValueError):
        dsf.add_object(obj, 9.5, 49.5, 0)
    pol = dsf.add_polygon_def("polygons/a.pol")
    with pytest.raises(ValueError):
        dsf.add_polygon(pol, 0, [[(8.5, 49.5), (8.6, 49.5), (8.6, 50.2)]])


def test_resource_paths_must_be_portable():
    dsf = DsfText(tile=Tile(8, 49))
    for bad in ("objects\\a.obj", "/abs/a.obj", "../a.obj", "objects/ü.obj"):
        with pytest.raises(ValueError):
            dsf.add_object_def(bad)


def test_obj8_layout():
    obj = Obj8(texture="t.png")
    for _ in range(2):
        obj.add_quad(
            [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
            (0, 0, 1),
            [(0, 0), (1, 0), (1, 1), (0, 1)],
        )
    lines = obj.to_text().splitlines()
    assert lines[:5] == ["I", "800 xpsb", "OBJ", "", "TEXTURE t.png"]
    assert "POINT_COUNTS 8 0 0 12" in lines
    assert sum(1 for line in lines if line.startswith("IDX10 ")) == 1
    assert sum(1 for line in lines if line.startswith("IDX ")) == 2
    assert lines[-1] == "TRIS 0 12"


def test_pol_layout():
    text = DrapedPolygonDef(
        texture="o.png",
        wrap=False,
        scale_s_m=60,
        scale_t_m=60,
        load_center=LoadCenter(49.3, 8.5, 60, 512),
    ).to_text()
    assert text.splitlines()[:3] == ["A", "850 xpsb", "DRAPED_POLYGON"]
    assert "TEXTURE_NOWRAP o.png" in text
    assert "LOAD_CENTER 49.30000 8.50000 60.0 512" in text
    assert "LAYER_GROUP beaches 1" in text
