"""The work behind each approval.

Analysis (M2): OSM objects and surfaces of the area, an open aerial image and,
if the user agreed, Claude's assessment of every item. The build still produces
the test scenery (real 3D generators arrive in M3); the install step copies it
into X-Plane with a backup of any existing package.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import shutil
import threading
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PIL import Image
from shapely.geometry import Polygon, shape
from shapely.ops import unary_union

from xpsb import APP_NAME, __version__, research
from xpsb import photos as photos_mod
from xpsb.ai import analyze as ai
from xpsb.ai import detect
from xpsb.ai.usage import UsageLedger
from xpsb.build import assemble, phototex
from xpsb.catalog import SurfaceMode
from xpsb.geo import METERS_PER_DEG_LAT, meters_per_deg_lon
from xpsb.imagery import retouch, roofs, wms
from xpsb.imagery.sources import candidates, resolve_source
from xpsb.jobs.events import EventLog
from xpsb.jobs.models import (
    AnalysisResult,
    BuildPlan,
    BuildResult,
    CallCost,
    ExclusionScope,
    ExclusionType,
    InstallResult,
    Job,
    JobState,
    Note,
    PlanItem,
    PlanObject,
    scan_model,
)
from xpsb.osm import bridges as bridges_mod
from xpsb.osm import classify, overpass, rows
from xpsb.photos import Photo
from xpsb.settings import Settings
from xpsb.xplane import dsftool, foreign, terrain
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
    dsftool: Path | None = None  # to read X-Plane's terrain (bridge heights)


_COST_LOCK = threading.Lock()


def cost_book(job: Job, ledger: UsageLedger, stage: str, item: str | None = None):
    """on_cost for AI runs: counts the call into the monthly ledger and the job's own
    list of calls (shown as the cost overview when installing). Thread-safe."""

    def add(call: CallCost) -> None:
        ledger.add(call.usd)
        with _COST_LOCK:
            job.costs.append(call.model_copy(update={"stage": stage, "item": item}))

    return add


def ai_budget(job: Job, settings: Settings, ledger: UsageLedger) -> float:
    assert job.brief is not None
    left_this_month = max(0.0, settings.ai.monthly_limit_usd - ledger.month_spent())
    return min(job.brief.ai_cost_limit_usd, left_this_month)


def _progress(log: EventLog, stage: str, value: float, key: str, **params) -> None:
    log.publish_threadsafe(type="progress", stage=stage, progress=value, key=key, params=params)


def _osm_stage(job: Job, deps: AnalysisDeps, log: EventLog, notes: list[Note]) -> list:
    assert job.brief is not None
    _progress(log, "osm", 0.0, "stage.osm")
    try:
        data = overpass.fetch(
            overpass.build_query(job.aoi.ring),
            deps.cache_dir / "overpass",
            ring=[tuple(p) for p in job.aoi.ring],
            own_endpoint=deps.settings.overpass_url,
            on_try=lambda ep: _progress(
                log, "osm", 0.0, "stage.osm_asking", host=urllib.parse.urlsplit(ep).hostname
            ),
        )
    except overpass.OverpassError as exc:
        # Without OSM the job goes on: the AI finds objects in the aerial image,
        # earlier jobs of the area bring back what the user confirmed there.
        notes.append(Note(key="analysis.osm_unavailable", params={"detail": str(exc)[:300]}))
        _progress(log, "osm", 1.0, "stage.osm_done", count=0)
        return []
    if data.get("source") == "cache":
        notes.append(Note(key="analysis.osm_from_cache", params={"days": data.get("age_days")}))
    elif data.get("source") == "osm_api":
        notes.append(Note(key="analysis.osm_from_api"))
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
    first, reason = resolve_source(deps.settings.imagery, lon, lat, deps.esri_key)
    if first is None:
        notes.append(Note(key=reason or "imagery.off"))
        return None
    ring = job.aoi.ring
    area = wms.Bounds(
        min(p[0] for p in ring),
        min(p[1] for p in ring),
        max(p[0] for p in ring),
        max(p[1] for p in ring),
    )
    # "auto": when a server fails, the next source covering the place (noted for the user)
    for source in candidates(deps.settings.imagery, lon, lat, deps.esri_key) or [first]:
        _progress(log, "imagery", 0.0, "stage.imagery", source=source.name)
        try:
            return wms.fetch_area_image(
                source,
                area,
                deps.job_dir / "imagery",
                progress=lambda p, s=source: _progress(
                    log, "imagery", p, "stage.imagery", source=s.name
                ),
            )
        except (wms.WmsError, OSError, ValueError) as exc:
            key = "imagery.esri_failed" if source.id == "esri" else "imagery.failed"
            notes.append(Note(key=key, params={"source": source.name, "detail": str(exc)[:300]}))
    return None


def _apply_memory(items: list, deps: AnalysisDeps) -> int:
    """Brings back what the user decided in earlier jobs of this area."""
    count = 0
    for item in items:
        mem = deps.memory_edits.get(item.osm_ref or "")
        if mem is not None:
            item.category, item.attrs, item.included = mem.category, mem.attrs, mem.included
            item.name = mem.name or item.name
            item.ai, item.remembered = mem.ai, True
            # A design from an earlier job is reused (not paid for again) unless it
            # paints photos of that job onto the walls.
            if mem.script and "photo:" not in mem.script and not item.script:
                item.script, item.script_request = mem.script, mem.script_request
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


CITY_DIR = "city"


def _city_data_stage(job: Job, deps: AnalysisDeps, log: EventLog, notes: list[Note]) -> None:
    """City mode in Bavaria: the official 3D buildings (real roof shapes) of the area,
    stored with the job; elsewhere the OSM buildings are used when planning."""
    from dataclasses import asdict

    from xpsb.city import build as city
    from xpsb.city import lod2

    lon, lat = job.aoi_center
    if not lod2.covers(lon, lat):
        return
    _progress(log, "city", 0.0, "stage.city_data")
    try:
        buildings = city.lod2_buildings(
            [tuple(p) for p in job.aoi.ring],
            deps.cache_dir / "lod2",
            lambda n, total: _progress(
                log, "city", n / max(1, total), "stage.city_data", done=n, total=total
            ),
        )
    except (lod2.Lod2Error, OSError, ValueError) as exc:
        notes.append(Note(key="analysis.city_lod2_failed", params={"detail": str(exc)[:300]}))
        return
    folder = deps.job_dir / CITY_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "lod2.json").write_text(json.dumps([asdict(b) for b in buildings]), "utf-8")
    _progress(log, "city", 1.0, "stage.city_data_done", count=len(buildings))


def city_items(job: Job) -> list:
    """The ordinary buildings the city mode builds in blocks: not the landmarks (worth
    a single detailed object), nor anything with a design of its own."""
    if job.brief is None or job.brief.mode != "city" or job.analysis is None:
        return []
    return [
        it
        for it in job.analysis.items
        if it.kind == "object"
        and str(it.category) == "buildings"
        and it.included
        and not it.proposal
        and not it.script
        and research.score(it) < 30
    ]


def _city_buildings(job: Job, job_dir: Path) -> tuple[list, str]:
    """(city buildings, source): the official 3D data if loaded, else OSM."""
    from xpsb.city import build as city
    from xpsb.city.model import CityBuilding

    assert job.analysis is not None
    ordinary = city_items(job)
    stored = job_dir / CITY_DIR / "lod2.json"
    if stored.is_file():
        raw = json.loads(stored.read_text("utf-8"))
        buildings = [
            CityBuilding(
                **{
                    **b,
                    "footprint": [[tuple(p) for p in r] for r in b["footprint"]],
                    "walls": [[tuple(p) for p in w] for w in b["walls"]],
                    "roofs": [[tuple(p) for p in r] for r in b["roofs"]],
                    "roof_color": tuple(b["roof_color"]) if b.get("roof_color") else None,
                }
            )
            for b in raw
        ]
        ids = {it.id for it in ordinary}
        single = [
            shape(it.geometry)
            for it in job.analysis.items
            if it.kind == "object" and it.included and not it.proposal and it.id not in ids
        ]
        return city.without(buildings, single), "lod2"
    return city.from_items(ordinary), "osm"


def _aerial(job: Job, job_dir: Path):
    """The aerial image for real roofs, if it may go into the package."""
    info = job.analysis.imagery if job.analysis else None
    if info is None or not info.package_ok:
        return None
    path = job_dir / "imagery" / info.file
    if not path.is_file():
        return None
    with Image.open(path) as img:
        image = retouch.apply(
            img.convert("RGB"), (info.west, info.south, info.east, info.north), []
        )
    return image, (info.west, info.south, info.east, info.north)


def build_city(job: Job, job_dir: Path, out_dir: Path, preview_dir: Path | None):
    """The city blocks (city mode) into out_dir; returns (blocks, source, buildings)."""
    from xpsb.city import build as city

    assert job.brief is not None
    buildings, source = _city_buildings(job, job_dir)
    blocks = city.build(
        buildings,
        job.brief.city_preset,
        job.aoi_center,
        out_dir,
        aerial=_aerial(job, job_dir),
        preview_dir=preview_dir,
    )
    return blocks, source, len(buildings)


def _terrain_stage(
    job: Job, items: list, deps: AnalysisDeps, log: EventLog, notes: list[Note]
) -> None:
    """Ground heights from X-Plane's own terrain where they matter: bridges (their
    decks meet the banks, their piers reach the ground, their ramps the roads)."""
    bridges = [it for it in items if str(it.category) == "bridges"]
    if not bridges:
        return
    from shapely.geometry import shape

    geoms = [shape(it.geometry) for it in bridges] + [
        shape(ap) for it in bridges for ap in it.approaches
    ]
    w = min(g.bounds[0] for g in geoms)
    s = min(g.bounds[1] for g in geoms)
    e = max(g.bounds[2] for g in geoms)
    n = max(g.bounds[3] for g in geoms)
    lon, lat = job.aoi_center
    _progress(log, "terrain", 0.0, "stage.terrain", count=len(bridges))
    try:
        ground = terrain.load(
            deps.settings.xplane_path,
            deps.dsftool,
            Tile.containing(lon, lat),
            (w, s, e, n),
            deps.cache_dir / "terrain",
            job.brief.package_name if job.brief else None,
        )
    except terrain.TerrainError as exc:
        notes.append(Note(key="analysis.terrain_failed", params={"detail": str(exc)[:300]}))
        return
    if ground is None:
        notes.append(Note(key="analysis.terrain_unknown", params={"count": len(bridges)}))
        return
    for it in bridges:
        it.ground = bridges_mod.sample_ground(it, ground.height)
    _progress(log, "terrain", 1.0, "stage.terrain_done", count=len(bridges))


def analyze_sync(job: Job, deps: AnalysisDeps, log: EventLog) -> AnalysisResult:
    """Runs in a worker thread."""
    assert job.brief is not None
    notes: list[Note] = []
    items = _osm_stage(job, deps, log, notes)
    remembered = _apply_memory(items, deps)
    if remembered:
        notes.append(Note(key="analysis.remembered", params={"count": remembered}))
    if not items:
        notes.append(Note(key="analysis.nothing_found"))
    _terrain_stage(job, items, deps, log, notes)
    if job.brief.mode == "city":
        _city_data_stage(job, deps, log, notes)
    info = _imagery_stage(job, deps, log, notes)
    # OSM splits a terrace into its houses; built one by one every house got its own
    # roof. They are joined - the aerial image (if any) tells apart different roofs.
    seen: dict[str, str] = {}
    if info is not None:
        with Image.open(deps.job_dir / "imagery" / info.file) as img:
            seen = roofs.measure(items, img, info)
    joined = rows.merge(items, seen)
    if joined:
        notes.append(Note(key="analysis.rows_joined", params={"count": joined}))
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
            model=scan_model(job.brief),
            language=deps.settings.language,
            notes=job.brief.notes,
            budget_usd=budget,
            transport=deps.transport_factory(deps.api_key),
            on_cost=cost_book(job, deps.ledger, "analysis"),
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
                log, "ai", p, "stage.ai", count=len(items), model=scan_model(job.brief)
            )
            run.progress(0.0)
            to_rate = [it for it in items if ai.needs_rating(it)]
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
        fallback = [m for m in result.ai.models_used if m != scan_model(job.brief)]
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
    if info is not None:  # roofs the AI (or OSM, or the user) gave no colour
        with Image.open(deps.job_dir / "imagery" / info.file) as img:
            roofs.apply(result.items, img, info)
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
                if found.entity is not None and str(item.category) == "monuments":
                    scan = research.attach_scan(found.entity, deps.job_dir / "scans")
                    if scan is not None:
                        item.scan = scan
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


def _object_bounds(o: PlanObject) -> Bounds | None:
    """What the built object really covers (its mesh and library objects, moved and
    turned as placed) in lon/lat - often much more than its OSM outline (a statue
    mapped as a point stands on terraces and steps)."""
    if o.extent is None:
        return None
    w, s, e, n = o.extent
    h = math.radians(o.heading)
    lon0, lat0 = o.anchor
    lons, lats = [], []
    for x, y in ((w, s), (e, s), (e, n), (w, n)):
        xr = x * math.cos(h) + y * math.sin(h)
        yr = -x * math.sin(h) + y * math.cos(h)
        lons.append(lon0 + xr / meters_per_deg_lon(lat0))
        lats.append(lat0 + yr / METERS_PER_DEG_LAT)
    return (min(lons), min(lats), max(lons), max(lats))


def _exclusion_rects(job: Job, built: list[PlanObject]) -> list[Bounds]:
    """Where other scenery is removed: one rectangle around each new object - its OSM
    outline and what was really built - or the whole area if the user chose that."""
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
    by_id = {o.item_id: o for o in built}
    rects = []
    for it in job.analysis.items:
        if it.id in by_id:
            w, s, e, n = shape(it.geometry).bounds
            built_bounds = _object_bounds(by_id[it.id])
            if built_bounds is not None:
                w, s = min(w, built_bounds[0]), min(s, built_bounds[1])
                e, n = max(e, built_bounds[2]), max(n, built_bounds[3])
            rects.append((w - dx, s - dy, e + dx, n + dy))
    return rects


def _foreign_zones(job: Job, built: list[PlanObject], reader) -> tuple[list[dict], list[str]]:
    """Exclusion zones for what other packs place on our objects' places."""
    assert job.brief is not None and job.analysis is not None
    if reader is None or not built or not job.brief.exclusions:
        return [], []
    from shapely.geometry import box as bbox_shape

    by_id = {o.item_id: o for o in built}
    ours = []
    for it in job.analysis.items:
        if it.id not in by_id:
            continue
        geom = shape(it.geometry)
        if geom.geom_type in ("LineString", "Point"):
            lat = geom.centroid.y
            width = (it.attrs.width_m or 4.0) / 2
            geom = geom.buffer(width / ((meters_per_deg_lon(lat) + METERS_PER_DEG_LAT) / 2))
        bounds = _object_bounds(by_id[it.id])
        ours.append(geom if bounds is None else geom.union(bbox_shape(*bounds)))
    if not ours:
        return [], []
    w, s, e, n = unary_union(ours).bounds
    found, failed = reader((w, s, e, n))
    wanted = {k.value for k in job.brief.exclusions}
    zones = [
        {"kind": p.kind, "bounds": list(foreign.zone(p)), "pack": p.pack, "path": p.path}
        for p in foreign.overlapping(found, ours)
        if p.kind in wanted
    ]
    return zones, failed


def _surface_rects(job: Job) -> list[Bounds]:
    assert job.analysis is not None
    return [
        shape(it.geometry).bounds
        for it in job.analysis.items
        if it.kind == "surface" and it.included and not it.proposal
    ]


def _exclusions(job: Job) -> list[tuple[str, Bounds]]:
    assert job.brief is not None and job.plan is not None
    built = [o for o in job.plan.objects if o.status == "ok"]
    rects = _exclusion_rects(job, built)
    out = []
    for kind in job.brief.exclusions:
        kind_rects = rects
        if (
            kind is ExclusionType.POLYGONS
            and job.brief.exclusion_scope is ExclusionScope.OBJECTS
            and job.brief.surface_mode is not SurfaceMode.NONE
        ):
            kind_rects = rects + _surface_rects(job)
        out += [(kind.value, r) for r in kind_rects]
    out += [(z["kind"], tuple(z["bounds"])) for z in job.plan.foreign_zones]
    if job.brief.mode == "city":  # the whole city is ours: X-Plane's own houses go
        ring = job.aoi.ring
        area = (
            min(p[0] for p in ring),
            min(p[1] for p in ring),
            max(p[0] for p in ring),
            max(p[1] for p in ring),
        )
        out += [(kind, area) for kind in ("objects", "facades")]
    return out


def library_packs(objects: list[PlanObject]) -> list[str]:
    """Add-on library packs the plan needs (X-Plane's own library is always there)."""
    return sorted({p.pack for o in objects for p in o.library if not p.default and p.pack})


def object_files(objects: list[PlanObject]) -> list[str]:
    """Package files of the 3D objects."""
    return [
        f"objects/{o.stem}{suffix}"
        for o in objects
        if o.status == "ok" and o.has_mesh and o.category != "city"  # see plan.city
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
    ok = [o for o in objects if o.status == "ok" and o.category != "city"]
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


def make_plan(
    job: Job,
    job_dir: Path,
    library: Catalog | None = None,
    foreign_reader: Callable | None = None,
) -> BuildPlan:
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
    in_blocks = {it.id for it in city_items(job)}
    results = assemble.generate_all(
        [it for it in job.analysis.items if it.id not in in_blocks],
        detail,
        preview_dir,
        True,
        None,
        library,
        job_dir / "photos",
    )
    objects = [PlanObject(**r.__dict__) for r in results]
    city_summary = None
    if job.brief.mode == "city":
        blocks, source, count = build_city(job, job_dir, job_dir / CITY_DIR / "build", preview_dir)
        for b in blocks:
            for obj in b.objects:
                objects.append(
                    PlanObject(
                        item_id=f"city/{b.n}",
                        stem=obj[:-4],
                        category="city",
                        anchor=b.anchor,
                        status="ok",
                        triangles=b.triangles if obj.endswith("_walls.obj") else 0,
                    )
                )
        city_summary = {
            "source": source,
            "buildings": sum(b.buildings for b in blocks),
            "blocks": len(blocks),
            "triangles": sum(b.triangles for b in blocks),
            "files": sorted(
                {f"objects/{f}" for b in blocks for f in b.files}
                | {"objects/city_atlas.png", "objects/city_atlas_LIT.png"}
            ),
        }
    surfaces = [
        it for it in job.analysis.items if it.kind == "surface" and it.included and not it.proposal
    ]
    if job.brief.surface_mode is SurfaceMode.NONE:  # the ground stays as it is
        surfaces = []

    items = [
        PlanItem(kind="confirmed", label_key=_label_key(key), count=count)
        for key, count in sorted(included_counts(job).items())
    ]
    ok = [o for o in objects if o.status == "ok"]
    items += object_items(objects)
    if city_summary:
        items.append(
            PlanItem(
                kind="city",
                label_key="plan.city_lod2" if city_summary["source"] == "lod2" else "plan.city_osm",
                count=city_summary["blocks"],
                params={"buildings": city_summary["buildings"]},
            )
        )
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
    if city_summary:
        files += city_summary["files"]
    kinds = ["ortho"] if ortho and surfaces else sorted({str(s.category) for s in surfaces})
    files += [f"polygons/xpsb_{k}.{ext}" for k in kinds for ext in ("pol", "png")]
    rects = _exclusion_rects(job, ok)
    zones, failed = _foreign_zones(job, ok, foreign_reader)
    if zones:
        by_pack: dict[str, int] = {}
        for z in zones:
            by_pack[z["pack"]] = by_pack.get(z["pack"], 0) + 1
        items.append(
            PlanItem(
                kind="foreign",
                label_key="plan.foreign",
                count=len(zones),
                params={"packs": ", ".join(f"{p} ({n})" for p, n in sorted(by_pack.items()))},
            )
        )
    if failed:
        items.append(
            PlanItem(
                kind="foreign_unread",
                label_key="plan.foreign_unread",
                count=len(failed),
                params={"packs": ", ".join(failed)},
            )
        )
    surface_mode = "imagery" if ortho else "material"
    if job.brief.surface_mode is SurfaceMode.NONE:
        surface_mode = "none"
    return BuildPlan(
        package_name=job.brief.package_name,
        tile=tile.dsf_relative_path,
        center=(lon, lat),
        detail=detail,
        objects=objects,
        surfaces=len(surfaces),
        surface_mode=surface_mode,
        exclusions=[k.value for k in job.brief.exclusions],
        exclusion_scope=job.brief.exclusion_scope.value,
        exclusion_rects=len(rects),
        foreign_zones=zones,
        city=city_summary,
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
    return assemble.Ortho(
        path,
        (info.west, info.south, info.east, info.north),
        retouch.areas(job.analysis.items),
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
    if plan.city and plan.city.get("source") == "lod2":
        from xpsb.city.lod2 import ATTRIBUTION_BAYERN

        text += f"{ATTRIBUTION_BAYERN}\n"
    packs = library_packs(plan.objects)
    if packs:
        text += (
            "\nRequires these library packs in Custom Scenery (objects are referenced, "
            "not included): " + ", ".join(packs) + "\n"
        )
    scanned = {o.item_id for o in plan.objects if o.status == "ok" and o.scan}
    items = job.analysis.items if job.analysis else []
    scans = [it.scan for it in items if it.id in scanned and it.scan]
    if scans:
        text += "\n3D scans used (reduced, from Wikimedia Commons):\n"
        for s in scans:
            author = s.author or "unknown author"
            text += f"- {s.title}: {author}, {s.license or ''}, {s.page_url or ''}\n"
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
    blender_stop: Callable[[], bool] = lambda: False,
    blender_cpu: bool = False,
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
    items = job.analysis.items
    if job.brief.surface_mode is SurfaceMode.NONE:  # no surfaces: the ground stays as it is
        items = [it for it in items if it.kind != "surface"]
    extra: list[tuple[str, float, float]] = []
    if job.brief.mode == "city":
        log.publish_threadsafe(type="progress", stage="city", progress=0.0, key="stage.city_build")
        in_blocks = {it.id for it in city_items(job)}
        items = [it for it in items if it.id not in in_blocks]
        blocks, _, _ = build_city(job, job_dir, package / "objects", None)
        extra = [(f"objects/{obj}", *b.anchor) for b in blocks for obj in b.objects]
    result = assemble.write_package(
        items,
        package,
        tile,
        plan.detail,
        _exclusions(job),
        ortho=_ortho(job, job_dir),
        library=library,
        photo_dir=job_dir / "photos",
        blender_exe=blender_exe,
        blender_stop=blender_stop,
        blender_cpu=blender_cpu,
        blender_progress=lambda s: log.publish_threadsafe(
            type="progress",
            stage="blender",
            progress=s.done / max(1, s.total),
            key=f"stage.blender_{s.phase}",
            params={
                "done": s.done,
                "total": s.total,
                "current": s.current,
                "device": s.device,
                "left": f"{int(s.left) // 60}:{int(s.left) % 60:02d}"
                if s.left is not None
                else "?",
            },
        ),
        progress=lambda p, n=0, total=0, name="": log.publish_threadsafe(
            type="progress",
            stage="write",
            progress=p,
            key=write_key,
            params={"done": n, "total": total, "name": name},
        ),
        extra_objects=extra,
    )
    text_file = result.dsf_text
    if result.outside:  # city blocks in a neighbouring tile: that tile's own DSF
        _write_other_tiles(result.outside, package, exe)
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
            f"\nSoft shading (ambient occlusion), weathering and rounded edges of {shaded} of "
            f"{len(meshes)} objects "
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
        blender_problem=next((o.blender_problem for o in meshes if o.blender_problem), ""),
        blender_log=str(build_root / "blender.log") if blender_exe else None,
        shaded_stems=[o.stem for o in meshes if o.shaded],
        meshes=len(meshes),
    )


def replace_placements(job: Job, job_dir: Path, exe: Path) -> list[Path]:
    """After the build: puts the objects where the plan now says (moved/turned in
    the 3D view of the result) without building anything again - only the DSF
    (object placements and exclusion zones) is written anew, in the package and,
    if installed, in Custom Scenery. Returns the DSF files written."""
    from xpsb.xplane.dsf_text import EXCLUSION_PROPERTIES

    plan = job.plan
    assert plan is not None and job.build is not None
    package = Path(job.build.package_dir)
    text_path = package.parent / f"{package.name}.dsf.txt"
    lines = text_path.read_text(encoding="ascii").splitlines()
    defs = [ln[len("OBJECT_DEF ") :] for ln in lines if ln.startswith("OBJECT_DEF ")]
    index = {p: i for i, p in enumerate(defs)}
    objects = []
    for o in plan.objects:
        if o.status != "ok":
            continue
        lon0, lat0 = o.anchor
        own = index.get(f"objects/{o.stem}.obj")
        if o.has_mesh and own is not None:
            objects.append(f"OBJECT {own} {lon0:.9f} {lat0:.9f} {o.heading % 360.0:.3f}")
        h = math.radians(o.heading)
        for p in o.library:
            if p.path not in index:
                continue
            x = p.x * math.cos(h) + p.y * math.sin(h)
            y = -p.x * math.sin(h) + p.y * math.cos(h)
            objects.append(
                f"OBJECT {index[p.path]} {lon0 + x / meters_per_deg_lon(lat0):.9f} "
                f"{lat0 + y / METERS_PER_DEG_LAT:.9f} {(p.heading + o.heading) % 360.0:.3f}"
            )
    exclusions = [
        f"PROPERTY {EXCLUSION_PROPERTIES[kind]} {w:.6f}/{s:.6f}/{e:.6f}/{n:.6f}"
        for kind, (w, s, e, n) in _exclusions(job)
    ]
    out: list[str] = []
    placed = False
    for ln in lines:
        if ln.startswith("PROPERTY sim/exclude_") or ln.startswith("OBJECT "):
            if ln.startswith("OBJECT ") and not placed:
                out += objects
                placed = True
            continue
        if ln.startswith("PROPERTY sim/creation_agent"):
            out.append(ln)
            out += exclusions
            continue
        if ln.startswith("BEGIN_POLYGON") and not placed:
            out += objects
            placed = True
        out.append(ln)
    if not placed:
        out += objects
    text_path.write_text("\n".join(out) + "\n", encoding="ascii", newline="\n")
    written = [package / plan.tile]
    dsftool.text2dsf(exe, text_path, written[0])
    if job.state is JobState.INSTALLED and job.install is not None:
        installed = Path(job.install.target_dir) / plan.tile
        if installed.parent.is_dir():
            shutil.copyfile(written[0], installed)
            written.append(installed)
    return written


def _write_other_tiles(outside: list, package: Path, exe: Path) -> None:
    from xpsb.xplane.dsf_text import DsfText

    by_tile: dict[str, tuple[Tile, list]] = {}
    for path, lon, lat in outside:
        tile = Tile.containing(lon, lat)
        by_tile.setdefault(tile.dsf_relative_path, (tile, []))[1].append((path, lon, lat))
    for rel, (tile, objs) in by_tile.items():
        dsf = DsfText(tile=tile)
        for path, lon, lat in objs:
            dsf.add_object(dsf.add_object_def(path), lon, lat, 0.0)
        text = package.parent / f"{package.name}.{tile.lon}_{tile.lat}.dsf.txt"
        text.write_text(dsf.to_text(), encoding="ascii", newline="\n")
        dsftool.text2dsf(exe, text, package / rel)


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
