"""Data models of a job. Everything the user decides is recorded here."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from xpsb.catalog import ObjectCategory, SurfaceMode, SurfaceType
from xpsb.geo import GeoJsonPolygon
from xpsb.imagery.wms import ImageryInfo
from xpsb.scene import SceneItem
from xpsb.settings import AiModel


def utcnow() -> datetime:
    return datetime.now(UTC)


class JobState(StrEnum):
    DRAFT = "draft"
    CLARIFIED = "clarified"
    ANALYZING = "analyzing"
    ANALYZED = "analyzed"
    PLANNING = "planning"  # the AI architect designs the important objects
    PLANNED = "planned"
    BUILDING = "building"
    BUILT = "built"
    INSTALLING = "installing"
    INSTALLED = "installed"
    FAILED = "failed"
    CANCELLED = "cancelled"


RUNNING_STATES = {
    JobState.ANALYZING,
    JobState.PLANNING,
    JobState.BUILDING,
    JobState.INSTALLING,
}

GateId = Literal["A", "B", "C", "D"]


class ExclusionType(StrEnum):
    OBJECTS = "objects"
    FACADES = "facades"
    FORESTS = "forests"
    POLYGONS = "polygons"
    ROADS = "roads"


class ExclusionScope(StrEnum):
    # Only where the new objects stand (one rectangle per object): neighbours stay.
    OBJECTS = "objects"
    AREA = "area"  # the whole drawn area


class DetailLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


PACKAGE_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9 _\-]{0,59}$"


SCAN_MODEL = "claude-sonnet-5-5"


def scan_model(brief) -> str:
    """The model for looking at the aerial image (object search, rating)."""
    if brief.ai_economy and "opus" in brief.ai_model:
        return SCAN_MODEL
    return brief.ai_model


class Brief(BaseModel):
    """Step 2: what exactly should be built (answers of the clarification dialog)."""

    object_categories: list[ObjectCategory] = Field(default_factory=list)
    surface_types: list[SurfaceType] = Field(default_factory=list)
    surface_mode: SurfaceMode = SurfaceMode.MATERIAL
    exclusions: list[ExclusionType] = Field(default_factory=list)
    exclusion_scope: ExclusionScope = ExclusionScope.OBJECTS
    use_ai: bool = True
    ai_model: AiModel = "claude-opus-5-5"
    # Saves money: looking at the aerial image (search, rating, "find similar") runs
    # on Sonnet when Opus is chosen; the AI architect keeps the chosen model.
    ai_economy: bool = True
    ai_cost_limit_usd: float = Field(default=5.0, ge=0, le=1000)
    # Search the aerial image for objects OSM does not know (proposals for the user).
    ai_detect: bool = True
    # Look up facts and photos of important objects (Wikidata, Wikipedia, Commons, ...).
    research: bool = True
    # Which objects the AI architect builds individually when the plan is made.
    ai_design: Literal["important", "all", "off"] = "important"
    detail: DetailLevel = DetailLevel.MEDIUM
    package_name: str = Field(pattern=PACKAGE_NAME_PATTERN)
    notes: str = Field(default="", max_length=2000)

    @field_validator("object_categories", "surface_types", "exclusions")
    @classmethod
    def _unique_sorted(cls, values: list) -> list:
        return sorted(set(values))

    @field_validator("package_name", mode="before")
    @classmethod
    def _strip_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("package_name")
    @classmethod
    def _reserved_name(cls, value: str) -> str:
        if value.upper() in {"CON", "PRN", "AUX", "NUL"}:
            raise ValueError("invalid_package_name")
        return value

    @model_validator(mode="after")
    def _something_to_build(self) -> Brief:
        if not self.object_categories and not self.surface_types:
            raise ValueError("nothing_selected")
        return self


Params = dict[str, str | int | float | None]


class QuestionOption(BaseModel):
    value: str
    label_key: str | None = None
    label: str | None = None  # free text (AI questions come in the UI language)
    params: Params = Field(default_factory=dict)


class Question(BaseModel):
    """A question the analysis could not answer with confidence."""

    id: str
    text_key: str | None = None
    text: str | None = None
    params: Params = Field(default_factory=dict)
    options: list[QuestionOption]
    suggested: str | None = None
    # Answering sets this field of this item (e.g. category, height_m).
    item_id: str | None = None
    field: str | None = None


class Note(BaseModel):
    key: str
    params: Params = Field(default_factory=dict)


class CallCost(BaseModel):
    """One AI call as billed: the token counts the API reported, priced with the list
    prices in ai/usage.py. `stage`/`item` are filled in by the job's cost book."""

    stage: str = ""  # analysis | rating | similar | design | correction
    item: str | None = None  # object name for designs and corrections
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    usd: float = 0.0
    at: datetime = Field(default_factory=utcnow)


class AiRunInfo(BaseModel):
    model: str
    models_used: list[str] = Field(default_factory=list)  # differs on server-side fallback
    cost_usd: float = 0.0
    analyzed: int = 0
    skipped: int = 0
    tiles_searched: int = 0
    proposals: int = 0
    # Finds of the object search that were left out, by reason (shown to the user).
    dropped: dict[str, int] = Field(default_factory=dict)
    limit_reached: bool = False
    error_key: str | None = None
    error_detail: str | None = None


class AnalysisResult(BaseModel):
    items: list[SceneItem] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)
    imagery: ImageryInfo | None = None
    ai: AiRunInfo | None = None
    next_user_id: int = 1


class PlanItem(BaseModel):
    kind: str
    label_key: str
    count: int
    params: Params = Field(default_factory=dict)


class LibraryPlacement(BaseModel):
    """An object of the X-Plane library placed by a build script."""

    path: str
    x: float  # metres east of the object's anchor
    y: float  # metres north
    heading: float
    size: list[float] = Field(default_factory=list)  # width, depth, height if known
    pack: str = ""
    default: bool = True  # part of X-Plane itself


class PlanObject(BaseModel):
    """One 3D object of the plan, as generated for the preview."""

    item_id: str
    stem: str
    category: str
    name: str | None = None
    anchor: tuple[float, float]  # lon, lat
    status: Literal["ok", "error", "skipped"]
    message: str = ""
    triangles: int = 0
    has_mesh: bool = True
    designed: bool = False  # own build script by the AI architect
    lit: bool = False  # lit windows/lamps at night
    lights: int = 0  # X-Plane lights (floodlights, street lamps)
    photo_textures: list[str] = Field(default_factory=list)  # photos painted onto it
    scan: bool = False  # built from a free 3D scan (Wikimedia Commons)
    heading: float = 0.0  # degrees clockwise, as turned in the 3D preview
    extent: tuple[float, float, float, float] | None = None  # local metres: w, s, e, n
    top_m: float | None = None  # highest point as built (for "make them 20 % lower")
    shaded: bool = False  # soft shading baked in by Blender (package build only)
    detailed: bool = False  # fine detail baked into the normal map by Blender
    weathered: bool = False  # weathering baked into the texture by Blender
    library: list[LibraryPlacement] = Field(default_factory=list)


class BuildPlan(BaseModel):
    package_name: str
    tile: str
    items: list[PlanItem]
    files: list[str]
    center: tuple[float, float]  # lon, lat
    detail: str = "medium"
    objects: list[PlanObject] = Field(default_factory=list)
    surfaces: int = 0
    surface_mode: str = "material"
    exclusions: list[str] = Field(default_factory=list)  # kinds, e.g. "objects"
    exclusion_scope: str = "objects"
    exclusion_rects: int = 0  # rectangles per kind
    # Objects/polygons of other packs on our objects' places (xplane/foreign.py):
    # {kind, bounds [w, s, e, n], pack, path} - each gets its own exclusion zone.
    foreign_zones: list[dict] = Field(default_factory=list)
    test_scenery: bool = False
    cube_height_m: float | None = None


class BuildResult(BaseModel):
    package_dir: str
    files: list[str]
    dsftool_version: str
    blender: str | None = None  # Blender version used for the soft shading
    shaded: int = 0  # objects Blender shaded
    detailed: int = 0  # of them with fine facade detail baked into the normal map
    blender_problem: str = ""  # why Blender left objects unshaded (see build/blender.log)
    blender_log: str | None = None
    shaded_stems: list[str] = Field(default_factory=list)  # their file names (xpsb_obj_003)
    meshes: int = 0  # objects with an own 3D model


class InstallResult(BaseModel):
    target_dir: str
    backup_dir: str | None = None


class GateItem(BaseModel):
    kind: Literal["action", "cost", "write", "warning", "info"]
    key: str
    params: Params = Field(default_factory=dict)


class PendingGate(BaseModel):
    gate: GateId
    items: list[GateItem]
    blocked: bool = False  # e.g. open questions or missing settings
    hash: str


class Approval(BaseModel):
    gate: GateId
    hash: str
    at: datetime = Field(default_factory=utcnow)


class JobError(BaseModel):
    key: str
    detail: str = ""


class Job(BaseModel):
    id: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    state: JobState = JobState.DRAFT
    aoi: GeoJsonPolygon
    aoi_area_km2: float
    aoi_center: tuple[float, float]  # lon, lat
    brief: Brief | None = None
    analysis: AnalysisResult | None = None
    answers: dict[str, str] = Field(default_factory=dict)
    plan: BuildPlan | None = None
    build: BuildResult | None = None
    install: InstallResult | None = None
    approvals: list[Approval] = Field(default_factory=list)
    error: JobError | None = None
    # Every AI call of this job, as billed (the cost overview when installing).
    costs: list[CallCost] = Field(default_factory=list)


class JobView(Job):
    """What the API returns: the job plus the approval currently waiting for the user."""

    pending_gate: PendingGate | None = None
    # Objects the AI architect would build individually after the list is confirmed
    # (shown in step 3, so the user can add photos before that).
    design_targets: list[str] = Field(default_factory=list)
