"""Paints the facade atlas of a city: one cell per style and wall colour.

A cell is four window axes wide (so neighbouring windows differ at night) and
stacked from the bottom: plinth, ground floor, four upper floors (repeated up a
taller building), and a plain attic band (gables, walls above the eaves).
The night texture lights a share of the windows, shops most of all.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

from xpsb.build import materials
from xpsb.build.materials import _noise
from xpsb.city.styles import FacadeStyle, RegionPreset

BAYS = 4  # window axes per cell
FLOOR_ROWS = 4  # upper floors per cell (a taller house repeats them)
PLINTH_M = 0.6
FOOT_M = 1.4  # the darker foot of the walls (from the bottom of the plinth)
FOOT_DARK = 0.64  # brightness right at the ground
ATTIC_M = 3.0
CORNICE_M = 0.8  # the moulded band under the eaves (3D cornice of historic houses)
PX_PER_M = 20.0
MAX_CELL_PX = 900
ATLAS_W = 4096
PAD = 4  # pixels around each cell (filled with its edge) against mipmap bleeding

ROOM_LIGHT = np.array((255, 196, 128), dtype=np.float32)
SHOP_LIGHT = np.array((255, 236, 200), dtype=np.float32)
OFFICE_LIGHT = np.array((226, 234, 255), dtype=np.float32)


@dataclass
class Cell:
    """Where a style's cell is in the atlas (texture coordinates, t up) and how
    tall each of its rows is in metres."""

    s0: float
    s1: float
    rows: dict[str, tuple[float, float]]  # plinth, ground, floor0..3, attic -> (t0, t1)
    bay_m: float
    ground_m: float
    storey_m: float


@dataclass
class Atlas:
    image: Image.Image
    lit: Image.Image
    cells: dict[tuple[str, int], Cell]


def _rows(f: FacadeStyle) -> list[tuple[str, float]]:
    rows = [("plinth", PLINTH_M), ("ground", f.ground_m)]
    rows += [(f"floor{k}", f.storey_m) for k in range(FLOOR_ROWS)]
    return rows + [("cornice", CORNICE_M), ("attic", ATTIC_M)]


def _cell_size(f: FacadeStyle) -> tuple[float, int, int]:
    width_m = BAYS * f.bay_m
    height_m = sum(h for _, h in _rows(f))
    ppm = min(PX_PER_M, MAX_CELL_PX / height_m, MAX_CELL_PX / width_m)
    return ppm, round(width_m * ppm), round(height_m * ppm)


def _hash(a: np.ndarray, seed: int) -> np.ndarray:
    h = (a.astype(np.int64) * 2654435761 + seed * 97 + 12345) & 0xFFFFFFFF
    h = (h ^ (h >> 15)) * 2246822519 & 0xFFFFFFFF
    return (h & 0xFFFF) / 65535.0


def _wall(f: FacadeStyle, color, u, v) -> np.ndarray:
    if f.wall == "brick":
        return materials.brick(u, v, color)
    if f.wall == "stone":
        return materials.stone(u, v, color)
    if f.wall in ("concrete", "panel"):
        img = materials.concrete(u, v, color)
        if f.wall == "panel":  # prefab panels: joints at every axis and storey
            joint = ((u % f.bay_m) < 0.03) | ((v % f.storey_m) < 0.03)
            img[joint] *= 0.78
        return img
    if f.wall == "metal":  # trapezoidal sheet: vertical ribs every 0.2 m
        img = materials._fill(u, color)
        rib = np.cos((u % 0.2) / 0.2 * 2 * np.pi)
        return img * (1.0 + 0.07 * rib[..., None] + 0.02 * _noise(u, v, 3.0, 5)[..., None])
    if f.wall == "wood":  # vertical boards
        img = materials._fill(u, color)
        board = np.floor(u / 0.16)
        img = img * (1.0 + 0.08 * _noise(board, board * 0, 1.0, 11)[..., None])
        img[(u % 0.16) < 0.012] *= 0.55
        return img * (1.0 + 0.05 * _noise(u, v, 0.4, 13)[..., None])
    if f.wall == "glass":
        return materials._fill(u, f.glass)
    return materials.plaster(u, v, color)


def _glass(f: FacadeStyle, u, v, h) -> np.ndarray:
    img = materials._fill(u, f.glass)
    sky = np.clip(v / max(h, 0.1), 0, 1)  # brighter towards the top: the sky in it
    return img * (0.85 + 0.35 * sky[..., None] + 0.06 * _noise(u, v, 0.7, 9)[..., None])


def _paint_row(
    f: FacadeStyle, color, kind: str, row: int, u: np.ndarray, v: np.ndarray, h: float, seed: int
) -> tuple[np.ndarray, np.ndarray]:
    """Colour and night light of one row; u, v in metres within the row."""
    img = _wall(f, color, u, v + row * 7.3)
    lit = np.zeros(u.shape + (3,), dtype=np.float32)
    bay = np.floor(u / f.bay_m)
    du = u - (bay + 0.5) * f.bay_m
    cell_id = bay + 31 * row

    if kind == "plinth":
        img = materials.concrete(u, v, (150, 146, 140))
        return img, lit
    if kind == "attic":
        return img, lit
    if kind == "cornice":  # mouldings, a row of dentils, the shadow line below
        img = materials.plaster(u, v, f.trim)
        img[v > h - 0.14] *= 1.06
        img[(v > h - 0.24) & (v <= h - 0.14)] *= 0.86
        dent = (v > 0.22) & (v < 0.42) & ((u % 0.24) < 0.13)
        img[dent] *= 1.05
        img[(v > 0.22) & (v < 0.42) & ~dent] *= 0.8
        img[v < 0.1] *= 0.75
        return img, lit

    def window(w, sill, top, arched=False):
        rect = (np.abs(du) < w / 2) & (v > sill) & (v < (top - w / 2 if arched else top))
        if arched:
            rect |= (v >= top - w / 2) & (du**2 + (v - (top - w / 2)) ** 2 < (w / 2) ** 2)
        return rect

    def put_window(mask, w, sill, top, share, light, mullions=True):
        nonlocal img
        outer = window(w + 0.24, sill - 0.12, top + 0.12) if f.bands or f.window == "tall" else None
        if outer is not None:
            img[outer] = f.trim
        img[mask] = _glass(f, u, v, h)[mask]
        frame = mask & ~window(w - 0.14, sill + 0.07, top - 0.07, f.window == "arched")
        bars = (
            mask
            & mullions
            & (
                ((np.abs(du) < 0.035) & (w > 0.9))
                | (np.abs(v - (sill + 0.7 * (top - sill))) < 0.03)
            )
        )
        img[frame | bars] = f.window_frame
        on = mask & ~frame & ~bars & (_hash(cell_id, seed) < share)
        lit[on] = light * (0.75 + 0.25 * _hash(cell_id, seed + 1))[on][:, None]

    if kind == "ground" and f.ground != "same":
        g = h
        if f.ground == "shop":
            shop = (np.abs(du) < f.bay_m / 2 - 0.35) & (v > 0.35) & (v < g - 0.9)
            img[(np.abs(du) < f.bay_m / 2 - 0.25) & (v > 0.25) & (v < g - 0.8)] = (54, 54, 58)
            img[shop] = _glass(f, u, v, g)[shop] * 1.1
            sign = (v > g - 0.75) & (v < g - 0.3) & (np.abs(du) < f.bay_m / 2 - 0.25)
            img[sign] = (48, 52, 60)
            img[sign & (np.abs(du) < f.bay_m * 0.3)] = (64, 68, 76)  # board (real names: life.py)
        elif f.ground in ("garage", "hall_door"):
            if f.ground == "garage":
                door = (np.abs(du) < min(1.25, f.bay_m / 2 - 0.2)) & (v < min(2.1, g - 0.3))
            else:
                door = (bay == 1) & (np.abs(du) < f.bay_m / 2 - 0.6) & (v < min(4.5, g - 1.0))
            img[door] = (196, 198, 200)
            img[door & ((v % 0.5) < 0.04)] = (150, 152, 154)
            if f.ground == "hall_door":
                put_window((bay == 3) & window(1.0, 0.0, 2.2), 1.0, 0.0, 2.2, 0.2, ROOM_LIGHT)
        elif f.ground == "lobby":
            glass = (v > 0.1) & (v < g - 0.4) & ((u % (f.bay_m / 2)) > 0.08)
            img[glass] = _glass(f, u, v, g)[glass]
            lit[glass & (_hash(cell_id, seed) < 0.7)] = OFFICE_LIGHT * 0.7
        elif f.ground == "plinth":
            img *= 0.88
            put_window(
                window(f.win_w_m * 0.8, g - 1.6, g - 0.6),
                f.win_w_m * 0.8,
                g - 1.6,
                g - 0.6,
                f.lit_share,
                ROOM_LIGHT,
            )
        if f.ground in ("plinth", "lobby", "shop") or f.ground == "garage":
            return img, lit

    # Upper floors (and a "same" ground floor): the windows of the style.
    if f.window == "curtain":
        glass = (v > 0.9) & ((u % (f.bay_m / 2)) > 0.06) & (v < h - 0.05)
        spandrel = (v <= 0.9) & ((u % (f.bay_m / 2)) > 0.06)
        img[spandrel] = np.array(f.glass, dtype=np.float32) * 0.7
        img[glass] = _glass(f, u, v, h)[glass]
        office = np.floor(u / (f.bay_m * 2)) + 31 * row
        lit[glass & (_hash(office, seed) < f.lit_share)] = OFFICE_LIGHT * 0.6
    elif f.window == "band":
        top = min(h - 0.3, f.sill_m + f.win_h_m)
        mask = window(min(f.win_w_m, f.bay_m - 0.2), f.sill_m, top)
        put_window(mask, min(f.win_w_m, f.bay_m - 0.2), f.sill_m, top, f.lit_share, ROOM_LIGHT)
    elif f.window == "hall":
        sill = max(0.5, h - 1.4 - f.win_h_m)
        mask = window(f.win_w_m, sill, sill + f.win_h_m)
        put_window(mask, f.win_w_m, sill, sill + f.win_h_m, f.lit_share, OFFICE_LIGHT, False)
    elif f.window != "none":
        top = min(h - 0.25, f.sill_m + f.win_h_m)
        arched = f.window == "arched"
        mask = window(f.win_w_m, f.sill_m, top, arched)
        if kind == "ground":  # the entrance in the second axis
            door = (bay == 1) & (np.abs(du) < 0.55) & (v < 2.2)
            mask &= ~(bay == 1)
            img[door] = (96, 70, 50) if f.wall != "glass" else f.glass
            img[door & ((np.abs(du) > 0.48) | (v > 2.13))] = f.window_frame
        if f.shutters is not None:
            side = (np.abs(du) >= f.win_w_m / 2 + 0.03) & (np.abs(du) < f.win_w_m + 0.03)
            shutter = side & (v > f.sill_m) & (v < top) & ~(kind == "ground") | (
                side & (v > f.sill_m) & (v < top) & (bay != 1)
            )
            img[shutter] = f.shutters
            img[shutter & ((v % 0.09) < 0.02)] = np.array(f.shutters, dtype=np.float32) * 0.7
        put_window(mask, f.win_w_m, f.sill_m, top, f.lit_share, ROOM_LIGHT)

    if f.bands:
        img[v < 0.15] = f.trim
    if f.balconies != "none" and kind != "ground":
        on_bay = (bay % 3 != 0) if f.balconies == "some" else np.ones_like(bay, dtype=bool)
        reach = np.abs(du) < min(f.bay_m / 2 - 0.1, f.win_w_m / 2 + 0.7)
        slab = on_bay & reach & (v < 0.18)
        rail = on_bay & reach & (v >= 0.18) & (v < 1.05)
        img[slab] = f.trim
        rc = np.array(f.balcony_color or f.trim, dtype=np.float32)
        img[rail] = img[rail] * 0.45 + rc * 0.55
        img[rail & ((u % 0.12) < 0.025)] = rc * 0.8
        img[on_bay & reach & (v > h - 0.3)] *= 0.82  # shade under the slab above
    return img, lit


def paint_cell(f: FacadeStyle, color, seed: int) -> tuple[np.ndarray, np.ndarray, list]:
    """The cell's pixels (rows from the top, like an image) and its rows' pixel spans."""
    ppm, w, _ = _cell_size(f)
    parts, lits, spans = [], [], []
    y = 0
    for n, (kind_name, h) in enumerate(_rows(f)):
        ph = max(2, round(h * ppm))
        kind = kind_name.rstrip("0123456789")
        u = (np.arange(w) + 0.5) / ppm
        v = h - (np.arange(ph) + 0.5) / ppm * (h * ppm / ph)
        uu, vv = np.meshgrid(u, v)
        img, lit = _paint_row(f, color, kind, n, uu, vv, h, seed)
        parts.append(img)
        lits.append(lit)
        spans.append((kind_name, ph))
        y += ph
    # rows were painted bottom first; an image runs top down
    img = np.concatenate(parts[::-1], axis=0)
    lit = np.concatenate(lits[::-1], axis=0)
    img *= _foot_shade(f, spans, img.shape[0])[:, None, None]
    return np.clip(img, 0, 255), np.clip(lit, 0, 255), spans


def _foot_shade(f: FacadeStyle, spans: list, height_px: int) -> np.ndarray:
    """Brightness per pixel row (top down): the foot of the wall darker - splash
    water, the shade of the ground, grime. A wall bright down to the lawn looks
    as if it stood on the ground rather than in it."""
    heights = {name: m for name, m in _rows(f)}
    metres = []
    for name, ph in spans:  # bottom first
        m = heights[name]
        base = metres[-1][1] if metres else 0.0
        metres.append((ph, base + m))
    out = np.ones(height_px, dtype=np.float32)
    row = height_px  # image row just below the current span
    below = 0.0
    for ph, top in metres:
        for k in range(ph):
            h = below + (k + 0.5) / ph * (top - below)
            if h < FOOT_M:
                out[row - 1 - k] = FOOT_DARK + (1 - FOOT_DARK) * (h / FOOT_M) ** 0.8
        row -= ph
        below = top
        if below >= FOOT_M:
            break
    return out


def build(preset: RegionPreset, used: set[tuple[str, int]] | None = None) -> Atlas:
    """The atlas with a cell for each (style, colour) - only the used ones if given."""
    keys = [
        (s.facade.id, k)
        for s in preset.styles
        for k in range(len(s.facade.colors))
        if used is None or (s.facade.id, k) in used
    ]
    by_id = {s.facade.id: s.facade for s in preset.styles}
    painted = []
    for key in keys:
        f = by_id[key[0]]
        img, lit, spans = paint_cell(f, f.colors[key[1]], seed=hash_seed(key))
        painted.append((key, img, lit, spans))
    # shelf packing, tallest first
    order = sorted(painted, key=lambda p: -p[1].shape[0])
    x = y = shelf = 0
    places = {}
    for key, img, _lit, _ in order:
        ch, cw = img.shape[0] + 2 * PAD, img.shape[1] + 2 * PAD
        if x + cw > ATLAS_W:
            x, y, shelf = 0, y + shelf, 0
        places[key] = (x + PAD, y + PAD)
        x += cw
        shelf = max(shelf, ch)
    height = 1
    while height < y + shelf:
        height *= 2
    canvas = np.zeros((height, ATLAS_W, 3), dtype=np.float32)
    canvas[...] = (128, 128, 128)
    night = np.zeros_like(canvas)
    cells = {}
    for key, img, lit, spans in painted:
        px, py = places[key]
        h, w = img.shape[:2]
        for arr, src in ((canvas, img), (night, lit)):
            padded = np.pad(src, ((PAD, PAD), (PAD, PAD), (0, 0)), mode="edge")
            arr[py - PAD : py + h + PAD, px - PAD : px + w + PAD] = padded
        f = by_id[key[0]]
        rows = {}
        bottom = py + h  # image row below the cell
        for name, ph in spans:  # bottom first
            rows[name] = (1 - bottom / height, 1 - (bottom - ph) / height)
            bottom -= ph
        cells[key] = Cell(
            s0=px / ATLAS_W,
            s1=(px + w) / ATLAS_W,
            rows=rows,
            bay_m=f.bay_m,
            ground_m=f.ground_m,
            storey_m=f.storey_m,
        )
    return Atlas(
        image=Image.fromarray(canvas.astype(np.uint8), "RGB"),
        lit=Image.fromarray(night.astype(np.uint8), "RGB"),
        cells=cells,
    )


def hash_seed(key: tuple[str, int]) -> int:
    return sum(ord(c) * (i + 1) for i, c in enumerate(key[0])) + 101 * key[1]
