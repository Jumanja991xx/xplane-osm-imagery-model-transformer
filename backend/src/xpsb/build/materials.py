"""Procedural materials painted at real-world scale (metres) into the texture
atlas of an object. Each painter gets surface coordinates in metres, so patterns
(seat rows, windows, panels) keep their size on every face.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import numpy as np

RGB = tuple[int, int, int]
Painter = Callable[[np.ndarray, np.ndarray, RGB | None], np.ndarray]

_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def parse_color(value) -> RGB:
    if isinstance(value, str):
        m = _HEX.match(value.strip())
        if m:
            h = m.group(1)
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    if isinstance(value, list) and len(value) == 3 and all(isinstance(c, int) for c in value):
        return tuple(max(0, min(255, c)) for c in value)  # type: ignore[return-value]
    raise ValueError(f"colour must be '#rrggbb' or [r, g, b], got {value!r}")


def _noise(u: np.ndarray, v: np.ndarray, scale: float, seed: int = 1) -> np.ndarray:
    """Cheap deterministic value noise in [-1, 1] (no randomness between builds)."""
    x = np.floor(u / scale).astype(np.int64)
    y = np.floor(v / scale).astype(np.int64)
    h = (x * 374761393 + y * 668265263 + seed * 2654435761) & 0xFFFFFFFF
    h = (h ^ (h >> 13)) * 1274126177 & 0xFFFFFFFF
    return (h & 0xFFFF) / 32767.5 - 1.0


def _fill(u: np.ndarray, rgb) -> np.ndarray:
    out = np.empty(u.shape + (3,), dtype=np.float32)
    out[...] = rgb
    return out


def _shade(img: np.ndarray, amount: np.ndarray) -> np.ndarray:
    return img * (1.0 + amount[..., None])


def concrete(u, v, color):
    img = _fill(u, color or (168, 166, 160))
    return _shade(img, 0.06 * _noise(u, v, 0.25) + 0.04 * _noise(u, v, 1.7, 3))


def plaster(u, v, color):
    img = _fill(u, color or (222, 214, 196))
    return _shade(img, 0.03 * _noise(u, v, 0.2) + 0.03 * _noise(u, v, 2.3, 5))


def brick(u, v, color):
    base = np.array(color or (150, 70, 52), dtype=np.float32)
    row = np.floor(v / 0.075)
    shift = (row % 2) * 0.12
    joint = ((v % 0.075) < 0.012) | (((u + shift) % 0.24) < 0.012)
    img = _fill(u, base)
    img = _shade(img, 0.10 * _noise(u + shift, row, 0.24, 7))
    img[joint] = (190, 186, 178)
    return img


def windows(u, v, color):
    """Facade wall with a window grid (3 m storeys, window every 2.6 m)."""
    img = plaster(u, v, color)
    in_x = (u % 2.6) > 1.0
    in_y = ((v % 3.0) > 0.9) & ((v % 3.0) < 2.4) & (v > 0.5)
    glass = in_x & in_y
    frame = glass & (
        ((u % 2.6) < 1.08) | ((u % 2.6) > 2.52) | ((v % 3.0) < 0.98) | ((v % 3.0) > 2.32)
    )
    img[glass] = (70, 88, 104)
    img[glass] = _shade(img[glass], 0.15 * _noise(u[glass], v[glass], 0.6, 9))
    img[frame] = (235, 235, 230)
    return img


def glass(u, v, color):
    img = _fill(u, color or (96, 124, 146))
    img = _shade(img, 0.12 * np.clip(np.sin(u * 0.7 + v * 0.3), -1, 1))
    mullion = ((u % 1.5) < 0.06) | ((v % 2.0) < 0.06)
    img[mullion] = (60, 64, 70)
    return img


def metal_sheet(u, v, color):
    img = _fill(u, color or (176, 180, 184))
    ribs = np.cos(u / 0.2 * 2 * np.pi)
    return _shade(img, 0.08 * ribs + 0.03 * _noise(u, v, 1.0, 11))


def roof_metal(u, v, color):
    img = _fill(u, color or (150, 152, 156))
    ribs = np.cos(u / 0.33 * 2 * np.pi)
    return _shade(img, 0.07 * ribs + 0.04 * _noise(u, v, 2.0, 13))


def roof_flat(u, v, color):
    img = _fill(u, color or (110, 110, 108))
    return _shade(img, 0.08 * _noise(u, v, 0.4, 15) + 0.05 * _noise(u, v, 3.0, 17))


def roof_tiles(u, v, color):
    img = _fill(u, color or (160, 70, 50))
    row = np.floor(v / 0.33)
    img = _shade(img, 0.10 * _noise(np.floor((u + (row % 2) * 0.15) / 0.3), row, 1.0, 19))
    img[(v % 0.33) < 0.04] *= 0.7
    return img


def solar(u, v, color):
    """Photovoltaic modules 1.0 x 1.7 m with light frames and cell grid."""
    img = _fill(u, color or (34, 46, 78))
    cell = ((u % 0.166) < 0.008) | ((v % 0.166) < 0.008)
    img[cell] *= 1.35
    frame = ((u % 1.02) < 0.03) | ((v % 1.72) < 0.03)
    img[frame] = (190, 194, 200)
    return _shade(img, 0.05 * _noise(np.floor(u / 1.02), np.floor(v / 1.72), 1.0, 21))


def seats(u, v, color):
    """Rows of seats along u (0.5 m wide), on a concrete tread."""
    img = concrete(u, v, None)
    seat = ((u % 0.5) > 0.06) & ((v % 0.8) > 0.25) & ((v % 0.8) < 0.65)
    img[seat] = color or (40, 90, 170)
    back = seat & ((v % 0.8) > 0.55)
    img[back] *= 0.8
    return img


def steps(u, v, color):
    img = concrete(u, v, color)
    img[(v % 0.4) < 0.03] *= 0.75
    return img


def steel(u, v, color):
    img = _fill(u, color or (88, 92, 98))
    return _shade(img, 0.05 * _noise(u, v, 0.5, 23))


def membrane(u, v, color):
    img = _fill(u, color or (238, 238, 232))
    return _shade(img, 0.04 * np.cos(u / 4.0 * 2 * np.pi) + 0.02 * _noise(u, v, 2.0, 25))


def wood(u, v, color):
    img = _fill(u, color or (150, 110, 70))
    return _shade(img, 0.10 * np.sin(v * 9 + _noise(u, v, 0.4, 27) * 2))


def lamp(u, v, color):
    return _fill(u, color or (245, 245, 235))


def paint(u, v, color):
    """Plain colour, e.g. signs, boards, painted steel."""
    img = _fill(u, color or (200, 200, 200))
    return _shade(img, 0.02 * _noise(u, v, 0.5, 29))


MATERIALS: dict[str, Painter] = {
    "concrete": concrete,
    "plaster": plaster,
    "brick": brick,
    "windows": windows,
    "glass": glass,
    "metal_sheet": metal_sheet,
    "roof_metal": roof_metal,
    "roof_flat": roof_flat,
    "roof_tiles": roof_tiles,
    "solar": solar,
    "seats": seats,
    "steps": steps,
    "steel": steel,
    "membrane": membrane,
    "wood": wood,
    "lamp": lamp,
    "paint": paint,
}

MATERIAL_HELP = {
    "concrete": "grey concrete",
    "plaster": "rendered wall, colour = wall colour",
    "brick": "brick wall",
    "windows": "wall with a window grid (3 m storeys), colour = wall colour",
    "glass": "glass facade with mullions",
    "metal_sheet": "ribbed cladding (vertical ribs along the face's u)",
    "roof_metal": "ribbed metal roof",
    "roof_flat": "flat roof membrane/gravel",
    "roof_tiles": "pitched tile roof",
    "solar": "solar panel modules",
    "seats": "stand tread with a row of seats, colour = seat colour",
    "steps": "concrete steps/terracing",
    "steel": "dark steel (columns, beams, trusses)",
    "membrane": "white fabric/membrane roof",
    "wood": "timber",
    "lamp": "floodlight head / bright panel",
    "paint": "plain painted surface in the given colour",
}
