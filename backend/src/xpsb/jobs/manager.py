"""Job state machine. Nothing with side effects happens without an approval."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import Callable
from pathlib import Path

from xpsb.geo import MAX_AOI_AREA_KM2, GeoJsonPolygon, ring_area_m2, ring_centroid
from xpsb.jobs import pipeline
from xpsb.jobs.events import EventLog
from xpsb.jobs.gates import GateContext, compute_gate, install_target
from xpsb.jobs.models import (
    RUNNING_STATES,
    Approval,
    Brief,
    GateId,
    Job,
    JobError,
    JobState,
    JobView,
    utcnow,
)
from xpsb.paths import AppPaths
from xpsb.secrets import ANTHROPIC_ACCOUNT, SecretStore
from xpsb.settings import SettingsStore
from xpsb.xplane import dsftool
from xpsb.xplane_detect import is_xplane_running

log = logging.getLogger(__name__)


class JobConflict(Exception):
    """The request does not fit the job's current state (HTTP 409)."""

    def __init__(self, key: str, **params):
        super().__init__(key)
        self.key = key
        self.params = params


class JobNotFound(Exception):
    pass


# On failure a job falls back to the last stable state so the user can fix the
# cause (e.g. set the DSFTool path) and approve again.
ROLLBACK = {
    JobState.ANALYZING: JobState.CLARIFIED,
    JobState.BUILDING: JobState.PLANNED,
    JobState.INSTALLING: JobState.BUILT,
}


class JobManager:
    def __init__(
        self,
        paths: AppPaths,
        settings: SettingsStore,
        secrets: SecretStore,
        resources_dir: Path | None,
        xplane_running: Callable[[], bool | None] = is_xplane_running,
        analysis_step_delay: float = 0.4,
    ):
        self._paths = paths
        self._settings = settings
        self._secrets = secrets
        self._resources_dir = resources_dir
        self._xplane_running = xplane_running
        self._step_delay = analysis_step_delay
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
            dsftool=dsftool.locate(settings.dsftool_path, self._resources_dir),
            xplane_running=self._xplane_running() if job.state is JobState.BUILT else None,
            job_dir=self.job_dir(job.id),
        )

    def view(self, job_id: str) -> JobView:
        job = self.get(job_id)
        gate = compute_gate(job, self._gate_context(job))
        return JobView(**job.model_dump(), pending_gate=gate)

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
        job.brief = brief
        # A changed brief invalidates everything derived from the previous one.
        job.analysis, job.answers, job.plan, job.build, job.install = None, {}, None, None, None
        job.error = None
        self._set_state(job, JobState.CLARIFIED)
        return job

    def set_answers(self, job_id: str, answers: dict[str, str]) -> Job:
        job = self.get(job_id)
        if job.state is not JobState.ANALYZED or job.analysis is None:
            raise JobConflict("error.wrong_state", state=job.state.value)
        questions = {q.id: q for q in job.analysis.questions}
        for qid, value in answers.items():
            q = questions.get(qid)
            if q is None or value not in {o.value for o in q.options}:
                raise JobConflict("error.invalid_answer", question=qid)
        job.answers = {**job.answers, **answers}
        self._save(job)
        return job

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
            job.plan = pipeline.make_plan(job)
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
        job.analysis = await pipeline.run_analysis(job, self.events(job.id), self._step_delay)
        self._set_state(job, JobState.ANALYZED)

    async def _do_build(self, job: Job, exe: Path) -> None:
        job.build = await asyncio.to_thread(
            pipeline.build_package, job, self.job_dir(job.id), exe, self.events(job.id)
        )
        self._set_state(job, JobState.BUILT)

    async def _do_install(self, job: Job, target: Path) -> None:
        job.install = await asyncio.to_thread(
            pipeline.install_package, job, target, self._paths.backups, self.events(job.id)
        )
        self._set_state(job, JobState.INSTALLED)
