"""The work behind each approval.

Analysis (M2): OSM objects and surfaces of the area, an open aerial image and,
if the user agreed, Claude's assessment of every item. The build still produces
the test scenery (real 3D generators arrive in M3); the install step copies it
into X-Plane with a backup of any existing package.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image
from shapely.geometry import Polygon

from xpsb import APP_NAME, __version__
from xpsb.ai import analyze as ai
from xpsb.ai.usage import UsageLedger
from xpsb.imagery import wms
from xpsb.imagery.sources import resolve_source
from xpsb.jobs.events import EventLog
from xpsb.jobs.models import (
    AnalysisResult,
    BuildPlan,
    BuildResult,
    InstallResult,
    Job,
    JobState,
    Note,
    PlanItem,
)
from xpsb.osm import classify, overpass
from xpsb.settings import Settings
from xpsb.xplane import dsftool
from xpsb.xplane.dsf_text import Tile
from xpsb.xplane.testscene import build_test_scenery, files_for

TEST_CUBE_HEIGHT_M = 20.0


@dataclass
class AnalysisDeps:
    job_dir: Path
    cache_dir: Path
    settings: Settings
    api_key: str | None
    transport_factory: Callable[[str], ai.AiTransport]
    ledger: UsageLedger


def ai_budget(job: Job, settings: Settings, ledger: UsageLedger) -> float:
    assert job.brief is not None
    left_this_month = max(0.0, settings.ai.monthly_limit_usd - ledger.month_spent())
    return min(job.brief.ai_cost_limit_usd, left_this_month)


def _progress(log: EventLog, stage: str, value: float, key: str, **params) -> None:
    log.publish_threadsafe(type="progress", stage=stage, progress=value, key=key, params=params)


def _osm_stage(job: Job, deps: AnalysisDeps, log: EventLog) -> list:
    assert job.brief is not None
    _progress(log, "osm", 0.0, "stage.osm")
    data = overpass.fetch(overpass.build_query(job.aoi.ring), deps.cache_dir / "overpass")
    items = classify.classify(
        data.get("elements", []),
        Polygon(job.aoi.ring),
        {c.value for c in job.brief.object_categories},
        {s.value for s in job.brief.surface_types},
    )
    _progress(log, "osm", 1.0, "stage.osm_done", count=len(items))
    return items


def _imagery_stage(job: Job, deps: AnalysisDeps, log: EventLog, notes: list[Note]):
    lon, lat = job.aoi_center
    source, reason = resolve_source(deps.settings.imagery, lon, lat)
    if source is None:
        notes.append(Note(key=reason or "imagery.off"))
        return None
    _progress(log, "imagery", 0.0, "stage.imagery", source=source.name)
    ring = job.aoi.ring
    area = wms.Bounds(
        min(p[0] for p in ring),
        min(p[1] for p in ring),
        max(p[0] for p in ring),
        max(p[1] for p in ring),
    )
    try:
        info = wms.fetch_area_image(
            source,
            area,
            deps.job_dir / "imagery",
            progress=lambda p: _progress(log, "imagery", p, "stage.imagery", source=source.name),
        )
    except (wms.WmsError, OSError, ValueError) as exc:
        notes.append(
            Note(key="imagery.failed", params={"source": source.name, "detail": str(exc)[:300]})
        )
        return None
    return info


def analyze_sync(job: Job, deps: AnalysisDeps, log: EventLog) -> AnalysisResult:
    """Runs in a worker thread."""
    assert job.brief is not None
    notes: list[Note] = []
    items = _osm_stage(job, deps, log)
    if not items:
        notes.append(Note(key="analysis.nothing_found"))
    info = _imagery_stage(job, deps, log, notes)
    result = AnalysisResult(items=items, imagery=info, notes=notes)

    if not job.brief.use_ai:
        notes.append(Note(key="analysis.ai_off"))
    elif info is None:
        notes.append(Note(key="analysis.ai_needs_imagery"))
    elif not deps.api_key:
        notes.append(Note(key="analysis.ai_no_key"))
    elif items:
        budget = ai_budget(job, deps.settings, deps.ledger)
        _progress(log, "ai", 0.0, "stage.ai", count=len(items), model=job.brief.ai_model)
        with Image.open(deps.job_dir / "imagery" / info.file) as img:
            image = img.convert("RGB")
        questions: list = []
        run = ai.AiRun(
            model=job.brief.ai_model,
            language=deps.settings.language,
            notes=job.brief.notes,
            budget_usd=budget,
            transport=deps.transport_factory(deps.api_key),
            on_cost=deps.ledger.add,
            progress=lambda p: _progress(
                log, "ai", p, "stage.ai", count=len(items), model=job.brief.ai_model
            ),
            should_stop=lambda: job.state is JobState.CANCELLED,
            questions=questions,
        )
        result.ai = ai.run(items, image, info, run)
        result.questions = questions
        if result.ai.limit_reached:
            notes.append(
                Note(key="analysis.ai_limit_reached", params={"limit_usd": round(budget, 2)})
            )
        if result.ai.error_key:
            notes.append(Note(key=result.ai.error_key))
        fallback = [m for m in result.ai.models_used if m != job.brief.ai_model]
        if fallback:
            notes.append(
                Note(key="analysis.ai_fallback_model", params={"model": ", ".join(fallback)})
            )
        _progress(
            log,
            "ai",
            1.0,
            "stage.ai_done",
            analyzed=result.ai.analyzed,
            cost_usd=round(result.ai.cost_usd, 4),
        )
    result.notes = notes  # pydantic copied the list on construction
    return result


async def run_analysis(job: Job, deps: AnalysisDeps, log: EventLog) -> AnalysisResult:
    return await asyncio.to_thread(analyze_sync, job, deps, log)


def included_counts(job: Job) -> dict[str, int]:
    counts: dict[str, int] = {}
    if job.analysis:
        for item in job.analysis.items:
            if item.included:
                counts[f"{item.kind}:{item.category}"] = (
                    counts.get(f"{item.kind}:{item.category}", 0) + 1
                )
    return counts


def _label_key(count_key: str) -> str:
    kind, category = count_key.split(":")
    return f"category.{category}" if kind == "object" else f"surface.{category}"


def make_plan(job: Job, job_dir: Path) -> BuildPlan:
    """Plan from the confirmed list. Also stores that list for the 3D build (M3)."""
    assert job.brief is not None and job.analysis is not None
    lon, lat = job.aoi_center
    tile = Tile.containing(lon, lat)
    scene = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": it.id,
                "geometry": it.geometry,
                "properties": {
                    "kind": it.kind,
                    "category": it.category,
                    "name": it.name,
                    "source": it.source,
                    "osm": it.osm_ref,
                    **it.attrs.model_dump(exclude_none=True),
                },
            }
            for it in job.analysis.items
            if it.included
        ],
    }
    (job_dir / "scene.geojson").write_text(
        json.dumps(scene, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    items = [
        PlanItem(kind="confirmed", label_key=_label_key(key), count=count)
        for key, count in sorted(included_counts(job).items())
    ]
    items += [
        PlanItem(
            kind="test_object",
            label_key="plan.test_object",
            count=1,
            params={"height_m": TEST_CUBE_HEIGHT_M},
        ),
        PlanItem(kind="test_surface", label_key="plan.test_surface", count=2),
    ]
    return BuildPlan(
        package_name=job.brief.package_name,
        tile=tile.dsf_relative_path,
        center=(lon, lat),
        test_scenery=True,
        cube_height_m=TEST_CUBE_HEIGHT_M,
        items=items,
        files=sorted(
            [
                tile.dsf_relative_path,
                "objects/xpsb_test_cube.obj",
                "objects/xpsb_test_cube.png",
                "polygons/xpsb_test_orientation.pol",
                "polygons/xpsb_test_orientation.png",
                "polygons/xpsb_test_checker.pol",
                "polygons/xpsb_test_checker.png",
                "README.txt",
            ]
        ),
    )


def _readme(plan: BuildPlan, job: Job) -> str:
    text = (
        f"{plan.package_name}\n"
        f"Created with {APP_NAME} {__version__} on {datetime.now():%Y-%m-%d}.\n\n"
        "Map data (c) OpenStreetMap contributors, available under the Open Database "
        "License (ODbL): https://www.openstreetmap.org/copyright\n"
    )
    info = job.analysis.imagery if job.analysis else None
    if info is not None:
        text += f"Aerial imagery: {info.attribution} ({info.license})\n"
    return text


def build_package(job: Job, job_dir: Path, exe: Path, log: EventLog) -> BuildResult:
    """Runs in a worker thread."""
    plan = job.plan
    assert plan is not None
    build_root = job_dir / "build"
    package = build_root / plan.package_name
    if package.exists():
        shutil.rmtree(package)
    package.mkdir(parents=True)

    log.publish_threadsafe(type="progress", stage="write", progress=0.0, key="stage.write")
    lon, lat = plan.center
    text_file = build_test_scenery(package, lon, lat, plan.cube_height_m or TEST_CUBE_HEIGHT_M)
    (package / "README.txt").write_text(_readme(plan, job), encoding="utf-8")

    log.publish_threadsafe(type="progress", stage="compile", progress=0.0, key="stage.compile")
    version = dsftool.version(exe)
    out = dsftool.text2dsf(exe, text_file, package / plan.tile)
    log.publish_threadsafe(type="log", text=out[-1000:])

    log.publish_threadsafe(type="progress", stage="verify", progress=0.0, key="stage.verify")
    roundtrip = build_root / "roundtrip.txt"
    dsftool.dsf2text(exe, package / plan.tile, roundtrip)
    _verify_roundtrip(text_file.read_text(encoding="ascii"), roundtrip.read_text(encoding="ascii"))
    log.publish_threadsafe(type="progress", stage="verify", progress=1.0, key="stage.verified")

    return BuildResult(package_dir=str(package), files=files_for(package), dsftool_version=version)


def _count(pattern: str, text: str) -> int:
    return len(re.findall(pattern, text, flags=re.MULTILINE))


def _verify_roundtrip(original: str, roundtrip: str) -> None:
    for pattern in (
        r"^OBJECT_DEF ",
        r"^POLYGON_DEF ",
        r"^OBJECT ",
        r"^BEGIN_POLYGON ",
        r"^POLYGON_POINT ",
        r"^PROPERTY sim/overlay 1$",
    ):
        if _count(pattern, original) != _count(pattern, roundtrip):
            raise RuntimeError(f"DSF roundtrip mismatch for {pattern!r}")


def install_package(job: Job, target: Path, backups_root: Path, log: EventLog) -> InstallResult:
    """Runs in a worker thread. Copies the built package into Custom Scenery."""
    assert job.build is not None
    source = Path(job.build.package_dir)
    custom_scenery = target.parent
    if (
        target.resolve().parent != custom_scenery.resolve()
        or custom_scenery.name != "Custom Scenery"
    ):
        raise RuntimeError("install target must be a direct child of 'Custom Scenery'")

    backup: Path | None = None
    if target.exists():
        # Backups live outside Custom Scenery so X-Plane does not load them as packs.
        backup = backups_root / f"{target.name}-{datetime.now():%Y%m%d-%H%M%S}"
        log.publish_threadsafe(
            type="progress",
            stage="backup",
            progress=0.0,
            key="stage.backup",
            params={"path": str(backup)},
        )
        shutil.move(str(target), str(backup))

    log.publish_threadsafe(
        type="progress", stage="copy", progress=0.0, key="stage.copy", params={"path": str(target)}
    )
    shutil.copytree(source, target)
    return InstallResult(target_dir=str(target), backup_dir=str(backup) if backup else None)
