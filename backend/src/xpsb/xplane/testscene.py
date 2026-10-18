"""Milestone-1 test scenery ("Spike A").

Exercises every technique the real generators will use, at the centre of the
user's area:
- an OBJ8 object (cube) with a texture, standing on the terrain
- a draped polygon with explicit UVs (param 65535, like orthophotos), whose
  texture shows N/E/S/W so texture orientation can be checked in the sim
- a draped polygon with a tiling material texture (TEXTURE + SCALE, like asphalt)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from xpsb.geo import offset
from xpsb.xplane.dsf_text import DsfText, Tile
from xpsb.xplane.obj8 import Obj8
from xpsb.xplane.pol import DrapedPolygonDef, LoadCenter

ORIENT_SIZE_M = 60.0
CHECKER_SIZE_M = 100.0
CUBE_SIZE_M = 20.0
SKIRT_M = 2.0  # cube reaches below ground so it never floats on slopes


@dataclass(frozen=True)
class TestSceneLayout:
    lon: float
    lat: float
    cube_height_m: float

    def footprint_points(self) -> list[tuple[float, float]]:
        """All corner points (lon, lat) of everything placed; used for tile checks."""
        pts = []
        for cx, size in ((-50.0, ORIENT_SIZE_M), (70.0, CHECKER_SIZE_M), (0.0, CUBE_SIZE_M)):
            h = size / 2
            for dx, dy in ((-h, -h), (h, -h), (h, h), (-h, h)):
                pts.append(offset(self.lon, self.lat, cx + dx, dy))
        return pts


def files_for(package_root: Path) -> list[str]:
    return sorted(
        str(p.relative_to(package_root)).replace("\\", "/")
        for p in package_root.rglob("*")
        if p.is_file()
    )


def _square(
    lon: float, lat: float, center_east_m: float, size_m: float
) -> list[tuple[float, float]]:
    h = size_m / 2
    # Counter-clockwise starting south-west.
    return [
        offset(lon, lat, center_east_m + dx, dy) for dx, dy in ((-h, -h), (h, -h), (h, h), (-h, h))
    ]


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def _orientation_texture(path: Path) -> None:
    img = Image.new("RGB", (512, 512), (40, 90, 160))
    d = ImageDraw.Draw(img)
    d.rectangle([4, 4, 507, 507], outline=(255, 255, 255), width=8)
    d.polygon([(256, 40), (206, 160), (306, 160)], fill=(255, 220, 0))  # arrow to north
    d.line([(256, 150), (256, 330)], fill=(255, 220, 0), width=24)
    font = _font(72)
    for text, xy in (("N", (256, 210)), ("S", (256, 450)), ("W", (60, 256)), ("E", (452, 256))):
        d.text(xy, text, fill=(255, 255, 255), font=font, anchor="mm")
    img.save(path)


def _checker_texture(path: Path) -> None:
    img = Image.new("RGB", (256, 256), (230, 230, 230))
    d = ImageDraw.Draw(img)
    for y in range(4):
        for x in range(4):
            if (x + y) % 2:
                d.rectangle([x * 64, y * 64, x * 64 + 63, y * 64 + 63], fill=(200, 40, 40))
    img.save(path)


def _cube_texture(path: Path) -> None:
    img = Image.new("RGB", (256, 256), (245, 245, 245))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 255, 255], outline=(220, 30, 30), width=16)
    d.text((128, 128), "XPSB", fill=(220, 30, 30), font=_font(56), anchor="mm")
    img.save(path)


def _cube(height_m: float) -> Obj8:
    h = CUBE_SIZE_M / 2
    y0, y1 = -SKIRT_M, height_m
    obj = Obj8(texture="xpsb_test_cube.png")
    uv = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    # Sides, counter-clockwise seen from outside. X east, Z south.
    obj.add_quad([(-h, y0, h), (h, y0, h), (h, y1, h), (-h, y1, h)], (0, 0, 1), uv)  # south
    obj.add_quad([(h, y0, -h), (-h, y0, -h), (-h, y1, -h), (h, y1, -h)], (0, 0, -1), uv)  # north
    obj.add_quad([(h, y0, h), (h, y0, -h), (h, y1, -h), (h, y1, h)], (1, 0, 0), uv)  # east
    obj.add_quad([(-h, y0, -h), (-h, y0, h), (-h, y1, h), (-h, y1, -h)], (-1, 0, 0), uv)  # west
    obj.add_quad([(-h, y1, h), (h, y1, h), (h, y1, -h), (-h, y1, -h)], (0, 1, 0), uv)  # roof
    obj.attributes.append("ATTR_LOD 0 6000")
    return obj


def build_test_scenery(package_root: Path, lon: float, lat: float, cube_height_m: float) -> Path:
    """Writes the package sources and returns the path of the DSF *text* file.

    Compiling the text to a .dsf is a separate step (DSFTool).
    """
    layout = TestSceneLayout(lon, lat, cube_height_m)
    tile = Tile.containing(lon, lat)
    if not all(tile.contains(*p) for p in layout.footprint_points()):
        raise ValueError("test_scene_crosses_tile_edge")

    # Textures sit next to the .obj/.pol that uses them (paths are relative to that file).
    for sub in ("objects", "polygons"):
        (package_root / sub).mkdir(parents=True, exist_ok=True)
    _orientation_texture(package_root / "polygons" / "xpsb_test_orientation.png")
    _checker_texture(package_root / "polygons" / "xpsb_test_checker.png")
    _cube_texture(package_root / "objects" / "xpsb_test_cube.png")

    (package_root / "objects" / "xpsb_test_cube.obj").write_text(
        _cube(cube_height_m).to_text(), encoding="ascii", newline="\n"
    )
    orient_center = offset(lon, lat, -50.0, 0.0)
    (package_root / "polygons" / "xpsb_test_orientation.pol").write_text(
        DrapedPolygonDef(
            texture="xpsb_test_orientation.png",
            wrap=False,
            scale_s_m=ORIENT_SIZE_M,
            scale_t_m=ORIENT_SIZE_M,
            load_center=LoadCenter(orient_center[1], orient_center[0], ORIENT_SIZE_M, 512),
        ).to_text(),
        encoding="ascii",
        newline="\n",
    )
    (package_root / "polygons" / "xpsb_test_checker.pol").write_text(
        DrapedPolygonDef(
            texture="xpsb_test_checker.png",
            wrap=True,
            scale_s_m=20.0,
            scale_t_m=20.0,
            layer_offset=2,
        ).to_text(),
        encoding="ascii",
        newline="\n",
    )

    dsf = DsfText(tile=tile)
    cube = dsf.add_object_def("objects/xpsb_test_cube.obj")
    orient = dsf.add_polygon_def("polygons/xpsb_test_orientation.pol")
    checker = dsf.add_polygon_def("polygons/xpsb_test_checker.pol")
    dsf.add_object(cube, lon, lat, 0.0)
    # Explicit UVs: s grows to the east, t grows to the north (t=0 is the south edge).
    o = _square(lon, lat, -50.0, ORIENT_SIZE_M)
    uv = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    dsf.add_polygon(orient, 65535, [[(p[0], p[1], s, t) for p, (s, t) in zip(o, uv, strict=True)]])
    dsf.add_polygon(checker, 0, [_square(lon, lat, 70.0, CHECKER_SIZE_M)])

    text_path = package_root.parent / f"{package_root.name}.dsf.txt"
    text_path.write_text(dsf.to_text(), encoding="ascii", newline="\n")
    return text_path
