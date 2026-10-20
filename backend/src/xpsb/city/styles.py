"""The style kit of a city: what its facades look like and which building gets which.

A style is a set of parameters (wall material and colours, window shape and
rhythm, storey heights, ground floor, balconies ...), not a fixed picture: the
painter turns it into texture cells, each colour into its own cell. A region
preset bundles styles with rules (use, storeys, roof shape, size) - written by
hand here, later also by the AI for any city of the world (same JSON schema).
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, Field

from xpsb.city.model import CityBuilding

RGB = tuple[int, int, int]

Wall = Literal["plaster", "brick", "concrete", "panel", "metal", "wood", "stone", "glass"]
Window = Literal["punched", "tall", "arched", "band", "curtain", "hall", "small", "none"]
Ground = Literal["same", "shop", "plinth", "garage", "hall_door", "lobby"]
Roof = Literal["tiles", "slate", "metal", "gravel", "green", "copper"]


class FacadeStyle(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    wall: Wall = "plaster"
    colors: list[RGB] = Field(min_length=1, max_length=12)
    trim: RGB = (238, 236, 230)  # window surrounds, balcony slabs, bands
    window: Window = "punched"
    window_frame: RGB = (240, 240, 236)
    glass: RGB = (62, 74, 88)
    bay_m: float = Field(default=3.4, ge=1.5, le=12)  # window axis spacing
    win_w_m: float = Field(default=1.2, ge=0.3, le=10)
    win_h_m: float = Field(default=1.4, ge=0.3, le=6)
    sill_m: float = Field(default=0.9, ge=0, le=3)
    storey_m: float = Field(default=2.9, ge=2.2, le=8)
    ground_m: float = Field(default=3.0, ge=2.2, le=10)
    ground: Ground = "same"
    shutters: RGB | None = None
    balconies: Literal["none", "some", "all"] = "none"
    balcony_color: RGB | None = None  # railing (None: trim colour)
    bands: bool = False  # string course under each storey
    lit_share: float = Field(default=0.3, ge=0, le=1)


class RoofStyle(BaseModel):
    material: Roof = "tiles"
    colors: list[RGB] = Field(min_length=1, max_length=12)


class Rule(BaseModel):
    uses: list[str] = Field(default_factory=lambda: ["residential"])
    min_levels: int = 0
    max_levels: int = 200
    roofs: list[str] = Field(default_factory=list)  # empty: any roof shape
    min_area_m2: float = 0
    max_area_m2: float = 1e9
    weight: float = 1.0


class StyleEntry(BaseModel):
    facade: FacadeStyle
    roof: RoofStyle
    rule: Rule


class RegionPreset(BaseModel):
    id: str
    name: str
    styles: list[StyleEntry] = Field(min_length=1)


# ---- presets ------------------------------------------------------------------------

_WHITE_CREAM = [(240, 236, 226), (236, 228, 208), (244, 240, 232), (232, 222, 196)]
_PASTEL = [(232, 214, 176), (226, 200, 168), (214, 222, 206), (236, 222, 196), (220, 210, 196)]
_RED_TILES = [(150, 70, 52), (126, 58, 46), (168, 88, 62), (110, 62, 52)]
_GREY_ROOF = [(92, 92, 96), (110, 108, 106), (70, 72, 78)]
_FLAT_ROOF = [(128, 126, 122), (150, 148, 142), (104, 104, 104)]


def _entry(facade: dict, roof: dict, rule: dict) -> StyleEntry:
    return StyleEntry(facade=FacadeStyle(**facade), roof=RoofStyle(**roof), rule=Rule(**rule))


CENTRAL_EUROPE = RegionPreset(
    id="central_europe",
    name="Mitteleuropa (Vorort und Stadt)",
    styles=[
        _entry(  # single family house, white or cream, shutters on some
            dict(
                id="house",
                colors=_WHITE_CREAM + _PASTEL[:2],
                storey_m=2.75,
                ground_m=2.8,
                bay_m=3.6,
                win_w_m=1.15,
                win_h_m=1.3,
                sill_m=0.95,
            ),
            dict(material="tiles", colors=_RED_TILES + _GREY_ROOF[:1]),
            dict(uses=["residential"], max_levels=3, max_area_m2=260, weight=3),
        ),
        _entry(  # Bavarian house with wooden balcony and shutters
            dict(
                id="house_balcony",
                colors=_WHITE_CREAM,
                storey_m=2.75,
                ground_m=2.8,
                bay_m=3.4,
                win_w_m=1.1,
                win_h_m=1.3,
                shutters=(92, 62, 40),
                balconies="some",
                balcony_color=(120, 82, 50),
            ),
            dict(material="tiles", colors=_RED_TILES),
            dict(uses=["residential"], max_levels=3, max_area_m2=320, roofs=["gabled"]),
        ),
        _entry(  # terraced / small apartment house
            dict(
                id="terrace",
                colors=_PASTEL + _WHITE_CREAM[:2],
                storey_m=2.8,
                ground_m=2.9,
                bay_m=3.0,
                win_w_m=1.2,
                win_h_m=1.4,
            ),
            dict(material="tiles", colors=_RED_TILES + _GREY_ROOF),
            dict(uses=["residential"], max_levels=4, min_area_m2=120, max_area_m2=700),
        ),
        _entry(  # 1960s-80s apartment block with balcony rows
            dict(
                id="block_70s",
                colors=[(222, 214, 196), (210, 206, 198), (226, 216, 186), (200, 196, 190)],
                window="band",
                storey_m=2.85,
                ground_m=2.9,
                bay_m=3.2,
                win_w_m=2.4,
                win_h_m=1.35,
                balconies="all",
                balcony_color=(150, 146, 140),
            ),
            dict(material="gravel", colors=_FLAT_ROOF),
            dict(uses=["residential"], min_levels=4, max_levels=12, min_area_m2=250, weight=1.5),
        ),
        _entry(  # 1990s-2020s apartment house, white with glass balconies
            dict(
                id="block_new",
                colors=[(244, 242, 236), (232, 230, 226), (220, 214, 204)],
                storey_m=2.9,
                ground_m=3.0,
                bay_m=3.3,
                win_w_m=1.6,
                win_h_m=1.9,
                sill_m=0.5,
                balconies="some",
                balcony_color=(150, 170, 180),
                window_frame=(70, 72, 76),
            ),
            dict(material="gravel", colors=_FLAT_ROOF + _GREY_ROOF[:1]),
            dict(uses=["residential"], min_levels=3, max_levels=10, min_area_m2=300),
        ),
        _entry(  # high-rise
            dict(
                id="tower",
                colors=[(206, 204, 198), (190, 188, 182), (214, 208, 194)],
                wall="panel",
                window="band",
                storey_m=2.85,
                ground_m=3.2,
                bay_m=3.6,
                win_w_m=2.6,
                win_h_m=1.4,
                balconies="some",
            ),
            dict(material="gravel", colors=_FLAT_ROOF),
            dict(uses=["residential"], min_levels=9, weight=2),
        ),
        _entry(  # town-centre house of the 19th century, shop on the ground floor
            dict(
                id="townhouse",
                colors=_PASTEL + [(222, 196, 150), (196, 176, 150)],
                window="tall",
                storey_m=3.6,
                ground_m=4.2,
                bay_m=3.6,
                win_w_m=1.25,
                win_h_m=2.2,
                sill_m=0.9,
                ground="shop",
                bands=True,
            ),
            dict(material="tiles", colors=_RED_TILES + _GREY_ROOF),
            dict(
                uses=["residential", "commercial"],
                min_levels=3,
                max_levels=6,
                roofs=["gabled", "hipped", "mansard", "mixed"],
                min_area_m2=150,
                weight=0.6,
            ),
        ),
        _entry(  # office, glass
            dict(
                id="office_glass",
                colors=[(90, 110, 124), (70, 92, 104), (110, 126, 132)],
                wall="glass",
                window="curtain",
                storey_m=3.6,
                ground_m=4.2,
                bay_m=3.0,
                ground="lobby",
                glass=(74, 98, 118),
                lit_share=0.45,
            ),
            dict(material="gravel", colors=_FLAT_ROOF),
            dict(uses=["commercial", "public"], min_levels=3, roofs=["flat"]),
        ),
        _entry(  # office of the 70s-90s, ribbon windows
            dict(
                id="office_band",
                colors=[(216, 210, 198), (196, 190, 180), (168, 160, 150)],
                wall="concrete",
                window="band",
                storey_m=3.4,
                ground_m=3.8,
                bay_m=3.6,
                win_w_m=3.2,
                win_h_m=1.5,
                sill_m=0.9,
                ground="lobby",
                lit_share=0.4,
            ),
            dict(material="gravel", colors=_FLAT_ROOF),
            dict(uses=["commercial", "public"], min_levels=2, roofs=["flat", "mixed"]),
        ),
        _entry(  # shop / supermarket
            dict(
                id="retail",
                colors=[(232, 230, 224), (210, 206, 200), (190, 60, 50)],
                wall="metal",
                window="none",
                storey_m=5.0,
                ground_m=4.5,
                bay_m=6.0,
                ground="shop",
            ),
            dict(material="metal", colors=[(150, 152, 156), (120, 124, 130)]),
            dict(uses=["commercial"], max_levels=2, min_area_m2=600),
        ),
        _entry(  # commercial hall
            dict(
                id="hall",
                colors=[(196, 200, 204), (150, 166, 186), (226, 228, 228), (170, 170, 164)],
                wall="metal",
                window="hall",
                storey_m=6.0,
                ground_m=6.0,
                bay_m=6.0,
                win_w_m=4.0,
                win_h_m=0.9,
                ground="hall_door",
                lit_share=0.15,
            ),
            dict(material="metal", colors=[(150, 152, 156), (176, 178, 180), (120, 124, 130)]),
            dict(uses=["industrial", "commercial"], max_levels=3, min_area_m2=250, weight=2),
        ),
        _entry(  # garage
            dict(
                id="garage",
                colors=[(214, 210, 202), (190, 186, 180)],
                wall="plaster",
                window="none",
                storey_m=2.6,
                ground_m=2.6,
                bay_m=3.0,
                ground="garage",
                lit_share=0,
            ),
            dict(material="gravel", colors=_FLAT_ROOF + _RED_TILES[:1]),
            dict(uses=["garage", "residential"], max_levels=1, max_area_m2=80, weight=4),
        ),
        _entry(  # barn / farm building
            dict(
                id="farm",
                colors=[(120, 84, 54), (104, 72, 48), (140, 104, 70)],
                wall="wood",
                window="small",
                storey_m=3.2,
                ground_m=3.2,
                bay_m=4.0,
                win_w_m=0.8,
                win_h_m=0.8,
                sill_m=1.6,
                lit_share=0.05,
            ),
            dict(material="tiles", colors=_RED_TILES + _GREY_ROOF[:1]),
            dict(uses=["farm"], weight=3),
        ),
        _entry(  # school, town hall, public building
            dict(
                id="public",
                colors=[(236, 228, 212), (226, 214, 188), (214, 210, 204)],
                storey_m=3.6,
                ground_m=3.8,
                bay_m=3.2,
                win_w_m=2.0,
                win_h_m=1.9,
                sill_m=0.9,
                window_frame=(90, 90, 92),
                lit_share=0.2,
            ),
            dict(material="tiles", colors=_RED_TILES + _FLAT_ROOF[:1]),
            dict(uses=["public"], weight=2),
        ),
        _entry(  # church
            dict(
                id="church",
                colors=[(240, 236, 226), (222, 214, 196)],
                wall="plaster",
                window="arched",
                storey_m=8.0,
                ground_m=8.0,
                bay_m=5.0,
                win_w_m=1.8,
                win_h_m=4.5,
                sill_m=2.5,
                lit_share=0.0,
            ),
            dict(material="tiles", colors=_RED_TILES + [(80, 112, 96)]),
            dict(uses=["religious"], weight=5),
        ),
    ],
)

_ECLECTIC = [
    (214, 192, 150),  # ochre
    (196, 186, 170),  # grey-beige
    (226, 210, 170),  # sand
    (176, 168, 158),  # weathered grey
    (222, 200, 178),  # pale pink
    (232, 220, 186),  # pale yellow
    (190, 170, 140),  # darker ochre
    (206, 202, 192),  # light grey
]
_TIN_SLATE = [(84, 86, 90), (104, 104, 102), (70, 74, 80), (122, 92, 76)]

# Historic city centre of Central Europe (Budapest, Vienna, Prague): perimeter
# blocks of the late 19th century with courtyards, interwar houses, some modern.
CENTRAL_EUROPE_CITY = RegionPreset(
    id="central_europe_city",
    name="Mitteleuropa (historische Innenstadt)",
    styles=[
        _entry(  # eclectic / Gruenderzeit apartment house, shops on the ground floor
            dict(
                id="eclectic",
                colors=_ECLECTIC,
                window="tall",
                storey_m=3.9,
                ground_m=4.6,
                bay_m=3.5,
                win_w_m=1.3,
                win_h_m=2.3,
                sill_m=0.9,
                ground="shop",
                bands=True,
                trim=(232, 224, 206),
            ),
            dict(material="slate", colors=_TIN_SLATE + _RED_TILES[:2]),
            dict(uses=["residential", "commercial"], min_levels=3, max_levels=8, weight=5),
        ),
        _entry(  # richer stone facade, round-arched windows (banks, palaces, hotels)
            dict(
                id="eclectic_stone",
                colors=[(206, 198, 180), (190, 182, 166), (220, 212, 192)],
                wall="stone",
                window="arched",
                storey_m=4.4,
                ground_m=5.0,
                bay_m=4.0,
                win_w_m=1.5,
                win_h_m=2.8,
                sill_m=1.0,
                ground="plinth",
                bands=True,
                trim=(226, 220, 204),
            ),
            dict(material="slate", colors=_TIN_SLATE),
            dict(
                uses=["residential", "commercial", "public"],
                min_levels=3,
                max_levels=8,
                min_area_m2=400,
                weight=1.2,
            ),
        ),
        _entry(  # 1930s modern apartment house (Bauhaus influence): wide windows, balconies
            dict(
                id="interwar",
                colors=[(232, 228, 220), (220, 216, 206), (214, 206, 190), (200, 200, 196)],
                window="punched",
                storey_m=3.2,
                ground_m=3.6,
                bay_m=3.3,
                win_w_m=1.7,
                win_h_m=1.5,
                sill_m=0.9,
                balconies="some",
                balcony_color=(210, 206, 198),
                ground="shop",
            ),
            dict(material="gravel", colors=_FLAT_ROOF),
            dict(uses=["residential"], min_levels=4, max_levels=9, weight=1.3),
        ),
        _entry(  # small older house (2 storeys) in the old town
            dict(
                id="oldtown_house",
                colors=_ECLECTIC[:6],
                window="punched",
                storey_m=3.4,
                ground_m=3.6,
                bay_m=3.2,
                win_w_m=1.1,
                win_h_m=1.7,
                sill_m=1.0,
                shutters=None,
                bands=True,
            ),
            dict(material="tiles", colors=_RED_TILES + _TIN_SLATE[:1]),
            dict(uses=["residential", "commercial"], max_levels=2, weight=2),
        ),
        _entry(  # public building / ministry / university, stone
            dict(
                id="civic",
                colors=[(214, 206, 188), (198, 190, 172), (224, 216, 198)],
                wall="stone",
                window="tall",
                storey_m=4.8,
                ground_m=5.2,
                bay_m=4.2,
                win_w_m=1.6,
                win_h_m=2.8,
                sill_m=1.0,
                ground="plinth",
                bands=True,
                lit_share=0.25,
            ),
            dict(material="slate", colors=_TIN_SLATE + [(80, 112, 96)]),
            dict(uses=["public"], weight=3),
        ),
        _entry(
            dict(
                id="modern_office",
                colors=[(90, 110, 124), (110, 126, 132), (70, 92, 104)],
                wall="glass",
                window="curtain",
                storey_m=3.6,
                ground_m=4.4,
                bay_m=3.0,
                ground="lobby",
                glass=(80, 100, 116),
                lit_share=0.45,
            ),
            dict(material="gravel", colors=_FLAT_ROOF),
            dict(uses=["commercial"], min_levels=3, roofs=["flat"], weight=1),
        ),
        _entry(  # socialist-era block between the old houses
            dict(
                id="block_60s",
                colors=[(206, 202, 194), (190, 186, 178), (214, 206, 186)],
                wall="panel",
                window="band",
                storey_m=2.9,
                ground_m=3.4,
                bay_m=3.4,
                win_w_m=2.5,
                win_h_m=1.4,
                ground="shop",
                balconies="some",
            ),
            dict(material="gravel", colors=_FLAT_ROOF),
            dict(uses=["residential"], min_levels=5, roofs=["flat"], weight=1),
        ),
        *[s for s in CENTRAL_EUROPE.styles if s.facade.id in ("church", "garage", "hall")],
    ],
)

PRESETS = {p.id: p for p in (CENTRAL_EUROPE, CENTRAL_EUROPE_CITY)}


# ---- assignment ---------------------------------------------------------------------


def _h(text: str) -> int:
    return int.from_bytes(hashlib.sha1(text.encode()).digest()[:8], "big")


def levels_of(b: CityBuilding, storey_m: float = 3.0) -> int:
    if b.levels:
        return b.levels
    return max(1, round(max(b.eave_m, 2.5) / storey_m))


def _fits(rule: Rule, b: CityBuilding, area: float, levels: int) -> bool:
    return (
        b.use in rule.uses
        and rule.min_levels <= levels <= rule.max_levels
        and rule.min_area_m2 <= area <= rule.max_area_m2
        and (not rule.roofs or b.roof_type in rule.roofs)
    )


def assign(b: CityBuilding, area_m2: float, preset: RegionPreset) -> tuple[StyleEntry, int, int]:
    """(style, wall colour index, roof colour index) - repeatable for the same building.
    Falls back to the closest rule ignoring roof shape and size, then to any style."""
    levels = levels_of(b)
    pool = [s for s in preset.styles if _fits(s.rule, b, area_m2, levels)]
    if not pool:
        pool = [
            s
            for s in preset.styles
            if b.use in s.rule.uses and s.rule.min_levels <= levels <= s.rule.max_levels
        ]
    if not pool:
        pool = [s for s in preset.styles if b.use in s.rule.uses] or preset.styles
    h = _h(b.id)
    total = sum(s.rule.weight for s in pool)
    pick = (h % 10_000) / 10_000 * total
    for style in pool:
        pick -= style.rule.weight
        if pick <= 0:
            break
    wall = (h >> 20) % len(style.facade.colors)
    roof = (h >> 32) % len(style.roof.colors)
    return style, int(wall), int(roof)
