"""What the analysis found in the area: objects and surfaces, ready to be edited.

Every item records where it came from (OSM, the user, the AI) so the UI can
show it and nothing the AI suggested looks like hard fact.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from xpsb.catalog import ObjectCategory, SurfaceType

ItemKind = Literal["object", "surface"]
ItemSource = Literal["osm", "user", "ai", "memory"]


class AiAssessment(BaseModel):
    model: str
    summary: str  # one sentence for the user, in the UI language
    confidence: float = Field(ge=0, le=1)


WallMaterial = Literal["windows", "plaster", "brick", "glass", "metal_sheet", "concrete", "wood"]


class ItemAttrs(BaseModel):
    """Parameters for the generators (M3). All optional: unknown stays unknown."""

    height_m: float | None = Field(default=None, ge=0, le=300)
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
