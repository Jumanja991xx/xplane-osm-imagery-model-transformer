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
