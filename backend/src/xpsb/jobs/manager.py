"""Job state machine. Nothing with side effects happens without an approval."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path

from PIL import Image
from pydantic import BaseModel
from shapely.geometry import Polygon

from xpsb import blender as blender_mod
from xpsb import photos, research
from xpsb.ai import analyze as ai
from xpsb.ai import detect
from xpsb.ai.usage import UsageLedger
from xpsb.build import phototex
from xpsb.catalog import category_label
from xpsb.geo import MAX_AOI_AREA_KM2, GeoJsonPolygon, ring_area_m2, ring_centroid
from xpsb.imagery.sources import ESRI_ACCOUNT
from xpsb.jobs import pipeline
from xpsb.jobs.events import EventLog
from xpsb.jobs.gates import GateContext, compute_gate, install_target
from xpsb.jobs.models import (
    RUNNING_STATES,
    AiRunInfo,
    Approval,
    Brief,
    GateId,
    Job,
    JobError,
    JobState,
    JobView,
    PlanObject,
    utcnow,
)
from xpsb.paths import AppPaths
from xpsb.scene import ItemAttrs, ItemPatch, NewItem, PhotoTexture, SceneItem, validate_item
from xpsb.secrets import ANTHROPIC_ACCOUNT, SecretStore
from xpsb.settings import SettingsStore
from xpsb.xplane import dsftool, library
from xpsb.xplane.library import Catalog
from xpsb.xplane_detect import is_xplane_running

log = logging.getLogger(__name__)


def _take_textures(item: SceneItem, made: list[PhotoTexture]) -> None:
    """The AI architect's photo textures replace its earlier ones; the user's own
    (marked in the photo panel) stay unless the AI made one with the same name."""
    names = {t.name for t in made}
    item.textures = [t for t in item.textures if t.source == "user" and t.name not in names]
    item.textures += made


class JobConflict(Exception):
    """The request does not fit the job's current state (HTTP 409)."""

    def __init__(self, key: str, **params):
        super().__init__(key)
        self.key = key
        self.params = params


class JobNotFound(Exception):
    pass


# Brief fields the analysis depends on; changing others keeps the analysis.
ANALYSIS_FIELDS = (
    "object_categories",
    "surface_types",
    "use_ai",
    "ai_model",
    "ai_detect",
    "research",
    "notes",
)


class SimilarEstimate(BaseModel):
    """What "find similar" would do; shown to the user before anything is sent."""

    model: str
    tiles: int
    estimate_usd: float
    budget_usd: float  # what is left of the job and monthly limits


class DesignEstimate(BaseModel):
    """What a request to the AI architect may cost; shown before anything is sent."""

    model: str
    estimate_usd: float  # typical: write + self-check
    max_usd: float  # with all fix attempts
    budget_usd: float  # what is left of the job and monthly limits


AUTO_DESIGN_REQUEST = (
    "Build this object so that it looks like the real one. Use everything you are given: "
    "the research facts, the photos, the aerial image and the OSM data. It must be "
    "recognisable from the air and from the ground: right shape and proportions, height, "
    "roof form, facade materials and colours, and the characteristic details."
)
DESIGN_PARALLEL = 3
MAX_AUTO_DESIGN = 40
IMPORTANT_DESIGN = 12


# On failure a job falls back to the last stable state so the user can fix the
# cause (e.g. set the DSFTool path) and approve again.
ROLLBACK = {
    JobState.ANALYZING: JobState.CLARIFIED,
    JobState.PLANNING: JobState.ANALYZED,
    JobState.BUILDING: JobState.PLANNED,
    JobState.INSTALLING: JobState.BUILT,
}


PLACEMENT_KEYS = {"shift_x_m", "shift_y_m", "turn_deg"}


class JobManager:
    def __init__(
        self,
        paths: AppPaths,
        settings: SettingsStore,
        secrets: SecretStore,
        resources_dir: Path | None,
        xplane_running: Callable[[], bool | None] = is_xplane_running,
        ai_transport_factory: Callable[[str], ai.AiTransport] = ai.AnthropicTransport,
    ):
        self._paths = paths
        self._settings = settings
        self._secrets = secrets
        self._resources_dir = resources_dir
        self._xplane_running = xplane_running
        self._ai_transport_factory = ai_transport_factory
        self._busy: set[str] = set()
        # Photo search hits per job (urls stay here; the client only knows ids).
        self._candidates: dict[str, dict[str, tuple[str, photos.PhotoCandidate]]] = {}
        self._thumbs: dict[str, dict[str, bytes]] = {}
        self._library: tuple[str | None, Catalog] | None = None
        self._blender: tuple[str, blender_mod.BlenderInfo | None] | None = None
        self._skip_blender: set[str] = set()  # jobs whose build goes on without Blender
        self._side_tasks: set[asyncio.Task] = set()
        self.ledger = UsageLedger(paths.root / "ai_usage.json")
        self._jobs: dict[str, Job] = {}
        self._logs: dict[str, EventLog] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called once at startup so worker threads can publish events safely."""
        self._loop = loop
        for event_log in self._logs.values():
            event_log.bind_loop(loop)

    # ---- persistence -------------------------------------------------------

    def job_dir(self, job_id: str) -> Path:
        return self._paths.jobs / job_id

    def _save(self, job: Job) -> None:
        job.updated_at = utcnow()
        d = self.job_dir(job.id)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "job.json.tmp"
        tmp.write_text(job.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, d / "job.json")

    def _set_state(self, job: Job, state: JobState) -> None:
        job.state = state
        self._save(job)
        self.events(job.id).publish(type="state", state=state.value)

    def load_existing(self) -> None:
        for file in sorted(self._paths.jobs.glob("*/job.json")):
            try:
                job = Job.model_validate_json(file.read_text(encoding="utf-8"))
            except ValueError:
                log.warning("skipping unreadable job file %s", file)
                continue
            if job.state in RUNNING_STATES:  # app was closed mid-step
                job.error = JobError(key="error.interrupted")
                job.state = ROLLBACK[job.state]
                self._save(job)
            self._jobs[job.id] = job

    # ---- queries -----------------------------------------------------------

    def get(self, job_id: str) -> Job:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise JobNotFound(job_id) from None

    def list(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def events(self, job_id: str) -> EventLog:
        self.get(job_id)
        if job_id not in self._logs:
            event_log = EventLog(self.job_dir(job_id) / "events.ndjson")
            if self._loop is not None:
                event_log.bind_loop(self._loop)
            self._logs[job_id] = event_log
        return self._logs[job_id]

    def _gate_context(self, job: Job) -> GateContext:
        settings = self._settings.load()
        return GateContext(
            settings=settings,
            has_api_key=bool(self._secrets.get(ANTHROPIC_ACCOUNT)),
            ai_month_spent=self.ledger.month_spent(),
            dsftool=dsftool.locate(settings.dsftool_path, self._resources_dir),
            xplane_running=self._xplane_running() if job.state is JobState.BUILT else None,
            job_dir=self.job_dir(job.id),
            has_mapillary=bool(self._secrets.get(photos.MAPILLARY_ACCOUNT)),
            esri_key=self._secrets.get(ESRI_ACCOUNT),
            blender=self._blender_version(job),
            **self._design_choice(job),
        )

    def skip_blender(self, job_id: str) -> Job:
        """The user does not want to wait: Blender stops, the package is finished
        with the objects shaded so far."""
        job = self.get(job_id)
        if job.state is not JobState.BUILDING:
            raise JobConflict("error.wrong_state", state=job.state.value)
        self._skip_blender.add(job_id)
        return job

    def _blender_version(self, job: Job) -> str | None:
        """Only looked up where approval C needs it (starts a program once)."""
        if job.state is not JobState.PLANNED:
            return None
        info = self.blender()
        return info.version if info else None

    def view(self, job_id: str) -> JobView:
        job = self.get(job_id)
        ctx = self._gate_context(job)
        gate = compute_gate(job, ctx)
        targets = [d["id"] for d in ctx.design] if job.state is JobState.ANALYZED else []
        return JobView(**job.model_dump(), pending_gate=gate, design_targets=targets)

    # ---- commands without side effects outside the job folder --------------

    def create(self, aoi: GeoJsonPolygon) -> Job:
        area_km2 = ring_area_m2(aoi.ring) / 1e6
        if area_km2 > MAX_AOI_AREA_KM2:
            raise JobConflict(
                "error.aoi_too_large", max_km2=MAX_AOI_AREA_KM2, area_km2=round(area_km2, 2)
            )
        if area_km2 < 1e-4:
            raise JobConflict("error.aoi_too_small")
        job = Job(
            id=uuid.uuid4().hex[:12],
            aoi=aoi,
            aoi_area_km2=area_km2,
            aoi_center=ring_centroid(aoi.ring),
        )
        self._jobs[job.id] = job
        self._save(job)
        self.events(job.id).publish(type="state", state=job.state.value)
        return job

    def set_brief(self, job_id: str, brief: Brief) -> Job:
        job = self.get(job_id)
        self._require_not_running(job)
        keep = (
            job.analysis is not None
            and job.brief is not None
            and all(getattr(job.brief, f) == getattr(brief, f) for f in ANALYSIS_FIELDS)
        )
        job.brief = brief
        job.plan, job.build, job.install = None, None, None
        job.error = None
        if keep:
            # Only build options changed (name, detail, exclusions...): the analysis
            # and everything the user edited stay.
            self._set_state(job, JobState.ANALYZED)
            return job
        job.analysis, job.answers = None, {}
        self._set_state(job, JobState.CLARIFIED)
        return job

    def reopen(self, job_id: str, to: JobState) -> Job:
        """Step back without losing work: to the list (analyzed) or the brief."""
        job = self.get(job_id)
        self._require_not_running(job)
        if to is JobState.ANALYZED and job.analysis is not None:
            job.plan, job.build, job.install = None, None, None
        elif to is JobState.CLARIFIED and job.brief is not None:
            pass  # the brief form opens with the current values; analysis kept until changed
        else:
            raise JobConflict("error.wrong_state", state=job.state.value)
        job.error = None
        self._set_state(job, to)
        return job

    def rename_package(self, job_id: str, name: str) -> Job:
        """After the build, before installing: give the scenery folder another name
        (only the folder in the job's own build directory is renamed)."""
        job = self.get(job_id)
        self._require_not_running(job)
        if job.state is not JobState.BUILT or job.brief is None or job.plan is None:
            raise JobConflict("error.wrong_state", state=job.state.value)
        try:
            brief = Brief.model_validate({**job.brief.model_dump(), "package_name": name})
        except ValueError:
            raise JobConflict("error.invalid_package_name") from None
        new = brief.package_name
        old = job.plan.package_name
        if new == old:
            return job
        build_root = self.job_dir(job.id) / "build"
        src, dst = build_root / old, build_root / new
        if dst.exists() and dst.resolve() != src.resolve():
            shutil.rmtree(dst)  # a leftover of an earlier build of this job
        if src.exists():
            src.rename(dst)
            readme = dst / "README.txt"
            if readme.exists():
                lines = readme.read_text(encoding="utf-8").split("\n", 1)
                readme.write_text("\n".join([new, *lines[1:]]), encoding="utf-8")
        job.brief = brief
        job.plan.package_name = new
        if job.build is not None:
            job.build.package_dir = str(dst)
        self._save(job)
        self.events(job.id).publish(type="log", key="log.renamed", params={"name": new})
        return job

    def memory(self, job: Job) -> tuple[list[SceneItem], dict[str, SceneItem]]:
        """What the user confirmed in earlier jobs of this area (list approved at B):
        own/AI/remembered items, and edits to OSM items by their OSM reference."""
        from shapely.geometry import Polygon, shape

        aoi = Polygon(job.aoi.ring)
        items: list[SceneItem] = []
        edits: dict[str, SceneItem] = {}
        taken: list = []
        for other in self.list():  # newest first: the latest decision wins
            if other.id == job.id or other.analysis is None:
                continue
            if not any(a.gate == "B" for a in other.approvals):
                continue
            for it in other.analysis.items:
                if it.proposal:
                    continue
                geom = shape(it.geometry)
                if not aoi.intersects(geom) or not aoi.contains(geom.representative_point()):
                    continue
                if it.source == "osm":
                    if it.osm_ref and (it.edited or it.remembered) and it.osm_ref not in edits:
                        edits[it.osm_ref] = it.model_copy(deep=True)
                    continue
                if not it.included or any(
                    geom.intersects(g) and geom.intersection(g).area > 0.5 * geom.area
                    for g in taken
                ):
                    continue
                taken.append(geom)
                items.append(it.model_copy(deep=True))
        return items, edits

    def summaries(self) -> list[dict]:
        return [
            {
                "id": j.id,
                "created_at": j.created_at.isoformat(),
                "updated_at": j.updated_at.isoformat(),
                "state": j.state.value,
                "package_name": j.brief.package_name if j.brief else None,
                "area_km2": round(j.aoi_area_km2, 3),
                "objects": sum(
                    1
                    for it in (j.analysis.items if j.analysis else [])
                    if it.kind == "object" and it.included and not it.proposal
                ),
            }
            for j in self.list()
        ]

    def _analyzed(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.state is not JobState.ANALYZED or job.analysis is None:
            raise JobConflict("error.wrong_state", state=job.state.value)
        return job

    def _item(self, job: Job, item_id: str) -> SceneItem:
        assert job.analysis is not None
        for item in job.analysis.items:
            if item.id == item_id:
                return item
        raise JobConflict("error.item_not_found", item=item_id)

    def set_answers(self, job_id: str, answers: dict[str, str]) -> Job:
        job = self._analyzed(job_id)
        assert job.analysis is not None
        questions = {q.id: q for q in job.analysis.questions}
        for qid, value in answers.items():
            q = questions.get(qid)
            if q is None or value not in {o.value for o in q.options}:
                raise JobConflict("error.invalid_answer", question=qid)
        for qid, value in answers.items():
            q = questions[qid]
            if q.item_id and q.field:
                item = self._item(job, q.item_id)
                ai.apply_answer(item, q.field, value)
        job.answers = {**job.answers, **answers}
        self._save(job)
        return job

    def _drop_questions(self, job: Job, item_id: str) -> None:
        """The user decided about this item themselves: its open AI questions are moot."""
        assert job.analysis is not None
        job.analysis.questions = [
            q for q in job.analysis.questions if q.item_id != item_id or q.id in job.answers
        ]

    def update_item(self, job_id: str, item_id: str, patch: ItemPatch) -> Job:
        job = self._analyzed(job_id)
        item = self._item(job, item_id)
        updated = item.model_copy(deep=True)
        changes = patch.model_dump(exclude_unset=True)
        # A proposal can only be accepted (rejecting it = deleting it).
        if changes.pop("proposal", None) is False and item.proposal:
            updated.proposal = False
            changes.setdefault("included", True)
        if "attrs" in changes:
            updated.attrs = updated.attrs.model_copy(update=changes.pop("attrs"))
        for key, value in changes.items():
            setattr(updated, key, value)
        try:
            validated = validate_item(updated.model_dump())
        except ValueError as exc:
            raise JobConflict("error.invalid_item", detail=str(exc)[:200]) from None
        validated.edited = True
        assert job.analysis is not None
        job.analysis.items = [validated if it.id == item_id else it for it in job.analysis.items]
        self._drop_questions(job, item_id)
        self._save(job)
        return job

    def add_item(self, job_id: str, new: NewItem) -> Job:
        job = self._analyzed(job_id)
        assert job.analysis is not None
        item_id = f"user/{job.analysis.next_user_id}"
        try:
            item = validate_item(
                {**new.model_dump(), "id": item_id, "source": "user", "edited": True}
            )
        except ValueError as exc:
            raise JobConflict("error.invalid_item", detail=str(exc)[:200]) from None
        job.analysis.next_user_id += 1
        job.analysis.items.append(item)
        if self._can_rate(job):
            # Height, roof and colours for what the user drew, within the job's limit.
            item.rating = True
            self._start_side_task(self._rate_user_item(job.id, item_id))
        self._save(job)
        return job

    def _can_rate(self, job: Job) -> bool:
        return bool(
            job.brief
            and job.brief.use_ai
            and job.analysis
            and job.analysis.imagery
            and self.imagery_file(job.id)
            and self._secrets.get(ANTHROPIC_ACCOUNT)
            and self._similar_budget(job) > 0
        )

    def _start_side_task(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._side_tasks.add(task)
        task.add_done_callback(self._side_tasks.discard)

    async def _rate_user_item(self, job_id: str, item_id: str) -> None:
        job = self.get(job_id)
        assert job.brief is not None and job.analysis is not None
        try:
            item = self._item(job, item_id).model_copy(deep=True)
            with Image.open(self.imagery_file(job_id)) as img:
                image = img.convert("RGB")
            questions: list = []
            cfg = ai.AiRun(
                model=job.brief.ai_model,
                language=self._settings.load().language,
                notes=job.brief.notes,
                budget_usd=self._similar_budget(job),
                transport=self._ai_transport_factory(self._secrets.get(ANTHROPIC_ACCOUNT)),
                on_cost=pipeline.cost_book(job, self.ledger, "rating", item.name),
                questions=questions,
                context=detect.area_context(job.analysis.items),
            )
            info = await asyncio.to_thread(ai.run, [item], image, job.analysis.imagery, cfg)
        except Exception:  # noqa: BLE001 - rating is optional; the item stays as drawn
            log.exception("rating drawn item failed")
            info, questions, item = None, [], None
        if job.analysis is None:
            return
        for target in job.analysis.items:
            if target.id == item_id:
                target.rating = False
                if item is not None and item.ai is not None:
                    target.attrs = target.attrs.model_copy(
                        update={
                            k: v
                            for k, v in item.attrs.model_dump().items()
                            if getattr(target.attrs, k) is None and v is not None
                        }
                    )
                    target.ai = item.ai
        if info is not None:
            run = job.analysis.ai or AiRunInfo(model=job.brief.ai_model)
            run.cost_usd = round(run.cost_usd + info.cost_usd, 6)
            run.analyzed += info.analyzed
            job.analysis.ai = run
            job.analysis.questions += questions
        self._save(job)
        self.events(job_id).publish(type="state", state=job.state.value)

    def delete_item(self, job_id: str, item_id: str) -> Job:
        job = self._analyzed(job_id)
        self._item(job, item_id)
        assert job.analysis is not None
        job.analysis.items = [it for it in job.analysis.items if it.id != item_id]
        job.analysis.questions = [q for q in job.analysis.questions if q.item_id != item_id]
        job.answers = {k: v for k, v in job.answers.items() if not k.startswith(f"ai:{item_id}:")}
        self._save(job)
        return job

    def imagery_file(self, job_id: str) -> Path | None:
        job = self.get(job_id)
        if job.analysis is None or job.analysis.imagery is None:
            return None
        path = self.job_dir(job_id) / "imagery" / job.analysis.imagery.file
        return path if path.exists() else None

    # ---- "find similar" (AI search with an example chosen by the user) ----------

    def _similar_ready(self, job_id: str, item_id: str) -> tuple[Job, SceneItem, Path]:
        job = self._analyzed(job_id)
        item = self._item(job, item_id)
        assert job.brief is not None and job.analysis is not None
        if not job.brief.use_ai:
            raise JobConflict("error.ai_off")
        if not self._secrets.get(ANTHROPIC_ACCOUNT):
            raise JobConflict("error.ai_no_key")
        image = self.imagery_file(job_id)
        if image is None or job.analysis.imagery is None:
            raise JobConflict("error.no_imagery")
        if item.kind != "object":
            raise JobConflict("error.similar_objects_only")
        return job, item, image

    def _similar_budget(self, job: Job) -> float:
        assert job.brief is not None and job.analysis is not None
        settings = self._settings.load()
        spent = job.analysis.ai.cost_usd if job.analysis.ai else 0.0
        return max(
            0.0,
            min(
                job.brief.ai_cost_limit_usd - spent,
                settings.ai.monthly_limit_usd - self.ledger.month_spent(),
            ),
        )

    def similar_estimate(self, job_id: str, item_id: str) -> SimilarEstimate:
        job, _, _ = self._similar_ready(job_id, item_id)
        assert job.brief is not None and job.analysis is not None
        tiles = len(detect.plan_tiles(job.analysis.imagery))
        return SimilarEstimate(
            model=job.brief.ai_model,
            tiles=tiles,
            estimate_usd=round(detect.similar_cost_estimate(tiles, job.brief.ai_model), 3),
            budget_usd=round(self._similar_budget(job), 4),
        )

    async def find_similar(self, job_id: str, item_id: str, max_usd: float) -> Job:
        """Runs only after the user confirmed the estimate (max_usd is what they saw)."""
        job, example, image_path = self._similar_ready(job_id, item_id)
        if job_id in self._busy:
            raise JobConflict("error.job_running")
        assert job.brief is not None and job.analysis is not None
        budget = min(max_usd, self._similar_budget(job))
        if budget <= 0:
            raise JobConflict("error.ai_budget_exhausted")
        self._busy.add(job_id)
        try:
            with Image.open(image_path) as img:
                image = img.convert("RGB")
            cfg = ai.AiRun(
                model=job.brief.ai_model,
                language=self._settings.load().language,
                notes=job.brief.notes,
                budget_usd=budget,
                transport=self._ai_transport_factory(self._secrets.get(ANTHROPIC_ACCOUNT)),
                on_cost=pipeline.cost_book(job, self.ledger, "similar"),
            )
            snapshot = [it.model_copy(deep=True) for it in job.analysis.items]
            found, info = await asyncio.to_thread(
                detect.find_similar,
                snapshot,
                example,
                Polygon(job.aoi.ring),
                image,
                job.analysis.imagery,
                cfg,
            )
        finally:
            self._busy.discard(job_id)

        analysis = job.analysis
        used = {it.id for it in analysis.items}
        n = 1
        for item in found:
            while f"ai/{n}" in used:
                n += 1
            item.id = f"ai/{n}"
            used.add(item.id)
        analysis.items.extend(found)
        run = analysis.ai or AiRunInfo(model=job.brief.ai_model)
        run.cost_usd = round(run.cost_usd + info.cost_usd, 6)
        run.tiles_searched += info.tiles_searched
        run.proposals += len(found)
        for m in info.models_used:
            if m not in run.models_used:
                run.models_used.append(m)
        analysis.ai = run
        self._save(job)
        if info.error_key:
            raise JobConflict(
                info.error_key.replace("analysis.", "error."), detail=info.error_detail or ""
            )
        return job

    # ---- 3D preview ------------------------------------------------------------

    def preview(self, job_id: str) -> dict:
        """Object placements and surfaces in metres around the area centre."""
        from shapely.geometry import shape

        from xpsb.geo import METERS_PER_DEG_LAT, meters_per_deg_lon

        job = self.get(job_id)
        if job.plan is None or job.analysis is None:
            raise JobConflict("error.wrong_state", state=job.state.value)
        lon0, lat0 = job.plan.center
        mx = meters_per_deg_lon(lat0)

        def local(lon: float, lat: float) -> list[float]:
            return [round((lon - lon0) * mx, 2), round((lat - lat0) * METERS_PER_DEG_LAT, 2)]

        surfaces = []
        for it in job.analysis.items:
            if it.kind != "surface" or not it.included or it.proposal:
                continue
            geom = shape(it.geometry)
            for poly in getattr(geom, "geoms", [geom]):
                surfaces.append(
                    {
                        "category": it.category,
                        "rings": [[local(x, y) for x, y in poly.exterior.coords]]
                        + [[local(x, y) for x, y in r.coords] for r in poly.interiors],
                    }
                )
        imagery = None
        info = job.analysis.imagery
        if info is not None:
            imagery = {"sw": local(info.west, info.south), "ne": local(info.east, info.north)}
        return {
            "objects": [{**o.model_dump(), "offset": local(*o.anchor)} for o in job.plan.objects],
            "surfaces": surfaces,
            "imagery": imagery,
            "surface_mode": job.plan.surface_mode,
        }

    # ---- fine-tuning single objects in the 3D preview (plan state) ------------

    def _planned(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.state is not JobState.PLANNED or job.plan is None or job.analysis is None:
            raise JobConflict("error.wrong_state", state=job.state.value)
        return job

    def regenerate_object(self, job: Job, item: SceneItem) -> PlanObject:
        """Builds one object of the plan again (preview only) and updates the plan."""
        from xpsb.build import assemble

        assert job.plan is not None and job.analysis is not None
        idx = next((i for i, o in enumerate(job.plan.objects) if o.item_id == item.id), None)
        if idx is None:
            raise JobConflict("error.item_not_found", item=item.id)
        res = assemble.generate_one(
            item,
            job.analysis.items,
            job.plan.detail,
            self.job_dir(job.id) / "preview",
            job.plan.objects[idx].stem,
            preview=True,
            library=self.library(),
            photo_dir=self.job_dir(job.id) / "photos",
        )
        job.plan.objects[idx] = PlanObject(**res.__dict__)
        items = job.plan.items
        at = next((i for i, it in enumerate(items) if it.kind in pipeline.OBJECT_ITEM_KINDS), 0)
        rest = [it for it in items if it.kind not in pipeline.OBJECT_ITEM_KINDS]
        job.plan.items = rest[:at] + pipeline.object_items(job.plan.objects) + rest[at:]
        files = [f for f in job.plan.files if not f.startswith("objects/")]
        files += pipeline.object_files(job.plan.objects)
        job.plan.files = sorted(files)
        return job.plan.objects[idx]

    def library(self) -> Catalog:
        """The X-Plane library catalog (empty without an X-Plane folder)."""
        path = self._settings.load().xplane_path
        if self._library is None or self._library[0] != path:
            catalog = library.load(path, self._paths.cache / "library_catalog.json")
            self._library = (path, catalog)
        return self._library[1]

    def adjust_object(self, job_id: str, item_id: str, attrs: dict) -> Job:
        """Quick controls from the 3D preview: changes parameters of one object and
        rebuilds only that one. Approval C then shows the new state."""
        job = self._planned(job_id)
        item = self._item(job, item_id)
        try:
            item.attrs = ItemAttrs.model_validate({**item.attrs.model_dump(), **attrs})
        except ValueError as exc:
            raise JobConflict("error.invalid_item", detail=str(exc)[:300]) from exc
        item.edited = True
        if set(attrs) <= PLACEMENT_KEYS:  # only moved/turned: the object stays as built
            from xpsb.build import assemble

            assert job.plan is not None
            obj = next((o for o in job.plan.objects if o.item_id == item.id), None)
            if obj is None:
                raise JobConflict("error.item_not_found", item=item.id)
            anchor, heading = assemble.placement(item)
            obj.anchor, obj.heading = anchor, heading
        else:
            self.regenerate_object(job, item)
        self._save(job)
        return job

    # ---- AI architect: own build script for one object -------------------------

    def _design_ready(self, job_id: str, item_id: str) -> tuple[Job, SceneItem]:
        job = self._planned(job_id)
        item = self._item(job, item_id)
        assert job.brief is not None
        if not job.brief.use_ai:
            raise JobConflict("error.ai_off")
        if not self._secrets.get(ANTHROPIC_ACCOUNT):
            raise JobConflict("error.ai_no_key")
        if item.kind != "object":
            raise JobConflict("error.similar_objects_only")
        return job, item

    def _current_script(self, item: SceneItem) -> str:
        from xpsb.build.scripts import DEFAULT_SCRIPTS

        return item.script or DEFAULT_SCRIPTS.get(str(item.category), "")

    def design_estimate(self, job_id: str, item_id: str) -> DesignEstimate:
        from xpsb.ai import architect

        job, item = self._design_ready(job_id, item_id)
        assert job.brief is not None
        typical, most = architect.cost_estimate(
            job.brief.ai_model,
            len(self._current_script(item)),
            len(item.photos),
            len(self.library().offer()),
        )
        return DesignEstimate(
            model=job.brief.ai_model,
            estimate_usd=round(typical, 3),
            max_usd=round(most, 3),
            budget_usd=round(self._similar_budget(job), 4),
        )

    async def redesign(
        self, job_id: str, item_id: str, request: str, max_usd: float
    ) -> tuple[Job, str, float]:
        """Runs only after the user confirmed the estimate (max_usd is what they saw).
        Returns the job, the architect's summary and what it cost."""
        job, item = self._design_ready(job_id, item_id)
        if job_id in self._busy:
            raise JobConflict("error.job_running")
        assert job.brief is not None and job.analysis is not None and job.plan is not None
        budget = min(max_usd, self._similar_budget(job))
        if budget <= 0:
            raise JobConflict("error.ai_budget_exhausted")
        log = self.events(job_id)

        def on_step(key: str, params: dict) -> None:
            log.publish_threadsafe(
                type="progress", stage="design", key=key, params={**params, "item": item.id}
            )

        self._busy.add(job_id)
        try:
            result = await asyncio.to_thread(
                self._design_call,
                job,
                item,
                request,
                budget,
                job.plan.detail,
                on_step,
                "correction",
            )
        finally:
            self._busy.discard(job_id)

        self._add_design_cost(job, result)
        if result.script is None:
            self._save(job)
            raise JobConflict(
                result.error_key or "error.architect_failed", detail=result.error_detail or ""
            )
        item.script = result.script
        _take_textures(item, result.textures)
        item.script_request = (
            f"{item.script_request} | {request}" if item.script_request else request
        )[-2000:]
        item.edited = True
        self.regenerate_object(job, item)
        self._save(job)
        return job, result.summary, result.info.cost_usd

    def _design_call(
        self,
        job: Job,
        item: SceneItem,
        request: str,
        budget: float,
        detail: str,
        on_step,
        stage: str = "design",
    ):
        """One run of the AI architect for one object (worker thread)."""
        from xpsb.ai import architect
        from xpsb.build import assemble

        assert job.brief is not None and job.analysis is not None
        aerial = None
        image_path = self.imagery_file(job.id)
        if image_path is not None and job.analysis.imagery is not None:
            with Image.open(image_path) as img:
                try:
                    aerial, _ = ai.render_crop(img.convert("RGB"), job.analysis.imagery, item)
                except ValueError:
                    aerial = None
        cfg = ai.AiRun(
            model=job.brief.ai_model,
            language=self._settings.load().language,
            notes=job.brief.notes,
            budget_usd=budget,
            transport=self._ai_transport_factory(self._secrets.get(ANTHROPIC_ACCOUNT)),
            on_cost=pipeline.cost_book(job, self.ledger, stage, item.name or item.id),
        )
        inputs = assemble._script_inputs(item, job.analysis.items, detail)
        max_tris = assemble.DETAIL.get(detail, assemble.DETAIL["medium"])[0]
        label = f"{item.name or ''} ({item.category})".strip()
        return architect.design(
            request=request,
            current_script=self._current_script(item),
            inputs=inputs,
            max_triangles=max_tris,
            aerial_png=aerial,
            cfg=cfg,
            object_label=label,
            on_step=on_step,
            photos=self._photos_for_ai(job, item),
            library=self.library(),
            research=item.research.facts if item.research else None,
            photo_ids={p.id: phototex.redistributable(p) for p in item.photos},
        )

    def _design_choice(self, job: Job) -> dict:
        """Which objects the AI architect builds at approval B, within the cost limit."""
        from xpsb.ai import architect

        b = job.brief
        if (
            job.state is not JobState.ANALYZED
            or job.analysis is None
            or b is None
            or not b.use_ai
            or b.ai_design == "off"
            or not self._secrets.get(ANTHROPIC_ACCOUNT)
        ):
            return {}
        items = job.analysis.items
        buildable = [
            it
            for it in items
            if it.kind == "object"
            and it.included
            and not it.proposal
            and not it.script  # the user's own design stays
            and str(it.category) != "barriers"
        ]
        if b.ai_design == "important":
            wanted = {it.id for it in research.important(items, limit=IMPORTANT_DESIGN)}
            candidates = [it for it in buildable if it.id in wanted]
            candidates.sort(key=research.score, reverse=True)
        else:
            candidates = sorted(buildable, key=research.score, reverse=True)[:MAX_AUTO_DESIGN]
        budget = self._similar_budget(job)
        language = self._settings.load().language
        library_lines = len(self.library().offer())
        chosen, total, cut = [], 0.0, 0
        for it in candidates:
            typical, most = architect.cost_estimate(
                b.ai_model, len(self._current_script(it)), len(it.photos), library_lines
            )
            if total + most > budget:
                cut += 1
                continue
            total += most
            name = it.name or category_label(str(it.category), language)
            chosen.append({"id": it.id, "name": name, "typical": typical, "max": most})
        return {"design": tuple(chosen), "design_cut": cut}

    def _design_many(self, job: Job, targets: tuple) -> None:
        """Worker thread: the AI architect builds the chosen objects, a few at a time."""
        from concurrent.futures import ThreadPoolExecutor

        assert job.brief is not None
        log = self.events(job.id)
        notes = job.brief.notes
        request = AUTO_DESIGN_REQUEST + (
            f"\nNotes from the user for the whole area: {notes}" if notes else ""
        )
        detail = job.brief.detail.value
        total = len(targets)
        done = designed = 0
        spent = 0.0

        def progress(key: str, value: float, **params) -> None:
            log.publish_threadsafe(
                type="progress", stage="design", progress=value, key=key, params=params
            )

        def status(t: dict, state: str, key: str, **params) -> None:
            # One line per object in the UI: what the AI architect is doing right now.
            log.publish_threadsafe(
                type="progress",
                stage=f"design:{t['id']}",
                progress=1.0 if state in ("done", "failed") else 0.0,
                key=key,
                params={"name": t["name"], "status": state, **params},
            )

        def one(t: dict):
            if job.state is not JobState.PLANNING:  # cancelled meanwhile
                return None
            item = self._item(job, t["id"])
            status(t, "running", "design.step.preparing", photos=len(item.photos))
            result = self._design_call(
                job,
                item,
                request,
                t["max"],
                detail,
                lambda k, p: status(t, "running", k, **p),
            )
            return item, result

        cost_max = round(sum(t["max"] for t in targets), 3)
        for t in targets:
            status(t, "waiting", "design.obj.waiting")
        progress("stage.design", 0.0, done=0, total=total, name="", cost_usd=0, cost_max=cost_max)
        with ThreadPoolExecutor(max_workers=DESIGN_PARALLEL) as pool:
            futures = [(pool.submit(one, t), t) for t in targets]
            for fut, t in futures:
                out = fut.result()
                done += 1
                if out is None:
                    status(t, "failed", "design.obj.cancelled")
                else:
                    item, result = out
                    self._add_design_cost(job, result)
                    spent += result.info.cost_usd
                    cost = round(result.info.cost_usd, 4)
                    if result.script is not None:
                        item.script = result.script
                        _take_textures(item, result.textures)
                        item.script_request = "auto"
                        designed += 1
                        status(
                            t,
                            "done",
                            "design.obj.done",
                            cost_usd=cost,
                            summary=(result.summary or "")[:240],
                        )
                    else:
                        status(
                            t,
                            "failed",
                            "design.obj.failed",
                            cost_usd=cost,
                            error=result.error_key or "error.architect_failed",
                        )
                progress(
                    "stage.design",
                    done / total,
                    done=done,
                    total=total,
                    name=t["name"],
                    cost_usd=round(spent, 3),
                    cost_max=cost_max,
                )
        progress("stage.design_done", 1.0, designed=designed, total=total, cost_usd=round(spent, 3))

    async def _do_plan(self, job: Job, targets: tuple) -> None:
        await asyncio.to_thread(self._design_many, job, targets)
        if job.state is not JobState.PLANNING:
            return
        self.events(job.id).publish(type="progress", stage="plan", progress=0.0, key="stage.plan")
        job.plan = await asyncio.to_thread(
            pipeline.make_plan, job, self.job_dir(job.id), self.library()
        )
        self._set_state(job, JobState.PLANNED)

    def _add_design_cost(self, job: Job, result) -> None:
        assert job.brief is not None and job.analysis is not None
        run = job.analysis.ai or AiRunInfo(model=job.brief.ai_model)
        run.cost_usd = round(run.cost_usd + result.info.cost_usd, 6)
        for m in result.info.models_used:
            if m not in run.models_used:
                run.models_used.append(m)
        job.analysis.ai = run

    def _photos_for_ai(self, job: Job, item: SceneItem) -> list[tuple[str, bytes]]:
        out = []
        for p in item.photos:
            path = self.photo_file(job.id, p.id)
            if path is None:
                continue
            source = "own photo" if p.source == "user" else photos.SOURCE_NAMES[p.source]
            usable = (
                "may be used as texture"
                if phototex.redistributable(p)
                else ("NOT usable as texture (licence)")
            )
            caption = f"Photo id={p.id} ({source}{', ' + p.title if p.title else ''}; {usable}):"
            out.append((caption, photos.for_ai(path)))
        return out

    def reset_design(self, job_id: str, item_id: str) -> Job:
        """Back to the default build script (free)."""
        job = self._planned(job_id)
        item = self._item(job, item_id)
        item.script, item.script_request = None, None
        self.regenerate_object(job, item)
        self._save(job)
        return job

    # ---- photos of real objects (input for the AI architect) ---------------------

    def _photo_job(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.state not in (JobState.ANALYZED, JobState.PLANNED) or job.analysis is None:
            raise JobConflict("error.wrong_state", state=job.state.value)
        return job

    def _add_photo(self, job: Job, item: SceneItem, raw: bytes, meta: dict) -> Job:
        if len(item.photos) >= photos.MAX_PHOTOS_PER_ITEM:
            raise JobConflict("error.photo_limit", max=photos.MAX_PHOTOS_PER_ITEM)
        try:
            photo = photos.store(self.job_dir(job.id) / "photos", raw, meta)
        except photos.PhotoError as exc:
            raise JobConflict("error.photo_invalid", detail=str(exc)) from None
        item.photos.append(photo)
        self._save(job)
        return job

    def add_photo(self, job_id: str, item_id: str, data_b64: str, rights_confirmed: bool) -> Job:
        job = self._photo_job(job_id)
        item = self._item(job, item_id)
        if not rights_confirmed:
            raise JobConflict("error.photo_rights")
        try:
            raw = photos.decode_upload(data_b64)
        except photos.PhotoError as exc:
            raise JobConflict("error.photo_invalid", detail=str(exc)) from None
        return self._add_photo(job, item, raw, {"source": "user"})

    def _photo_owner(self, job: Job, photo_id: str) -> tuple[SceneItem, photos.Photo]:
        assert job.analysis is not None
        for item in job.analysis.items:
            for p in item.photos:
                if p.id == photo_id:
                    return item, p
        raise JobConflict("error.photo_not_found")

    def _checked_texture(self, job: Job, item: SceneItem, tex: PhotoTexture) -> Path:
        photo = next((p for p in item.photos if p.id == tex.photo_id), None)
        path = self.photo_file(job.id, tex.photo_id)
        if photo is None or path is None:
            raise JobConflict("error.photo_not_found")
        if not phototex.redistributable(photo):
            raise JobConflict("error.texture_licence", license=photo.license or "?")
        return path

    def texture_preview(self, job_id: str, item_id: str, tex: PhotoTexture) -> bytes:
        """The straightened facade as it would go onto the model (JPEG)."""
        job = self._photo_job(job_id)
        item = self._item(job, item_id)
        path = self._checked_texture(job, item, tex)
        try:
            img = phototex.rectify(path, tex)
        except ValueError:
            raise JobConflict("error.texture_corners") from None
        img.thumbnail((1024, 1024))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()

    def set_textures(self, job_id: str, item_id: str, textures: list[PhotoTexture]) -> Job:
        """The item's photo textures as edited in the photo panel (the whole list)."""
        job = self._photo_job(job_id)
        item = self._item(job, item_id)
        names: set[str] = set()
        for tex in textures:
            path = self._checked_texture(job, item, tex)
            if tex.name in names:
                raise JobConflict("error.texture_duplicate", name=tex.name)
            names.add(tex.name)
            try:
                phototex.rectify(path, tex)
            except ValueError:
                raise JobConflict("error.texture_corners", name=tex.name) from None
        item.textures = textures
        item.edited = True
        planned = job.plan.objects if job.state is JobState.PLANNED and job.plan else []
        if any(o.item_id == item.id for o in planned):
            self.regenerate_object(job, item)
        self._save(job)
        return job

    def delete_photo(self, job_id: str, photo_id: str) -> Job:
        job = self._photo_job(job_id)
        item, photo = self._photo_owner(job, photo_id)
        item.photos.remove(photo)
        item.textures = [t for t in item.textures if t.photo_id != photo_id]
        (self.job_dir(job_id) / "photos" / f"{photo_id}.jpg").unlink(missing_ok=True)
        self._save(job)
        return job

    def photo_file(self, job_id: str, photo_id: str) -> Path | None:
        if not photo_id.isalnum():
            return None
        path = self.job_dir(job_id) / "photos" / f"{photo_id}.jpg"
        return path if path.is_file() else None

    def search_photos(self, job_id: str, item_id: str) -> photos.SearchResult:
        """Asks the open photo sources for pictures near the object (position only)."""
        from shapely.geometry import shape

        job = self._photo_job(job_id)
        item = self._item(job, item_id)
        p = shape(item.geometry).representative_point()
        result = photos.search(p.x, p.y, self._secrets.get(photos.MAPILLARY_ACCOUNT))
        found = self._candidates.setdefault(job_id, {})
        for c in result.candidates:
            found[c.id] = (item_id, c)
        return result

    def candidate_thumb(self, job_id: str, candidate_id: str) -> bytes:
        entry = self._candidates.get(job_id, {}).get(candidate_id)
        if entry is None:
            raise JobConflict("error.photo_not_found")
        cache = self._thumbs.setdefault(job_id, {})
        if candidate_id not in cache:
            c = entry[1]
            try:
                thumb = photos.http_get(c.thumb_url)
                if c.pano and c.heading is not None and c.bearing is not None:
                    thumb = photos.view_towards(thumb, c.heading, c.bearing)
                cache[candidate_id] = thumb
            except photos.PhotoError as exc:
                raise JobConflict("error.photo_download", detail=str(exc)) from None
        return cache[candidate_id]

    def import_photo(self, job_id: str, candidate_id: str) -> Job:
        job = self._photo_job(job_id)
        entry = self._candidates.get(job_id, {}).get(candidate_id)
        if entry is None:
            raise JobConflict("error.photo_not_found")
        item_id, c = entry
        item = self._item(job, item_id)
        try:
            raw = photos.download(c)
        except photos.PhotoError as exc:
            raise JobConflict("error.photo_download", detail=str(exc)) from None
        meta = c.model_dump(include={"source", "title", "author", "license", "page_url"})
        return self._add_photo(job, item, raw, meta)

    def preview_file(self, job_id: str, name: str) -> Path | None:
        job = self.get(job_id)
        if job.plan is None or not all(c.isalnum() or c in "_." for c in name):
            return None
        path = self.job_dir(job_id) / "preview" / name
        return path if path.is_file() and path.suffix in (".json", ".png") else None

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        task = self._tasks.pop(job_id, None)
        if task and not task.done():
            task.cancel()
        if job.state not in (JobState.INSTALLED, JobState.CANCELLED):
            self._set_state(job, JobState.CANCELLED)
        return job

    # ---- approvals ---------------------------------------------------------

    def approve(self, job_id: str, gate: GateId, seen_hash: str) -> JobView:
        job = self.get(job_id)
        ctx = self._gate_context(job)
        pending = compute_gate(job, ctx)
        if pending is None or pending.gate != gate:
            raise JobConflict("error.wrong_state", state=job.state.value)
        if pending.blocked:
            raise JobConflict("error.gate_blocked", gate=gate)
        if pending.hash != seen_hash:
            raise JobConflict("error.gate_stale", gate=gate)

        job.approvals.append(Approval(gate=gate, hash=seen_hash))
        job.error = None
        if gate == "A":
            self._set_state(job, JobState.ANALYZING)
            self._start(job, self._do_analysis(job))
        elif gate == "B":
            if ctx.design:
                self._set_state(job, JobState.PLANNING)
                self._start(job, self._do_plan(job, ctx.design))
            else:
                job.plan = pipeline.make_plan(job, self.job_dir(job.id), self.library())
                self._set_state(job, JobState.PLANNED)
        elif gate == "C":
            assert ctx.dsftool is not None
            self._set_state(job, JobState.BUILDING)
            self._start(job, self._do_build(job, ctx.dsftool))
        elif gate == "D":
            target = install_target(ctx.settings, job.plan.package_name)
            assert target is not None
            self._set_state(job, JobState.INSTALLING)
            self._start(job, self._do_install(job, target))
        return self.view(job_id)

    def _require_not_running(self, job: Job) -> None:
        if job.state in RUNNING_STATES:
            raise JobConflict("error.job_running")

    def _start(self, job: Job, coro) -> None:
        self._tasks[job.id] = asyncio.get_running_loop().create_task(self._guard(job, coro))

    async def _guard(self, job: Job, coro) -> None:
        running = job.state
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - every failure is reported to the user
            log.exception("job %s failed in %s", job.id, running.value)
            job.error = JobError(key=f"error.{running.value}_failed", detail=str(exc)[:2000])
            self.events(job.id).publish(type="error", key=job.error.key, text=job.error.detail)
            self._set_state(job, ROLLBACK[running])
        finally:
            self._tasks.pop(job.id, None)

    async def _do_analysis(self, job: Job) -> None:
        memory_items, memory_edits = self.memory(job)
        deps = pipeline.AnalysisDeps(
            job_dir=self.job_dir(job.id),
            cache_dir=self._paths.cache,
            settings=self._settings.load(),
            api_key=self._secrets.get(ANTHROPIC_ACCOUNT)
            if job.brief and job.brief.use_ai
            else None,
            transport_factory=self._ai_transport_factory,
            ledger=self.ledger,
            memory_items=memory_items,
            memory_edits=memory_edits,
            mapillary_token=self._secrets.get(photos.MAPILLARY_ACCOUNT),
            esri_key=self._secrets.get(ESRI_ACCOUNT),
        )
        analysis = await pipeline.run_analysis(job, deps, self.events(job.id))
        if job.state is JobState.ANALYZING:  # not cancelled meanwhile
            job.analysis = analysis
            self._set_state(job, JobState.ANALYZED)

    def blender(self) -> blender_mod.BlenderInfo | None:
        """The user's Blender if it is to be used (looked up once per path)."""
        s = self._settings.load()
        if not s.use_blender:
            return None
        key = s.blender_path or ""
        if self._blender is None or self._blender[0] != key:
            self._blender = (key, blender_mod.find(s.blender_path))
        return self._blender[1]

    async def _do_build(self, job: Job, exe: Path) -> None:
        info = await asyncio.to_thread(self.blender)
        job.build = await asyncio.to_thread(
            pipeline.build_package,
            job,
            self.job_dir(job.id),
            exe,
            self.events(job.id),
            self.library(),
            info.path if info else None,
            info.version if info else None,
            lambda: job.id in self._skip_blender,
            self._settings.load().blender_device == "cpu",
        )
        self._skip_blender.discard(job.id)
        self._set_state(job, JobState.BUILT)

    async def _do_install(self, job: Job, target: Path) -> None:
        job.install = await asyncio.to_thread(
            pipeline.install_package, job, target, self._paths.backups, self.events(job.id)
        )
        self._set_state(job, JobState.INSTALLED)
