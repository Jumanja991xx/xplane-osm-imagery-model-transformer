"""Life on the buildings: real shops with their names, cornices and oriels on the
facades, dormers, chimneys and roof equipment.

Shops come from open place data (Overture/OSM): a bakery, a pharmacy or a café
gets a shop window with its real name on the sign at the wall nearest to where
it really is. Everything else is placed by rules (like autogen, but per building
and repeatable): the same building always gets the same details.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import Point, Polygon
from shapely.strtree import STRtree

from xpsb.build.materials import _noise

# ---- shops ----------------------------------------------------------------------------

# open place categories -> kind of shop front
SHOP_KINDS = {
    "food": (
        "restaurant",
        "bar",
        "cafe",
        "casual_eatery",
        "coffee_shop",
        "fast_food_restaurant",
        "bakery",
        "pub",
        "ice_cream_shop",
        "dessert_shop",
        "pizza_restaurant",
        "wine_bar",
        "pastry_shop",
        "tea_room",
        "bistro",
    ),
    "shop": (
        "fashion_and_apparel_store",
        "shopping",
        "books_music_and_video_store",
        "electronics_store",
        "flowers_and_gifts_store",
        "sporting_goods_store",
        "second_hand_store",
        "arts_crafts_and_hobby_store",
        "personal_care_and_beauty_store",
        "hardware_home_and_garden_store",
        "jewelry_store",
        "shoe_store",
        "toy_store",
        "clothing_store",
        "furniture_store",
        "optician",
        "eyewear_store",
        "gift_shop",
        "convenience_store",
        "souvenir_shop",
        "department_store",
    ),
    "grocery": ("food_and_beverage_store", "supermarket", "grocery_store", "butcher_shop"),
    "pharmacy": ("pharmacy_and_drug_store", "pharmacy"),
    "service": (
        "bank_or_credit_union",
        "travel_service",
        "real_estate_service",
        "personal_or_beauty_service",
        "financial_service",
        "hair_salon",
        "beauty_salon",
        "nail_salon",
        "barber",
        "dry_cleaning",
        "copy_shop",
    ),
}
_KIND_OF = {c: k for k, cats in SHOP_KINDS.items() for c in cats}
AWNING_KINDS = ("food",)


@dataclass
class Shop:
    name: str
    kind: str
    lon: float
    lat: float
    sign: int = -1  # slot in the sign atlas (-1: plain board)


def shop_kind(category: str | None) -> str | None:
    return _KIND_OF.get(category or "")


def assign_shops(
    footprints: dict[str, Polygon], places: list[dict], max_dist_m: float, to_local
) -> dict[str, list[Shop]]:
    """Each place with a shop front -> the building it is in or next to.
    `footprints` in local metres, `to_local(lon, lat) -> (x, y)`."""
    ids = list(footprints)
    tree = STRtree([footprints[i] for i in ids])
    out: dict[str, list[Shop]] = {}
    for p in places:
        kind = shop_kind(p.get("category"))
        if kind is None or not p.get("name"):
            continue
        x, y = to_local(p["lon"], p["lat"])
        pt = Point(x, y)
        near = tree.query(pt.buffer(max_dist_m))
        if len(near) == 0:
            continue
        best = min(near, key=lambda i: footprints[ids[i]].exterior.distance(pt))
        if footprints[ids[best]].exterior.distance(pt) > max_dist_m:
            continue
        out.setdefault(ids[best], []).append(Shop(p["name"], kind, p["lon"], p["lat"]))
    return out


# ---- sign atlas -----------------------------------------------------------------------

ATLAS = 4096
SIGN_W, SIGN_H = 256, 28
WINDOW_W, WINDOW_H = 256, 144
DESIGNS_PER_KIND = 4
AWNING_H = 64
SIGN_COLORS = {
    "food": [(92, 30, 28), (40, 52, 44), (24, 24, 26), (110, 70, 34)],
    "shop": [(20, 20, 22), (236, 234, 228), (40, 50, 90), (120, 30, 60)],
    "grocery": [(30, 96, 50), (200, 40, 36), (30, 60, 120)],
    "pharmacy": [(24, 120, 70)],
    "service": [(20, 40, 90), (60, 60, 64), (200, 200, 196)],
}
AWNINGS = [
    ((178, 34, 34), (236, 232, 224)),
    ((30, 90, 50), (236, 232, 224)),
    ((110, 24, 40), (110, 24, 40)),
    ((26, 40, 80), (26, 40, 80)),
    ((40, 40, 40), (40, 40, 40)),
]


@dataclass
class SignAtlas:
    image: Image.Image
    lit: Image.Image
    windows: dict[str, list[tuple[float, float, float, float]]]  # kind -> uv rects
    awnings: list[tuple[float, float, float, float]]
    signs: list[tuple[float, float, float, float]]  # slot -> uv rect
    plain: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)


def _h(text: str) -> int:
    return int.from_bytes(hashlib.sha1(text.encode()).digest()[:8], "big")


def _font(size: int):
    for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf", "LiberationSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    for path in Path("/usr/share/fonts").rglob("DejaVuSans-Bold.ttf"):
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def _uv(x: int, y: int, w: int, h: int) -> tuple[float, float, float, float]:
    """Pixel rect -> (s0, t0, s1, t1), t up."""
    return (x / ATLAS, 1 - (y + h) / ATLAS, (x + w) / ATLAS, 1 - y / ATLAS)


def _window(kind: str, variant: int) -> tuple[np.ndarray, np.ndarray]:
    """A shop window seen from the street: frame, door, interior, and its night glow."""
    h, w = WINDOW_H, WINDOW_W
    u, v = np.meshgrid(np.arange(w) / w * 5.0, (h - np.arange(h)) / h * 2.9)
    img = np.zeros((h, w, 3), np.float32)
    seed = variant * 13 + len(kind)
    if kind == "food":
        back = np.array((70, 46, 30), np.float32)
        img[...] = back * (1 + 0.15 * _noise(u, v, 0.4, seed)[..., None])
        tables = (v > 0.7) & (v < 0.78) & ((u % 1.4) < 0.8)
        img[tables] = (150, 110, 70)
        lamps = ((u % 1.4 - 0.4) ** 2 + (v - 2.3) ** 2) < 0.012
        img[lamps] = (255, 220, 160)
        glow = np.clip(1.2 - np.abs(v - 1.6), 0.3, 1)[..., None] * np.array((255, 190, 120))
    elif kind == "pharmacy":
        img[...] = (226, 236, 230)
        shelves = (v % 0.45) < 0.06
        img[shelves] = (170, 190, 180)
        boxes = ~shelves & (_noise(u, v, 0.12, seed) > 0.3)
        img[boxes] = (60, 150, 100)
        glow = np.full((h, w, 3), (210, 240, 225), np.float32)
    elif kind == "grocery":
        img[...] = (210, 206, 196)
        shelves = (v % 0.5) < 0.07
        img[shelves] = (120, 110, 100)
        goods = ~shelves & (v < 2.0)
        palette = np.array([(200, 60, 40), (240, 180, 40), (90, 150, 60), (230, 120, 40)])
        idx = (np.floor(u / 0.25) + np.floor(v / 0.5) * 7 + seed).astype(int) % len(palette)
        img[goods] = palette[idx[goods]]
        glow = np.full((h, w, 3), (255, 245, 225), np.float32)
    elif kind == "service":
        img[...] = (180, 190, 196)
        frost = (v > 0.4) & (v < 1.7)
        img[frost] = (214, 220, 224)
        glow = np.full((h, w, 3), (230, 236, 255), np.float32) * 0.7
    else:  # shop: bright, things on display
        img[...] = (236, 232, 226)
        palette = np.array([(30, 30, 30), (180, 40, 50), (60, 80, 140), (200, 180, 140)])
        items = ((u % 0.9) > 0.3) & ((u % 0.9) < 0.6) & (v > 0.3) & (v < 1.9)
        idx = (np.floor(u / 0.9) + seed).astype(int) % len(palette)
        img[items] = palette[idx[items]]
        glow = np.full((h, w, 3), (255, 248, 236), np.float32)
    # glass: a little sky reflection
    img = img * 0.75 + np.array((150, 170, 190)) * 0.25 * np.clip(v / 2.9, 0, 1)[..., None]
    frame = (u < 0.06) | (u > 4.94) | (v < 0.06) | (v > 2.84) | (np.abs(u - 3.6) < 0.04)
    door = (u > 3.64) & (u < 4.9) & (v < 2.3)
    img[door] *= 0.55
    img[frame] = (40, 40, 42)
    glow = glow * 0.85
    glow[frame] = 0
    return img, glow


def _sign_board(name: str, kind: str, color, font) -> tuple[Image.Image, Image.Image]:
    board = Image.new("RGB", (SIGN_W, SIGN_H), tuple(color))
    lum = sum(color) / 3
    ink = (30, 30, 30) if lum > 150 else ((255, 226, 150) if kind == "food" else (250, 250, 246))
    d = ImageDraw.Draw(board)
    text = name.strip()
    if kind == "pharmacy":  # green cross on the left
        d.rectangle((8, 11, 22, 17), fill=(250, 250, 250))
        d.rectangle((12, 7, 18, 21), fill=(250, 250, 250))
    size = 22
    while size > 9:
        f = font(size)
        if d.textlength(text, font=f) <= SIGN_W - 40:
            break
        size -= 1
    f = font(size)
    if d.textlength(text, font=f) > SIGN_W - 40:
        while text and d.textlength(text + "…", font=f) > SIGN_W - 40:
            text = text[:-1]
        text += "…"
    tw = d.textlength(text, font=f)
    d.text(
        ((SIGN_W - tw) / 2 + (8 if kind == "pharmacy" else 0), SIGN_H / 2),
        text,
        fill=ink,
        font=f,
        anchor="lm",
    )
    lit = Image.new("RGB", (SIGN_W, SIGN_H), (0, 0, 0))
    ImageDraw.Draw(lit).text(
        ((SIGN_W - tw) / 2 + (8 if kind == "pharmacy" else 0), SIGN_H / 2),
        text,
        fill=tuple(int(c * 0.95) for c in ink),
        font=f,
        anchor="lm",
    )
    return board, lit


def build_signs(shops: list[Shop]) -> SignAtlas:
    """Window designs per kind, awnings, and one sign per shop (as many as fit)."""
    img = Image.new("RGB", (ATLAS, ATLAS), (60, 60, 60))
    lit = Image.new("RGB", (ATLAS, ATLAS), (0, 0, 0))
    windows: dict[str, list] = {}
    x = y = 0
    for kind in SHOP_KINDS:
        for k in range(DESIGNS_PER_KIND):
            if x + WINDOW_W > ATLAS:
                x, y = 0, y + WINDOW_H
            a, g = _window(kind, k)
            img.paste(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)), (x, y))
            lit.paste(Image.fromarray(np.clip(g, 0, 255).astype(np.uint8)), (x, y))
            windows.setdefault(kind, []).append(_uv(x + 1, y + 1, WINDOW_W - 2, WINDOW_H - 2))
            x += WINDOW_W
    y += WINDOW_H
    x = 0
    awnings = []
    for c1, c2 in AWNINGS:
        stripe = Image.new("RGB", (WINDOW_W, AWNING_H), c1)
        d = ImageDraw.Draw(stripe)
        for sx in range(0, WINDOW_W, 32):
            d.rectangle((sx + 16, 0, sx + 31, AWNING_H - 10), fill=c2)
        d.rectangle((0, AWNING_H - 10, WINDOW_W, AWNING_H), fill=c1)  # valance
        img.paste(stripe, (x, y))
        awnings.append(_uv(x + 1, y + 1, WINDOW_W - 2, AWNING_H - 2))
        x += WINDOW_W
    y += AWNING_H
    plain = {}
    x = 0
    for kind, colors in SIGN_COLORS.items():
        img.paste(Image.new("RGB", (SIGN_W, SIGN_H), colors[0]), (x, y))
        plain[kind] = _uv(x + 1, y + 1, SIGN_W - 2, SIGN_H - 2)
        x += SIGN_W
    y += SIGN_H
    signs = []
    x = 0
    for shop in shops:
        if x + SIGN_W > ATLAS:
            x, y = 0, y + SIGN_H
        if y + SIGN_H > ATLAS:
            break
        colors = SIGN_COLORS[shop.kind]
        board, glow = _sign_board(shop.name, shop.kind, colors[_h(shop.name) % len(colors)], _font)
        img.paste(board, (x, y))
        lit.paste(glow, (x, y))
        shop.sign = len(signs)
        signs.append(_uv(x + 1, y + 1, SIGN_W - 2, SIGN_H - 2))
        x += SIGN_W
    return SignAtlas(img, lit, windows, awnings, signs, plain)


# ---- placement rules ------------------------------------------------------------------

HISTORIC = ("eclectic", "eclectic_stone", "civic", "townhouse", "oldtown_house")
DORMER_STYLES = HISTORIC + ("house", "house_balcony", "terrace")
ERKER_STYLES = ("eclectic", "eclectic_stone", "townhouse")


def chance(key: str, share: float) -> bool:
    return (_h(key) % 10_000) / 10_000 < share


def along(p, q, t: float, inward: float) -> tuple[float, float]:
    """Point at distance t along p->q, `inward` metres to its left."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    return (p[0] + ux * t - uy * inward, p[1] + uy * t + ux * inward)


def party_wall(p, q, others: list[Polygon], own: Polygon) -> bool:
    """Whether the wall p->q (inside on its left) touches a neighbour: there is
    another building just outside it (no windows, shops or oriels there)."""
    mid = along(p, q, math.dist(p, q) / 2, -0.8)
    pt = Point(mid)
    return any(o is not own and o.contains(pt) for o in others)
