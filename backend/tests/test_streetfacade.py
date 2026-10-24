"""Facades from street photos: the right photo per wall, and the wall painted back
from it. The photos here are rendered from a known wall pattern (360° panorama and
an ordinary photo), so the result can be compared with the pattern itself."""

from __future__ import annotations

import math

import numpy as np
from shapely.geometry import Polygon

from xpsb import streetfacade as sf

WALL = sf.Wall((0.0, 0.0), (20.0, 0.0), 0.0, 12.0)  # outward normal: south (0, -1)


def pattern(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """A wall with 2 m checks and a left-to-right gradient (u, v in metres)."""
    check = ((np.floor(u / 2) + np.floor(v / 2)) % 2) * 120 + 60
    return np.stack([check, u / 20 * 255, 255 - v / 12 * 200], axis=-1)


def render(cam: sf.Camera, w: int, h: int) -> np.ndarray:
    """What the camera sees of the wall (ray casting); the rest is grey."""
    xs = (np.arange(w) + 0.5) / w
    ys = (np.arange(h) + 0.5) / h
    gx, gy = np.meshgrid(xs, ys)
    if cam.pano:
        yaw = np.radians(cam.heading + (gx - 0.5) * 360.0)
        pitch = np.radians((0.5 - gy) * 180.0)
        d = np.stack([np.sin(yaw) * np.cos(pitch), np.cos(yaw) * np.cos(pitch), np.sin(pitch)], -1)
    else:
        hd = math.radians(cam.heading)
        f = (w / 2) / math.tan(math.radians(cam.fov) / 2)
        fwd = np.array([math.sin(hd), math.cos(hd), 0.0])
        right = np.array([math.cos(hd), -math.sin(hd), 0.0])
        x = (gx * w - w / 2) / f
        y = (h / 2 - gy * h) / f
        d = fwd + x[..., None] * right + y[..., None] * np.array([0.0, 0.0, 1.0])
    # the wall's plane is y = 0
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (0.0 - cam.y) / d[..., 1]
    px = cam.x + t * d[..., 0]
    pz = cam.z + t * d[..., 2]
    hit = (t > 0) & (px >= 0) & (px <= 20) & (pz >= 0) & (pz <= 12)
    img = np.full((h, w, 3), 128.0)
    img[hit] = pattern(px[hit], 12 - pz[hit])
    return img.astype(np.uint8)


def truth(tex: np.ndarray) -> np.ndarray:
    h, w = tex.shape[:2]
    u = (np.arange(w) + 0.5) / w * 20
    v = (np.arange(h) + 0.5) / h * 12
    uu, vv = np.meshgrid(u, v)
    return pattern(uu, vv)


def test_a_panorama_paints_the_wall_back():
    cam = sf.Camera(10.0, -15.0, heading=30.0, pano=True)
    pano = render(cam, 4096, 2048)
    tex, mask = sf.project(pano, WALL, cam, px_per_m=10)
    assert mask.all()
    err = np.abs(tex.astype(float) - truth(tex)).mean()
    assert err < 12, err  # checks and gradient in the right places


def test_an_ordinary_photo_paints_what_it_shows():
    cam = sf.Camera(10.0, -18.0, heading=0.0, pano=False, fov=70.0)
    photo = render(cam, 1600, 1200)
    tex, mask = sf.project(photo, WALL, cam, px_per_m=10)
    assert 0.5 < mask.mean() <= 1.0
    err = np.abs(tex.astype(float) - truth(tex))[mask].mean()
    assert err < 15, err


def test_the_best_photo_per_wall_and_none_from_behind_or_through_a_house():
    straight = sf.Camera(10.0, -14.0, heading=0.0)
    slanted = sf.Camera(-12.0, -6.0, heading=60.0, pano=True)
    behind = sf.Camera(10.0, 12.0, heading=180.0, pano=True)
    looking_away = sf.Camera(10.0, -14.0, heading=180.0)
    assert sf.view_score(WALL, behind) is None
    assert sf.view_score(WALL, looking_away) is None
    picked = sf.choose([WALL], [slanted, straight, behind], [])
    assert [(c.wall, c.photo) for c in picked] == [(0, 1)]
    # a house between the street and the wall: no photo for it
    kiosk = Polygon([(-5, -9), (25, -9), (25, -5), (-5, -5)])
    assert sf.choose([WALL], [straight], [kiosk]) == []


def test_walls_of_an_outline():
    walls = sf.walls_of([[0, 0], [20, 0], [20, 2], [0, 2]], 0, 10)
    assert [round(w.length) for w in walls] == [20, 20]  # the 2 m ends are too short
    assert walls[0].normal == (0.0, -1.0)


def test_the_bake_paints_a_wall_from_a_street_panorama():
    from xpsb.build.bake import bake
    from xpsb.build.mesh import Mesh, quad_face

    mesh = Mesh(1000)
    wall = quad_face([(0, 0, 0), (20, 0, 0), (20, 0, 12), (0, 0, 12)], "plaster", (200, 190, 170))
    mesh.patches.append(wall)
    cam = sf.Camera(10.0, -15.0, heading=30.0, pano=True)
    street = sf.Street([sf.Shot("p1", cam, render(cam, 4096, 2048))], [])
    baked = bake(mesh, 1024, 10.0, street=street)
    assert baked.street_photos == ["p1"]
    img = np.asarray(baked.image.convert("RGB"), dtype=float)
    h, w = img.shape[:2]
    (sa, ta), (sb, _), _, (_, td) = baked.uvs[0]
    us, vs = np.meshgrid(np.linspace(1, 19, 40), np.linspace(1, 11, 25))
    px = ((sa + (sb - sa) * us / 20) * w).astype(int)
    py = ((1 - (ta + (td - ta) * vs / 12)) * h).astype(int)
    err = np.abs(img[py, px] - pattern(us, 12 - vs)).mean()
    assert err < 15, err


def test_a_building_gets_the_street_photos_that_show_enough_of_it(tmp_path):
    from PIL import Image

    from xpsb.build import assemble
    from xpsb.photos import Photo, StreetCamera
    from xpsb.scene import SceneItem

    lon0, lat0, dlon, dlat = 19.034, 47.5, 0.0004, 0.0001  # about 30 x 11 m
    ring = [[lon0, lat0], [lon0 + dlon, lat0], [lon0 + dlon, lat0 + dlat], [lon0, lat0 + dlat]]
    item = SceneItem(
        id="way/7",
        kind="object",
        category="buildings",
        geometry={"type": "Polygon", "coordinates": [[*ring, ring[0]]]},
        source="osm",
    )
    Image.new("RGB", (2048, 1024), (40, 90, 200)).save(tmp_path / "p1.jpg")
    south = StreetCamera(lon=lon0 + dlon / 2, lat=lat0 - 0.00015, heading=0.0, pano=True)
    item.photos = [Photo(id="p1", source="panoramax", license="CC-BY-SA-4.0", camera=south)]
    _, baked, used = assemble.generate(item, [item], "medium", None, tmp_path)
    assert used == ["p1"] and baked.street_photos == ["p1"]
    # the photo shows only a short end wall: not enough, the building stays as it was
    east = StreetCamera(lon=lon0 + dlon * 1.5, lat=lat0 + dlat / 2, heading=270.0, pano=True)
    item.photos = [Photo(id="p1", source="panoramax", license="CC-BY-SA-4.0", camera=east)]
    _, baked, used = assemble.generate(item, [item], "medium", None, tmp_path)
    assert used == [] and baked.street_photos == []
    # a photo that may not be shared is never painted
    item.photos = [Photo(id="p1", source="mapillary", license="CC-BY-NC-4.0", camera=south)]
    assert assemble.generate(item, [item], "medium", None, tmp_path)[2] == []


def test_street_photos_keep_their_camera_and_come_from_different_sides():
    import io

    from PIL import Image

    from xpsb import photos, research

    def cand(bearing: float, dist: float, pano: bool = True) -> photos.PhotoCandidate:
        return photos.PhotoCandidate(
            id=f"c{bearing:.0f}_{dist:.0f}",
            source="panoramax",
            license="CC-BY-SA-4.0",
            distance_m=dist,
            thumb_url="t",
            image_url=f"i{bearing}{dist}",
            heading=10.0,
            pano=pano,
            bearing=bearing,
            cam_lon=19.0,
            cam_lat=47.5,
            fov=None if pano else 60.0,
        )

    picked = research.spread([cand(0, 10), cand(5, 12), cand(180, 30), cand(90, 50)], 3)
    assert [c.bearing for c in picked] == [0, 180, 90]
    assert research.spread([cand(0, 10), cand(10, 12)], 3) == [cand(0, 10)]

    def jpeg(w: int, h: int) -> bytes:
        buf = io.BytesIO()
        Image.new("RGB", (w, h)).save(buf, format="JPEG")
        return buf.getvalue()

    c = cand(200.0, 20)
    full = photos.camera_of(c, jpeg(2000, 1000))
    assert full.pano and full.heading == 10.0 and full.yaw_span == 360.0
    cut = photos.camera_of(c, photos.view_towards(jpeg(2000, 1000), 10.0, 200.0))
    assert cut.heading == 200.0 and cut.yaw_span == cut.pitch_span == photos.VIEW_FOV_DEG
    plain = photos.camera_of(cand(200.0, 20, pano=False), jpeg(1600, 1200))
    assert not plain.pano and plain.fov == 60.0
    c.heading = None
    assert photos.camera_of(c, jpeg(2000, 1000)) is None


def test_a_panorama_cut_out_paints_the_same_wall():
    cam = sf.Camera(10.0, -15.0, heading=30.0, pano=True)
    pano = render(cam, 4096, 2048)
    # the 90° x 90° view towards the wall's middle, as the download keeps it
    bearing = math.degrees(math.atan2(10.0 - cam.x, 0.0 - cam.y))
    rel = (bearing - cam.heading + 180) % 360 - 180
    cx = int(4096 / 2 + rel / 360 * 4096)
    cut = pano[512:1536, cx - 512 : cx + 512]
    view = sf.Camera(10.0, -15.0, heading=bearing, pano=True, yaw_span=90, pitch_span=90)
    tex, mask = sf.project(cut, WALL, view, px_per_m=10)
    assert mask.mean() > 0.9
    err = np.abs(tex.astype(float) - truth(tex))[mask].mean()
    assert err < 15, err
