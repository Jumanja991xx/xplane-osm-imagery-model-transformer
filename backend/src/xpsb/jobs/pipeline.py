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
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PIL import Image
from shapely.geometry import Polygon, shape

from xpsb import APP_NAME, __version__, research
from xpsb import photos as photos_mod
from xpsb.ai import analyze as ai
from xpsb.ai import detect
from xpsb.ai.usage import UsageLedger
from xpsb.build import assemble, phototex
from xpsb.geo import METERS_PER_DEG_LAT, meters_per_deg_lon
from xpsb.imagery import wms
from xpsb.imagery.sources import resolve_source
from xpsb.jobs.events import EventLog
from xpsb.jobs.models import (
    AnalysisResult,
    BuildPlan,
    BuildResult,
    ExclusionScope,
    ExclusionType,
    InstallResult,
    Job,
    JobState,
    Note,
    PlanItem,
    PlanObject,
)
from xpsb.osm import classify, overpass
from xpsb.photos import Photo
from xpsb.settings import Settings
from xpsb.xplane import dsftool
from xpsb.xplane.dsf_text import Tile
from xpsb.xplane.library import Catalog
from xpsb.xplane.testscene import files_for


@dataclass
class AnalysisDeps:
    job_dir: Path
    cache_dir: Path
    settings: Settings
    api_key: str | None
    transport_factory: Callable[[str], ai.AiTransport]
    ledger: UsageLedger
    memory_items: list = field(default_factory=list)
    memory_edits: dict = field(default_factory=dict)
    mapillary_token: str | None = None
    esri_key: str | None = None


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
    source, reason = resolve_source(deps.settings.imagery, lon, lat, deps.esri_key)
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
        key = "imagery.esri_failed" if source.id == "esri" else "imagery.failed"
        notes.append(Note(key=key, params={"source": source.name, "detail": str(exc)[:300]}))
        return None
    return info


def _apply_memory(items: list, deps: AnalysisDeps) -> int:
    """Brings back what the user decided in earlier jobs of this area."""
    count = 0
    for item in items:
        mem = deps.memory_edits.get(item.osm_ref or "")
        if mem is not None:
            item.category, item.attrs, item.included = mem.category, mem.attrs, mem.included
            item.name = mem.name or item.name
            item.ai, item.remembered = mem.ai, True
            count += 1
    known = [shape(it.geometry) for it in items if it.kind == "object"]
    for n, mem in enumerate(deps.memory_items, start=1):
        geom = shape(mem.geometry)
        if any(geom.intersects(g) and geom.intersection(g).area > 0.5 * geom.area for g in known):
            continue  # meanwhile mapped in OSM
        items.append(
            mem.model_copy(
                update={
                    "id": f"mem/{n}",
                    "source": "memory",
                    "remembered": True,
                    "included": True,
                    "edited": False,
                }
            )
        )
        count += 1
    return count


def analyze_sync(job: Job, deps: AnalysisDeps, log: EventLog) -> AnalysisResult:
    """Runs in a worker thread."""
    assert job.brief is not None
    notes: list[Note] = []
    items = _osm_stage(job, deps, log)
    remembered = _apply_memory(items, deps)
    if remembered:
        notes.append(Note(key="analysis.remembered", params={"count": remembered}))
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
    elif items or job.brief.ai_detect:
        budget = ai_budget(job, deps.settings, deps.ledger)
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
            should_stop=lambda: job.state is JobState.CANCELLED,
            questions=questions,
            context=detect.area_context(items),
        )
        proposals: list = []
        run_info = None
        if job.brief.ai_detect:
            tiles = len(detect.plan_tiles(info))
            look = {"pass": 1, "passes": detect.PASSES}

            def detect_progress(p: float) -> None:
                _progress(log, "detect", p, "stage.detect", tiles=tiles, **look)

            def detect_pass(n: int, total: int) -> None:
                look.update({"pass": n, "passes": total})
                detect_progress((n - 1) / total)

            run.progress = detect_progress
            proposals, run_info = detect.search(
                items,
                Polygon(job.aoi.ring),
                image,
                info,
                run,
                [c.value for c in job.brief.object_categories],
                [s.value for s in job.brief.surface_types],
                on_pass=detect_pass,
            )
            for n, item in enumerate(proposals, start=1):
                item.id = f"ai/{n}"
            if run_info.dropped.get("category"):
                notes.append(
                    Note(
                        key="analysis.dropped_category",
                        params={"count": run_info.dropped["category"]},
                    )
                )
            _progress(log, "detect", 1.0, "stage.detect_done", count=len(proposals))
        if items:
            run.progress = lambda p: _progress(
                log, "ai", p, "stage.ai", count=len(items), model=job.brief.ai_model
            )
            run.progress(0.0)
            to_rate = [it for it in items if not it.remembered]
            result.ai = ai.run(to_rate, image, info, run, result=run_info)
        else:
            result.ai = run_info
        result.items = items + proposals
        result.questions = questions
        if result.ai.limit_reached:
            notes.append(
                Note(key="analysis.ai_limit_reached", params={"limit_usd": round(budget, 2)})
            )
        if result.ai.error_key:
            notes.append(Note(key=result.ai.error_key, params={"detail": result.ai.error_detail}))
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
    if job.brief.research:
        _research_stage(job, result, deps, log, notes)
    result.notes = notes  # pydantic copied the list on construction
    return result


def _research_stage(
    job: Job, result: AnalysisResult, deps: AnalysisDeps, log: EventLog, notes: list[Note]
) -> None:
    """Facts and photos from free sources for the important objects (no AI, no costs)."""
    from concurrent.futures import ThreadPoolExecutor

    todo = research.important(result.items)
    if not todo:
        return
    _progress(log, "research", 0.0, "stage.research", count=len(todo))
    done = failed = photos_added = 0

    def one(item):
        return item, research.research_item(item, deps.settings.language, deps.mapillary_token)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(one, it) for it in todo]
        for fut in futures:
            if job.state is JobState.CANCELLED:
                break
            try:
                item, found = fut.result()
            except (photos_mod.PhotoError, ValueError, KeyError, TypeError, AttributeError):
                failed += 1
            else:
                item.research = found.research
                if found.height_m and item.attrs.height_m is None and found.height_m < 400:
                    item.attrs.height_m = round(found.height_m, 1)
                photos_added += research.attach_photos(item, found, deps.job_dir / "photos")
            done += 1
            _progress(log, "research", done / len(todo), "stage.research", count=len(todo))
    _progress(
        log,
        "research",
        1.0,
        "stage.research_done",
        count=done - failed,
        photos=photos_added,
    )
    if failed:
        notes.append(Note(key="analysis.research_failed", params={"count": failed}))


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


EXCLUSION_MARGIN_M = 3.0
Bounds = tuple[float, float, float, float]  # west, south, east, north


def _exclusion_rects(job: Job, built_ids: set[str]) -> list[Bounds]:
    """Where other scenery is removed: one rectangle around each new object (and
    surface, for surface exclusions), or the whole area if the user chose that."""
    assert job.brief is not None and job.analysis is not None
    if job.brief.exclusion_scope is ExclusionScope.AREA:
        ring = job.aoi.ring
        return [
            (
                min(p[0] for p in ring),
                min(p[1] for p in ring),
                max(p[0] for p in ring),
                max(p[1] for p in ring),
            )
        ]
    _, lat0 = job.aoi_center
    dx = EXCLUSION_MARGIN_M / meters_per_deg_lon(lat0)
    dy = EXCLUSION_MARGIN_M / METERS_PER_DEG_LAT
    rects = []
    for it in job.analysis.items:
        if it.id in built_ids:
            w, s, e, n = shape(it.geometry).bounds
            rects.append((w - dx, s - dy, e + dx, n + dy))
    return rects


def _surface_rects(job: Job) -> list[Bounds]:
    assert job.analysis is not None
    return [
        shape(it.geometry).bounds
        for it in job.analysis.items
        if it.kind == "surface" and it.included and not it.proposal
    ]


def _exclusions(job: Job) -> list[tuple[str, Bounds]]:
    assert job.brief is not None and job.plan is not None
    built = {o.item_id for o in job.plan.objects if o.status == "ok"}
    rects = _exclusion_rects(job, built)
    out = []
    for kind in job.brief.exclusions:
        kind_rects = rects
        if kind is ExclusionType.POLYGONS and job.brief.exclusion_scope is ExclusionScope.OBJECTS:
            kind_rects = rects + _surface_rects(job)
        out += [(kind.value, r) for r in kind_rects]
    return out


def library_packs(objects: list[PlanObject]) -> list[str]:
    """Add-on library packs the plan needs (X-Plane's own library is always there)."""
    return sorted({p.pack for o in objects for p in o.library if not p.default and p.pack})


def object_files(objects: list[PlanObject]) -> list[str]:
    """Package files of the 3D objects."""
    return [
        f"objects/{o.stem}{suffix}"
        for o in objects
        if o.status == "ok" and o.has_mesh
        for suffix in (".obj", ".png", "_nml.png") + (("_lit.png",) if o.lit else ())
    ]


OBJECT_ITEM_KINDS = (
    "objects",
    "designed",
    "photo_textures",
    "night",
    "library",
    "library_packs",
)


def object_items(objects: list[PlanObject]) -> list[PlanItem]:
    """Plan lines about the 3D objects (recomputed when single objects change)."""
    ok = [o for o in objects if o.status == "ok"]
    items = [PlanItem(kind="objects", label_key="plan.objects", count=sum(o.has_mesh for o in ok))]
    designed = sum(o.designed for o in ok)
    if designed:
        items.append(PlanItem(kind="designed", label_key="plan.designed", count=designed))
    photo_objs = sum(1 for o in ok if o.photo_textures)
    if photo_objs:
        items.append(
            PlanItem(
                kind="photo_textures",
                label_key="plan.photo_textures",
                count=photo_objs,
                params={"photos": len({p for o in ok for p in o.photo_textures})},
            )
        )
    lit = sum(o.lit for o in ok)
    lights = sum(o.lights for o in ok)
    if lit or lights:
        items.append(
            PlanItem(kind="night", label_key="plan.night", count=lit, params={"lights": lights})
        )
    placed = sum(len(o.library) for o in ok)
    if placed:
        items.append(PlanItem(kind="library", label_key="plan.library", count=placed))
    packs = library_packs(objects)
    if packs:
        items.append(
            PlanItem(
                kind="library_packs",
                label_key="plan.library_packs",
                count=len(packs),
                params={"packs": ", ".join(packs)},
            )
        )
    return items


def make_plan(job: Job, job_dir: Path, library: Catalog | None = None) -> BuildPlan:
    """Plan from the confirmed list: generates every object once for the 3D preview
    (job folder only) so the user sees what will be built before approving it."""
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
            if it.included and not it.proposal
        ],
    }
    (job_dir / "scene.geojson").write_text(
        json.dumps(scene, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    detail = job.brief.detail.value
    preview_dir = job_dir / "preview"
    if preview_dir.exists():
        shutil.rmtree(preview_dir)
    results = assemble.generate_all(
        job.analysis.items, detail, preview_dir, True, None, library, job_dir / "photos"
    )
    objects = [PlanObject(**r.__dict__) for r in results]
    surfaces = [
        it for it in job.analysis.items if it.kind == "surface" and it.included and not it.proposal
    ]

    items = [
        PlanItem(kind="confirmed", label_key=_label_key(key), count=count)
        for key, count in sorted(included_counts(job).items())
    ]
    ok = [o for o in objects if o.status == "ok"]
    items += object_items(objects)
    info = job.analysis.imagery
    ortho = job.brief.surface_mode.value == "imagery" and info is not None and info.package_ok
    items.append(
        PlanItem(
            kind="surfaces",
            label_key="plan.surfaces_ortho" if ortho else "plan.surfaces",
            count=len(surfaces),
        )
    )
    files = [tile.dsf_relative_path, "README.txt"] + object_files(objects)
    kinds = ["ortho"] if ortho and surfaces else sorted({str(s.category) for s in surfaces})
    files += [f"polygons/xpsb_{k}.{ext}" for k in kinds for ext in ("pol", "png")]
    rects = _exclusion_rects(job, {o.item_id for o in ok})
    return BuildPlan(
        package_name=job.brief.package_name,
        tile=tile.dsf_relative_path,
        center=(lon, lat),
        detail=detail,
        objects=objects,
        surfaces=len(surfaces),
        surface_mode="imagery" if ortho else "material",
        exclusions=[k.value for k in job.brief.exclusions],
        exclusion_scope=job.brief.exclusion_scope.value,
        exclusion_rects=len(rects),
        items=items,
        files=sorted(files),
    )


def _ortho(job: Job, job_dir: Path) -> assemble.Ortho | None:
    """The aerial image as ground texture, if the user chose that for surfaces."""
    assert job.brief is not None and job.analysis is not None
    info = job.analysis.imagery
    if job.brief.surface_mode.value != "imagery" or info is None or not info.package_ok:
        return None
    path = job_dir / "imagery" / info.file
    if not path.exists():
        return None
    return assemble.Ortho(path, (info.west, info.south, info.east, info.north))


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
    packs = library_packs(plan.objects)
    if packs:
        text += (
            "\nRequires these library packs in Custom Scenery (objects are referenced, "
            "not included): " + ", ".join(packs) + "\n"
        )
    used = photo_texture_photos(plan.objects, job)
    if used:
        text += "\nPhotos used as textures (straightened and cropped):\n" + "\n".join(
            phototex.attribution(p) for p in used
        )
        if any("sa" in (p.license or "").lower() for p in used):
            text += (
                "\nThe textures made from photos under a share-alike licence (CC BY-SA) "
                "are shared under the same licence.\n"
            )
        else:
            text += "\n"
    return text


def photo_texture_photos(objects: list[PlanObject], job: Job) -> list[Photo]:
    """The photos painted onto objects of the plan (for the README and approval C)."""
    wanted = {pid for o in objects if o.status == "ok" for pid in o.photo_textures}
    if not wanted or job.analysis is None:
        return []
    seen: dict[str, Photo] = {}
    for it in job.analysis.items:
        for p in it.photos:
            if p.id in wanted:
                seen[p.id] = p
    return [seen[k] for k in sorted(seen)]


def build_package(
    job: Job,
    job_dir: Path,
    exe: Path,
    log: EventLog,
    library: Catalog | None = None,
    blender_exe: str | None = None,
    blender_version: str | None = None,
) -> BuildResult:
    """Runs in a worker thread."""
    plan = job.plan
    assert plan is not None
    build_root = job_dir / "build"
    package = build_root / plan.package_name
    if package.exists():
        shutil.rmtree(package)
    package.mkdir(parents=True)

    write_key = "stage.write"
    log.publish_threadsafe(type="progress", stage="write", progress=0.0, key=write_key)
    assert job.analysis is not None
    tile = Tile.containing(*plan.center)
    result = assemble.write_package(
        job.analysis.items,
        package,
        tile,
        plan.detail,
        _exclusions(job),
        ortho=_ortho(job, job_dir),
        library=library,
        photo_dir=job_dir / "photos",
        blender_exe=blender_exe,
        blender_progress=lambda done, total, device, left: log.publish_threadsafe(
            type="progress",
            stage="blender",
            progress=done / max(1, total),
            key="stage.blender" if left is not None else "stage.blender_start",
            params={
                "done": done,
                "total": total,
                "device": device,
                "left": f"{int(left) // 60}:{int(left) % 60:02d}" if left is not None else "",
            },
        ),
        progress=lambda p, n=0, total=0, name="": log.publish_threadsafe(
            type="progress",
            stage="write",
            progress=p,
            key=write_key,
            params={"done": n, "total": total, "name": name},
        ),
    )
    text_file = result.dsf_text
    meshes = [o for o in result.objects if o.status == "ok" and o.has_mesh]
    shaded = sum(o.shaded for o in meshes)
    detailed = sum(o.detailed for o in meshes)
    # The 3D view of the result shows the finished textures (same atlas layout as
    # the preview, now with Blender's shading).
    preview_dir = job_dir / "preview"
    for o in meshes:
        if o.shaded and (preview_dir / f"{o.stem}.png").is_file():
            for suffix in (".png", "_nml.png"):
                src = package / "objects" / f"{o.stem}{suffix}"
                if src.is_file():
                    shutil.copyfile(src, preview_dir / f"{o.stem}{suffix}")
    readme = _readme(plan, job)
    if shaded:
        readme += (
            f"\nSoft shading (ambient occlusion) of {shaded} of {len(meshes)} objects "
            f"baked with Blender {blender_version or ''}"
            + (f"; fine facade detail baked into {detailed} of them" if detailed else "")
            + ".\n"
        )
    (package / "README.txt").write_text(readme, encoding="utf-8")

    log.publish_threadsafe(type="progress", stage="compile", progress=0.0, key="stage.compile")
    version = dsftool.version(exe)
    out = dsftool.text2dsf(exe, text_file, package / plan.tile)
    log.publish_threadsafe(type="log", text=out[-1000:])

    log.publish_threadsafe(type="progress", stage="verify", progress=0.0, key="stage.verify")
    roundtrip = build_root / "roundtrip.txt"
    dsftool.dsf2text(exe, package / plan.tile, roundtrip)
    _verify_roundtrip(text_file.read_text(encoding="ascii"), roundtrip.read_text(encoding="ascii"))
    log.publish_threadsafe(type="progress", stage="verify", progress=1.0, key="stage.verified")

    return BuildResult(
        package_dir=str(package),
        files=files_for(package),
        dsftool_version=version,
        blender=blender_version if blender_exe else None,
        shaded=shaded,
        detailed=detailed,
        shaded_stems=[o.stem for o in meshes if o.shaded],
        meshes=len(meshes),
    )


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
        r"^PROPERTY sim/require_object ",
        r"^PROPERTY sim/exclude_",
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
