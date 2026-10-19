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


def _facade(
    u,
    v,
    color,
    *,
    bay: float,
    storey: float,
    win_w: float,
    win_h: float,
    sill: float,
    arched: bool,
    base: RGB,
    surround: RGB,
    ground: float = 0.0,
    bands: bool = True,
):
    """Wall with one window per bay and storey: stone surround, mullion and transom,
    a cornice band under every storey line; optional round-arched window heads.
    Nothing below `ground` metres (a plinth or rusticated base storey goes there)."""
    img = plaster(u, v, color or base)
    bu = u % bay
    sv = v % storey
    cx = bay / 2
    half = win_w / 2
    top = sill + win_h
    spring = top - half if arched else top  # where the arch starts
    dx = bu - cx
    upper = (v >= ground) & (v > 0.5)

    def opening(margin: float) -> np.ndarray:
        rect = (np.abs(dx) < half + margin) & (sv > sill - margin) & (sv < spring)
        if arched:
            head = (sv >= spring) & (dx**2 + (sv - spring) ** 2 < (half + margin) ** 2)
        else:
            head = (sv >= spring) & (sv < top + margin) & (np.abs(dx) < half + margin)
        return (rect | head) & upper

    frame = opening(0.18)
    glass_ = opening(0.0)
    img[frame] = surround
    img[frame] = _shade(img[frame], 0.04 * _noise(u[frame], v[frame], 0.3, 13))
    img[glass_] = (58, 70, 82)
    img[glass_] = _shade(img[glass_], 0.18 * _noise(u[glass_], v[glass_], 0.5, 9))
    bars = glass_ & ((np.abs(dx) < 0.04) | (np.abs(sv - (sill + 0.62 * (spring - sill))) < 0.04))
    img[bars] = (205, 200, 190)
    if not bands:
        return img
    # Cornice band under each storey line, with its shadow below.
    band = ((sv > storey - 0.35) | (sv < 0.08)) & upper
    img[band] = np.array(surround, dtype=np.float32) * 0.97
    shadow = (sv > storey - 0.5) & (sv <= storey - 0.35) & upper
    img[shadow] = _shade(img[shadow], np.full(img[shadow].shape[0], -0.18, dtype=np.float32))
    return img


def windows_classic(u, v, color):
    """Historic facade: tall windows with stone surrounds, 4.8 m storeys, one every 4.2 m."""
    return _facade(
        u,
        v,
        color,
        bay=4.2,
        storey=4.8,
        win_w=1.5,
        win_h=2.7,
        sill=1.1,
        arched=False,
        base=(214, 206, 188),
        surround=(232, 226, 212),
    )


def windows_arched(u, v, color):
    """Historic facade with round-arched windows: 5.5 m storeys, one every 4.6 m."""
    return _facade(
        u,
        v,
        color,
        bay=4.6,
        storey=5.5,
        win_w=1.7,
        win_h=3.4,
        sill=1.1,
        arched=True,
        base=(214, 206, 188),
        surround=(232, 226, 212),
    )


def windows_house(u, v, color):
    """Residential wall: 2.8 m storeys, a window every 3.6 m (fewer than `windows`)."""
    return _facade(
        u,
        v,
        color,
        bay=3.6,
        storey=2.8,
        win_w=1.2,
        win_h=1.35,
        sill=0.9,
        arched=False,
        base=(230, 222, 204),
        surround=(244, 242, 236),
        bands=False,
    )


def stone(u, v, color):
    """Ashlar: large dressed stone blocks in courses."""
    base = np.array(color or (204, 196, 178), dtype=np.float32)
    course = np.floor(v / 0.6)
    shift = (course % 2) * 0.6
    img = _fill(u, base)
    img = _shade(img, 0.05 * _noise(u + shift, course, 1.2, 17) + 0.02 * _noise(u, v, 0.15, 19))
    joint = ((v % 0.6) < 0.02) | (((u + shift) % 1.2) < 0.02)
    img[joint] = _shade(img[joint], np.full(img[joint].shape[0], -0.22, dtype=np.float32))
    return img


def rustication(u, v, color):
    """Rusticated base storey of palaces: stone with deep horizontal grooves."""
    base = np.array(color or (186, 178, 162), dtype=np.float32)
    course = np.floor(v / 0.5)
    shift = (course % 2) * 0.9
    img = _fill(u, base)
    img = _shade(img, 0.05 * _noise(u + shift, course, 1.8, 23))
    groove = (v % 0.5) < 0.07
    joint = ((u + shift) % 1.8) < 0.03
    img[groove] = _shade(img[groove], np.full(img[groove].shape[0], -0.35, dtype=np.float32))
    img[joint & ~groove] = _shade(
        img[joint & ~groove], np.full(img[joint & ~groove].shape[0], -0.2, dtype=np.float32)
    )
    return img


def glass_pane(u, v, color):
    """One window pane in its own 2 x 2 texture square (all windows share it): frame,
    mullion, transom and a little sky reflection."""
    img = _fill(u, color or (54, 66, 80))
    img = _shade(img, 0.10 * np.clip(1.2 - v, -1, 1) + 0.08 * _noise(u, v, 0.4, 31))
    frame = (u < 0.12) | (u > 1.88) | (v < 0.1) | (v > 1.9)
    bars = (np.abs(u - 1.0) < 0.05) | (np.abs(v - 1.3) < 0.05)
    img[frame | bars] = (226, 222, 212)
    return img


def moulding(u, v, color):
    """Smooth light stone for cornices and string courses (no joints)."""
    img = _fill(u, color or (236, 231, 219))
    return _shade(img, 0.025 * _noise(u, v, 0.3, 37))


def door(u, v, color):
    """Wooden double door in its own 2 x 2 texture square: panels and a frame."""
    img = _fill(u, color or (92, 62, 40))
    img = _shade(img, 0.06 * _noise(u * 4, v, 0.3, 41))
    frame = (u < 0.1) | (u > 1.9) | (v > 1.9)
    split = np.abs(u - 1.0) < 0.03
    panel = ((u % 1.0) > 0.22) & ((u % 1.0) < 0.78) & (((v % 0.9) > 0.15) & ((v % 0.9) < 0.75))
    img[panel] = _shade(img[panel], np.full(img[panel].shape[0], 0.12, dtype=np.float32))
    img[frame | split] = (210, 204, 192)
    return img


def balustrade(u, v, color):
    """Balustrade: small stone columns with gaps, a rail on top and a plinth below."""
    img = stone(u, v, color or (226, 220, 206))
    post = (u % 0.32) < 0.17
    body = (v > 0.18) & (v < 0.82)
    gap = body & ~post
    img[gap] = _shade(img[gap], np.full(img[gap].shape[0], -0.45, dtype=np.float32))
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
    "windows_classic": windows_classic,
    "windows_arched": windows_arched,
    "windows_house": windows_house,
    "stone": stone,
    "rustication": rustication,
    "glass": glass,
    "glass_pane": glass_pane,
    "door": door,
    "moulding": moulding,
    "balustrade": balustrade,
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
    "windows": "modern wall with a dense window grid (3 m storeys, a window every 2.6 m), "
    "colour = wall colour - offices, apartment blocks",
    "windows_classic": "historic facade (palaces, town halls, courts, museums, 19th century): "
    "tall windows with stone surrounds, 4.8 m storeys, one every 4.2 m, cornice bands",
    "windows_arched": "like windows_classic with round-arched windows (5.5 m storeys, one "
    "every 4.6 m) - neo-renaissance/baroque palaces, churches, stations",
    "windows_house": "residential wall: 2.8 m storeys, a window every 3.6 m",
    "stone": "ashlar stone blocks, colour = stone colour (towers, plinths, monuments)",
    "rustication": "rusticated base storey of palaces (deep horizontal grooves)",
    "glass": "glass facade with mullions",
    "glass_pane": "a single window pane (used by facade(); texture square 2 x 2)",
    "balustrade": "balustrade with small columns (parapets of palaces, terraces)",
    "door": "a wooden double door (used by facade(); texture square 2 x 2)",
    "moulding": "smooth light stone for cornices, string courses, window surrounds",
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


# ---- relief and gloss (normal map) -------------------------------------------------


# Relief, in metres below the surface: mortar joints, grooves, tile rows, ribs.
def _h_brick(u, v):
    row = np.floor(v / 0.075)
    shift = (row % 2) * 0.12
    joint = ((v % 0.075) < 0.012) | (((u + shift) % 0.24) < 0.012)
    return np.where(joint, -0.012, 0.0)


def _h_stone(u, v):
    course = np.floor(v / 0.6)
    shift = (course % 2) * 0.6
    joint = ((v % 0.6) < 0.02) | (((u + shift) % 1.2) < 0.02)
    return np.where(joint, -0.015, 0.0) + 0.003 * _noise(u, v, 0.15, 19)


def _h_rustication(u, v):
    course = np.floor(v / 0.5)
    shift = (course % 2) * 0.9
    groove = (v % 0.5) < 0.07
    joint = ((u + shift) % 1.8) < 0.03
    return np.where(groove, -0.05, np.where(joint, -0.025, 0.0))


def _h_tiles(u, v):
    return 0.025 * ((v % 0.3) / 0.3)  # each row overlaps the next one down


def _h_ribs(u, v):
    return 0.012 * np.cos(u / 0.2 * 2 * np.pi)


HEIGHT = {
    "brick": _h_brick,
    "stone": _h_stone,
    "rustication": _h_rustication,
    "roof_tiles": _h_tiles,
    "metal_sheet": _h_ribs,
    "roof_metal": _h_ribs,
}
# Where dark really means deep (painted windows, gaps): relief from brightness, in
# metres for black to white.
FROM_BRIGHTNESS_M = {
    "windows": 0.12,
    "windows_classic": 0.12,
    "windows_arched": 0.12,
    "windows_house": 0.1,
    "balustrade": 0.08,
    "glass_pane": 0.03,
    "door": 0.03,
    "seats": 0.04,
    "steps": 0.05,
}
# Gloss for X-Plane's normal-map alpha (1 = smooth and shiny, 0 = rough).
GLOSS: dict[str, float] = {
    "glass": 0.85,
    "glass_pane": 0.8,
    "solar": 0.7,
    "metal_sheet": 0.45,
    "roof_metal": 0.4,
    "steel": 0.4,
    "membrane": 0.3,
    "paint": 0.3,
    "lamp": 0.3,
}
DEFAULT_GLOSS = 0.08


def normal_map(
    rgb: np.ndarray, material: str, px_per_m: float, u: np.ndarray, v: np.ndarray
) -> np.ndarray:
    """Tangent-space normals (+u right, +v up) and gloss of a painted region, RGBA 0..255."""
    fn = HEIGHT.get(material)
    if fn is not None:
        height = fn(u, v).astype(np.float32)
    elif material in FROM_BRIGHTNESS_M:
        lum = rgb[..., 0] * 0.3 + rgb[..., 1] * 0.59 + rgb[..., 2] * 0.11
        height = (lum / 255.0) * FROM_BRIGHTNESS_M[material]
    else:  # smooth surfaces: plaster, concrete, glass, paint ...
        height = np.zeros(u.shape, dtype=np.float32)
    # rows run down the image (v decreases), columns along u
    d_row, d_col = np.gradient(height)
    dh_du = d_col * px_per_m
    dh_dv = -d_row * px_per_m
    nx, ny, nz = -dh_du, -dh_dv, np.ones_like(height)
    length = np.sqrt(nx * nx + ny * ny + nz * nz)
    out = np.empty(height.shape + (4,), dtype=np.float32)
    out[..., 0] = (nx / length) * 127.5 + 127.5
    out[..., 1] = (ny / length) * 127.5 + 127.5
    out[..., 2] = (nz / length) * 127.5 + 127.5
    out[..., 3] = GLOSS.get(material, DEFAULT_GLOSS) * 255.0
    return out
