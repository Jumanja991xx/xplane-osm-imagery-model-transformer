"""What the analysis found in the area: objects and surfaces, ready to be edited.

Every item records where it came from (OSM, the user, the AI) so the UI can
show it and nothing the AI suggested looks like hard fact.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from xpsb.catalog import ObjectCategory, SurfaceType
from xpsb.photos import Photo

ItemKind = Literal["object", "surface"]
ItemSource = Literal["osm", "user", "ai", "memory"]


class AiAssessment(BaseModel):
    model: str
    summary: str  # one sentence for the user, in the UI language
    confidence: float = Field(ge=0, le=1)


class Research(BaseModel):
    """What the research found out about an object (free sources, no AI)."""

    facts: str = ""  # compact text for the AI architect and the user
    wikidata: str | None = None
    sources: list[str] = Field(default_factory=list)


MonumentKind = Literal["statue", "rider", "bust", "obelisk", "column", "stone"]
StatuePose = Literal["standing", "arm_raised", "arms_raised", "pointing", "seated"]
BridgeStructure = Literal["beam", "arch", "truss", "suspension", "cable_stayed"]
StatueHeld = Literal["none", "palm", "wreath", "sword", "torch", "book", "flag"]
WallMaterial = Literal[
    "windows",
    "windows_classic",
    "windows_arched",
    "windows_house",
    "plaster",
    "stone",
    "brick",
    "glass",
    "metal_sheet",
    "concrete",
    "wood",
]


class ItemAttrs(BaseModel):
    """Parameters for the generators (M3). All optional: unknown stays unknown."""

    height_m: float | None = Field(default=None, ge=0, le=300)
    min_height_m: float | None = Field(default=None, ge=0, le=300)  # raised parts (roofs)
    levels: int | None = Field(default=None, ge=0, le=100)
    roof_shape: str | None = None
    roof_color: str | None = None
    wall_color: str | None = None
    seat_color: str | None = None
    material: str | None = None
    covered: bool | None = None
    width_m: float | None = Field(default=None, ge=0, le=200)  # lines (tracks, barriers)
    # Fine-tuning from the 3D preview.
    flip: bool | None = None  # turn the front (e.g. of a grandstand) around
    rows: int | None = Field(default=None, ge=1, le=80)  # grandstand rows
    wall_material: WallMaterial | None = None
    # Statues and monuments (the default monument script reads these).
    monument_kind: MonumentKind | None = None
    pose: StatuePose | None = None
    holding: StatueHeld | None = None
    robe: bool | None = None
    use_scan: bool | None = None  # False: build the figure instead of the 3D scan
    # Placement fine-tuned in the 3D preview: moved (metres east/north) and turned
    # (degrees clockwise) on the ground as a whole - no rebuild needed.
    shift_x_m: float | None = Field(default=None, ge=-1000, le=1000)
    shift_y_m: float | None = Field(default=None, ge=-1000, le=1000)
    turn_deg: float | None = Field(default=None, ge=-720, le=720)
    # Paint the real object out of the aerial ground image under it (its photo and
    # shadow would show beneath the model). None: on for bridges, off otherwise.
    hide_below: bool | None = None
    # Bridges: how it carries its deck (the default bridge script reads this).
    bridge_structure: BridgeStructure | None = None


class Scan(BaseModel):
    """A free 3D scan of the object (Wikimedia Commons), reduced and stored with the
    job (file relative to the job folder); built with scan() in the build script."""

    file: str
    title: str
    author: str | None = None
    license: str | None = None
    page_url: str | None = None
    triangles: int = 0
    up: str = "z"


class BuildingPart(BaseModel):
    """An OSM building:part inside the outline of a building (Simple 3D Buildings):
    the outline is the object, its parts describe the shape in more detail."""

    geometry: dict[str, Any]  # GeoJSON Polygon (lon, lat)
    osm_ref: str | None = None
    height_m: float | None = None
    min_height_m: float | None = None
    roof_shape: str | None = None
    wall_color: str | None = None
    roof_color: str | None = None
    part: str | None = None  # building:part value, e.g. "roof", "tower"


class PhotoTexture(BaseModel):
    """A facade cut out of one of the item's photos (see build/phototex.py): the four
    corners in the photo (fractions of its width/height, from the top left, in the
    order top-left, top-right, bottom-right, bottom-left) and its real size."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,23}$")
    photo_id: str = Field(pattern=r"^[A-Za-z0-9]{1,40}$")
    corners: list[list[float]] = Field(min_length=4, max_length=4)
    width_m: float = Field(ge=0.5, le=400)
    height_m: float = Field(ge=0.5, le=200)
    repeat: bool = False  # a sample that repeats (e.g. two bays of a stadium facade)
    walls: bool = False  # the default script puts it on all walls
    source: Literal["ai", "user"] = "user"

    @field_validator("corners")
    @classmethod
    def _fractions(cls, value: list[list[float]]) -> list[list[float]]:
        for p in value:
            if len(p) != 2 or not all(-0.05 <= c <= 1.05 for c in p):
                raise ValueError("corners are [x, y] fractions of the photo (0..1)")
        return [[round(min(1.0, max(0.0, c)), 5) for c in p] for p in value]


class SceneItem(BaseModel):
    id: str
    kind: ItemKind
    category: ObjectCategory | SurfaceType
    geometry: dict[str, Any]  # GeoJSON Polygon / LineString / Point (lon, lat)
    source: ItemSource
    name: str | None = None
    osm_ref: str | None = None  # e.g. "way/123"
    tags: dict[str, str] = Field(default_factory=dict)
    attrs: ItemAttrs = Field(default_factory=ItemAttrs)
    ai: AiAssessment | None = None
    included: bool = True
    edited: bool = False  # changed by the user after analysis
    # Found by the AI object search; stays out of the plan until the user accepts it.
    proposal: bool = False
    # Taken over from an earlier job in the same area (confirmed there by the user).
    remembered: bool = False
    # The AI is looking at this item right now (drawn by the user, being rated).
    rating: bool = False
    # Own build script written by the AI architect on the user's request.
    script: str | None = None
    script_request: str | None = None
    # Photos of the real object (input for the AI architect, never in the package).
    photos: list[Photo] = Field(default_factory=list)
    # Facades cut out of those photos, painted onto the model (only photos whose
    # licence allows sharing them go into the package).
    textures: list[PhotoTexture] = Field(default_factory=list)
    research: Research | None = None
    scan: Scan | None = None
    parts: list[BuildingPart] = Field(default_factory=list)
    # Bridges: the roads that lead onto it (GeoJSON LineStrings, each starting at
    # the bridge end): the approach ramps follow them down to the ground.
    approaches: list[dict[str, Any]] = Field(default_factory=list)


class ItemPatch(BaseModel):
    """Editor changes; only the fields sent are applied."""

    category: ObjectCategory | SurfaceType | None = None
    geometry: dict[str, Any] | None = None
    name: str | None = Field(default=None, max_length=120)
    attrs: dict[str, Any] | None = None
    included: bool | None = None
    proposal: bool | None = None  # False = accepted


class NewItem(BaseModel):
    kind: ItemKind
    category: ObjectCategory | SurfaceType
    geometry: dict[str, Any]
    name: str | None = Field(default=None, max_length=120)
    attrs: ItemAttrs = Field(default_factory=ItemAttrs)


MAX_ITEM_VERTICES = 2000
OBJECT_GEOMETRIES = {"Polygon", "MultiPolygon", "LineString", "Point"}
SURFACE_GEOMETRIES = {"Polygon", "MultiPolygon"}


def validate_item(data: dict[str, Any]) -> SceneItem:
    """Full check of an item coming from the editor (raises ValueError)."""
    from shapely.geometry import shape

    item = SceneItem.model_validate(data)
    kind_enum = ObjectCategory if item.kind == "object" else SurfaceType
    if item.category not in {c.value for c in kind_enum}:
        raise ValueError("category does not fit kind")
    allowed = OBJECT_GEOMETRIES if item.kind == "object" else SURFACE_GEOMETRIES
    if item.geometry.get("type") not in allowed:
        raise ValueError("geometry type not allowed")
    try:
        geom = shape(item.geometry)
    except Exception as exc:  # noqa: BLE001 - any parse problem is a bad request
        raise ValueError("invalid geometry") from exc
    coords = []
    for part in getattr(geom, "geoms", [geom]):
        if part.geom_type == "Polygon":
            coords += list(part.exterior.coords)
        else:
            coords += list(part.coords)
    if not coords or len(coords) > MAX_ITEM_VERTICES:
        raise ValueError("vertex count")
    if any(not (-180 <= x <= 180 and -85 <= y <= 85) for x, y, *_ in coords):
        raise ValueError("coordinate out of range")
    if geom.geom_type in SURFACE_GEOMETRIES and (not geom.is_valid or geom.area <= 0):
        raise ValueError("polygon is self-intersecting or empty")
    item.category = kind_enum(item.category)
    return item
