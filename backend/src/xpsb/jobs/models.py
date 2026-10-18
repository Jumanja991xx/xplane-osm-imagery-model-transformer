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
    PLANNED = "planned"
    BUILDING = "building"
    BUILT = "built"
    INSTALLING = "installing"
    INSTALLED = "installed"
    FAILED = "failed"
    CANCELLED = "cancelled"


RUNNING_STATES = {JobState.ANALYZING, JobState.BUILDING, JobState.INSTALLING}

GateId = Literal["A", "B", "C", "D"]


class ExclusionType(StrEnum):
    OBJECTS = "objects"
    FACADES = "facades"
    FORESTS = "forests"
    POLYGONS = "polygons"
    ROADS = "roads"


class DetailLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


PACKAGE_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9 _\-]{0,59}$"


class Brief(BaseModel):
    """Step 2: what exactly should be built (answers of the clarification dialog)."""

    object_categories: list[ObjectCategory] = Field(default_factory=list)
    surface_types: list[SurfaceType] = Field(default_factory=list)
    surface_mode: SurfaceMode = SurfaceMode.MATERIAL
    exclusions: list[ExclusionType] = Field(default_factory=list)
    use_ai: bool = True
    ai_model: AiModel = "claude-opus-5-5"
    ai_cost_limit_usd: float = Field(default=5.0, ge=0, le=1000)
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


class AiRunInfo(BaseModel):
    model: str
    models_used: list[str] = Field(default_factory=list)  # differs on server-side fallback
    cost_usd: float = 0.0
    analyzed: int = 0
    skipped: int = 0
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


class BuildPlan(BaseModel):
    package_name: str
    tile: str
    items: list[PlanItem]
    files: list[str]
    center: tuple[float, float]  # lon, lat
    test_scenery: bool = False
    cube_height_m: float | None = None


class BuildResult(BaseModel):
    package_dir: str
    files: list[str]
    dsftool_version: str


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


class JobView(Job):
    """What the API returns: the job plus the approval currently waiting for the user."""

    pending_gate: PendingGate | None = None
