"""Approval gates A–D.

A gate summarises *what will happen, what it costs and where it writes*. The UI
shows exactly these items and sends back the hash; the backend recomputes the
gate and only proceeds if nothing changed in between.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

from xpsb import research
from xpsb.ai import detect
from xpsb.geo import METERS_PER_DEG_LAT, meters_per_deg_lon
from xpsb.imagery.sources import resolve_source
from xpsb.imagery.wms import MARGIN_M
from xpsb.jobs.models import GateId, GateItem, Job, JobState, PendingGate, scan_model
from xpsb.settings import Settings
from xpsb.xplane_detect import check_xplane_path

GATE_FOR_STATE: dict[JobState, GateId] = {
    JobState.CLARIFIED: "A",
    JobState.ANALYZED: "B",
    JobState.PLANNED: "C",
    JobState.BUILT: "D",
}


@dataclass(frozen=True)
class GateContext:
    settings: Settings
    has_api_key: bool
    dsftool: Path | None
    xplane_running: bool | None
    job_dir: Path
    ai_month_spent: float = 0.0
    has_mapillary: bool = False
    # Objects the AI architect would build at approval B: {id, name, typical, max}
    design: tuple = ()
    design_cut: int = 0  # left out because of the cost limit
    esri_key: str | None = None  # never shown or stored; only decides the source
    blender: str | None = None  # version of the user's Blender, if it will be used


def content_hash(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def install_target(settings: Settings, package_name: str) -> Path | None:
    if not settings.xplane_path:
        return None
    return Path(settings.xplane_path) / "Custom Scenery" / package_name


def packs_above(target: Path, dsf_relative: str) -> list[str]:
    """Enabled packs listed before ours in scenery_packs.ini that have a DSF for the
    same tile. Empty if our pack is not listed yet: X-Plane adds new packs at the top."""
    ini = target.parent / "scenery_packs.ini"
    try:
        lines = ini.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    names: list[tuple[str, bool]] = []
    for line in lines:
        key, _, rest = line.strip().partition(" ")
        if key in ("SCENERY_PACK", "SCENERY_PACK_DISABLED"):
            name = rest.strip().replace("\\", "/").rstrip("/").split("/")[-1]
            names.append((name, key == "SCENERY_PACK"))
    ours = [i for i, (name, _) in enumerate(names) if name == target.name]
    if not ours:
        return []
    return [
        name
        for name, enabled in names[: ours[0]]
        if enabled and (target.parent / name / dsf_relative).is_file()
    ]


def compute_gate(job: Job, ctx: GateContext) -> PendingGate | None:
    gate = GATE_FOR_STATE.get(job.state)
    if gate is None:
        return None
    items, blocked, payload = {"A": _gate_a, "B": _gate_b, "C": _gate_c, "D": _gate_d}[gate](
        job, ctx
    )
    payload |= {
        "gate": gate,
        "job": job.id,
        "items": [i.model_dump() for i in items],
        "blocked": blocked,
    }
    return PendingGate(gate=gate, items=items, blocked=blocked, hash=content_hash(payload))


def _gate_a(job: Job, ctx: GateContext):
    assert job.brief is not None
    b = job.brief
    blocked = False
    items = [
        GateItem(kind="action", key="gate.a.osm", params={"area_km2": round(job.aoi_area_km2, 3)}),
    ]
    imagery = ctx.settings.imagery
    source, reason = resolve_source(imagery, *job.aoi_center, ctx.esri_key)
    if source is not None:
        items.append(
            GateItem(
                kind="action",
                key="gate.a.imagery",
                params={
                    "source": source.name,
                    "license": source.license,
                    "attribution": source.attribution,
                },
            )
        )
        if not source.package_ok:
            items.append(GateItem(kind="info", key="gate.a.imagery_analysis_only"))
        if not source.builtin and not imagery.custom_rights_confirmed:
            items.append(GateItem(kind="warning", key="gate.a.custom_rights"))
            blocked = True
    else:
        items.append(GateItem(kind="warning", key=f"gate.a.{reason}"))

    if b.use_ai:
        month_left = max(0.0, ctx.settings.ai.monthly_limit_usd - ctx.ai_month_spent)
        items.append(
            GateItem(
                kind="cost",
                key="gate.a.ai_cost",
                params={
                    "model": scan_model(b),
                    "limit_usd": round(min(b.ai_cost_limit_usd, month_left), 2),
                },
            )
        )
        items.append(
            GateItem(
                kind="info",
                key="gate.a.ai_month",
                params={
                    "spent_usd": round(ctx.ai_month_spent, 2),
                    "limit_usd": ctx.settings.ai.monthly_limit_usd,
                },
            )
        )
        if source is not None:
            items.append(
                GateItem(kind="action", key="gate.a.ai_upload", params={"model": scan_model(b)})
            )
            if b.ai_detect:
                tiles = _tile_count(job)
                items.append(
                    GateItem(
                        kind="action",
                        key="gate.a.ai_detect",
                        params={
                            "tiles": tiles,
                            "passes": detect.PASSES,
                            "estimate_usd": round(
                                tiles * detect.PASSES * detect.estimate_tile_cost(scan_model(b)), 3
                            ),
                        },
                    )
                )
        else:
            items.append(GateItem(kind="warning", key="gate.a.ai_needs_imagery"))
        if not ctx.has_api_key:
            items.append(GateItem(kind="warning", key="gate.a.ai_no_key"))
            blocked = True
        elif month_left <= 0:
            items.append(GateItem(kind="warning", key="gate.a.ai_month_exhausted"))
    else:
        items.append(GateItem(kind="cost", key="gate.a.no_ai"))
    if b.research:
        sources = "Wikidata, Wikipedia, Wikimedia Commons, Panoramax"
        items.append(
            GateItem(
                kind="action",
                key="gate.a.research",
                params={
                    "max": research.MAX_OBJECTS,
                    "sources": sources + (", Mapillary" if ctx.has_mapillary else ""),
                },
            )
        )
    lo, hi = analysis_minutes(b, _tile_count(job), source is not None)
    items.append(GateItem(kind="info", key="gate.a.duration", params={"min": lo, "max": hi}))
    items.append(GateItem(kind="write", key="gate.a.workdir", params={"path": str(ctx.job_dir)}))
    payload = {
        "aoi": job.aoi.model_dump(),
        "brief": b.model_dump(mode="json"),
        "imagery": imagery.model_dump(),
    }
    return items, blocked, payload


def _speed(model: str) -> float:
    """Rough answer time of a model relative to Sonnet."""
    return 1.4 if "opus" in model else 0.5 if "haiku" in model else 1.0


def _minutes(lo: float, hi: float) -> tuple[int, int]:
    return max(1, round(lo)), max(1, math.ceil(hi))


def analysis_minutes(b, tiles: int, imagery: bool) -> tuple[int, int]:
    """Usual duration of the analysis in minutes (shown before it starts)."""
    lo, hi = 0.2, 1.0  # OSM and aerial image
    f = _speed(scan_model(b))
    if b.use_ai:
        lo, hi = lo + 0.3 * f, hi + 1.5 * f  # rating every item
        if b.ai_detect and imagery:  # up to 4 images at a time, each pass
            rounds = detect.PASSES * math.ceil(tiles / 4)
            lo, hi = lo + 0.7 * rounds * f, hi + 2.5 * rounds * f
    if b.research:
        lo, hi = lo + 0.2, hi + 1.0
    return _minutes(lo, hi)


def design_minutes(model: str, count: int, parallel: int = 3) -> tuple[int, int]:
    """Usual duration of the AI architect for `count` objects, `parallel` at a time."""
    rounds = math.ceil(count / parallel)
    f = _speed(model)
    return _minutes(1.0 * rounds * f, 3.0 * rounds * f + 0.5)


def _tile_count(job: Job) -> int:
    ring = job.aoi.ring
    lat = job.aoi_center[1]
    width = (max(p[0] for p in ring) - min(p[0] for p in ring)) * meters_per_deg_lon(lat)
    height = (max(p[1] for p in ring) - min(p[1] for p in ring)) * METERS_PER_DEG_LAT
    return detect.count_tiles(width + 2 * MARGIN_M, height + 2 * MARGIN_M)


def _gate_b(job: Job, ctx: GateContext):
    assert job.analysis is not None
    a = job.analysis
    included = [it for it in a.items if it.included]
    items = [
        GateItem(
            kind="info",
            key="gate.b.counts",
            params={
                "objects": sum(1 for it in included if it.kind == "object"),
                "surfaces": sum(1 for it in included if it.kind == "surface"),
                "excluded": sum(1 for it in a.items if not it.included and not it.proposal),
            },
        )
    ]
    open_proposals = sum(1 for it in a.items if it.proposal)
    if open_proposals:
        items.append(
            GateItem(kind="info", key="gate.b.open_proposals", params={"count": open_proposals})
        )
    if a.ai is not None:
        items.append(
            GateItem(
                kind="cost",
                key="gate.b.ai_spent",
                params={"cost_usd": round(a.ai.cost_usd, 4), "analyzed": a.ai.analyzed},
            )
        )
    open_questions = [q.id for q in a.questions if q.id not in job.answers]
    if open_questions:
        items.append(
            GateItem(
                kind="warning", key="gate.b.open_questions", params={"count": len(open_questions)}
            )
        )
    if not included:
        items.append(GateItem(kind="warning", key="gate.b.nothing_included"))
    if ctx.design:
        b = job.brief
        assert b is not None
        names = [d["name"] for d in ctx.design]
        items.append(
            GateItem(
                kind="action",
                key="gate.b.design",
                params={
                    "count": len(ctx.design),
                    "names": ", ".join(names[:10]) + (" …" if len(names) > 10 else ""),
                },
            )
        )
        items.append(
            GateItem(kind="action", key="gate.b.design_upload", params={"model": b.ai_model})
        )
        lo, hi = design_minutes(b.ai_model, len(ctx.design))
        items.append(
            GateItem(kind="info", key="gate.b.design_duration", params={"min": lo, "max": hi})
        )
        items.append(
            GateItem(
                kind="cost",
                key="gate.b.design_cost",
                params={
                    "estimate_usd": round(sum(d["typical"] for d in ctx.design), 2),
                    "max_usd": round(sum(d["max"] for d in ctx.design), 2),
                    "model": b.ai_model,
                },
            )
        )
    if ctx.design_cut:
        items.append(
            GateItem(kind="warning", key="gate.b.design_cut", params={"count": ctx.design_cut})
        )
    items.append(GateItem(kind="action", key="gate.b.make_plan"))
    items.append(
        GateItem(
            kind="write",
            key="gate.b.scene_file",
            params={"path": str(ctx.job_dir / "scene.geojson")},
        )
    )
    payload = {
        "analysis": a.model_dump(mode="json"),
        "answers": job.answers,
        "design": [(d["id"], round(d["max"], 4)) for d in ctx.design],
    }
    return items, bool(open_questions) or not included, payload


def _gate_c(job: Job, ctx: GateContext):
    assert job.plan is not None
    p = job.plan
    items = [
        GateItem(
            kind="info", key="gate.c.confirmed", params={"label": i.label_key, "count": i.count}
        )
        for i in p.items
        if i.kind == "confirmed"
    ]
    items += [
        GateItem(kind="action", key=f"plan.{i.kind}", params={"count": i.count, **i.params})
        for i in p.items
        if i.kind != "confirmed"
    ]
    ok = sum(1 for o in p.objects if o.status == "ok")
    if p.exclusions and (p.exclusion_rects or p.exclusion_scope == "area"):
        items += [
            GateItem(
                kind="action",
                key=f"gate.c.exclude_{p.exclusion_scope}",
                params={"label": f"exclusion.{kind}", "count": p.exclusion_rects},
            )
            for kind in p.exclusions
        ]
    elif ok and not p.test_scenery:
        # The usual surprise: simHeaven/autogen houses stay inside the new objects.
        items.append(GateItem(kind="warning", key="gate.c.no_exclusions"))
    b = job.brief
    if b is not None and b.surface_mode.value == "imagery" and p.surface_mode != "imagery":
        items.append(GateItem(kind="info", key="gate.c.ortho_not_allowed"))
    if p.surface_mode == "imagery" and p.surfaces:
        items.append(GateItem(kind="info", key="gate.c.ortho_in_package"))
    assert job.analysis is not None
    scripts = {
        it.id: hashlib.sha256(it.script.encode()).hexdigest()[:16]
        for it in job.analysis.items
        if it.script and it.included and not it.proposal
    }
    if scripts:
        items.append(
            GateItem(kind="info", key="gate.c.custom_scripts", params={"count": len(scripts)})
        )
    failed = [o for o in p.objects if o.status == "error"]
    skipped = [o for o in p.objects if o.status == "skipped"]
    if failed:
        items.append(
            GateItem(kind="warning", key="gate.c.objects_failed", params={"count": len(failed)})
        )
    if skipped:
        items.append(
            GateItem(kind="info", key="gate.c.objects_skipped", params={"count": len(skipped)})
        )
    items.append(
        GateItem(
            kind="write",
            key="gate.c.write_package",
            params={"path": str(ctx.job_dir / "build" / p.package_name)},
        )
    )
    meshes = sum(1 for o in p.objects if o.status == "ok" and o.has_mesh)
    if ctx.blender and meshes:
        items.append(
            GateItem(
                kind="action",
                key="gate.c.blender",
                params={"version": ctx.blender, "minutes": max(1, round((30 + meshes * 6) / 60))},
            )
        )
    elif ctx.settings.use_blender and meshes:
        items.append(GateItem(kind="info", key="gate.c.no_blender"))
    blocked = ctx.dsftool is None
    if blocked:
        items.append(GateItem(kind="warning", key="gate.c.no_dsftool"))
    payload = {
        "plan": p.model_dump(mode="json"),
        "dsftool": str(ctx.dsftool),
        "scripts": scripts,
        "blender": ctx.blender,
    }
    return items, blocked, payload


def _gate_d(job: Job, ctx: GateContext):
    assert job.plan is not None
    items: list[GateItem] = []
    target = install_target(ctx.settings, job.plan.package_name)
    blocked = False
    exists = False
    if target is None or not check_xplane_path(ctx.settings.xplane_path or "").valid:
        items.append(GateItem(kind="warning", key="gate.d.no_xplane"))
        blocked = True
    else:
        exists = target.exists()
        items.append(GateItem(kind="write", key="gate.d.target", params={"path": str(target)}))
        if exists:
            items.append(GateItem(kind="action", key="gate.d.backup", params={"path": str(target)}))
        above = packs_above(target, job.plan.tile)
        if above:
            # Our removals only reach packs listed below ours; these would keep
            # their houses inside the new objects (airport sceneries, mostly).
            items.append(
                GateItem(
                    kind="warning",
                    key="gate.d.packs_above",
                    params={"packs": ", ".join(above[:5]), "count": len(above)},
                )
            )
        if ctx.xplane_running:
            items.append(GateItem(kind="warning", key="gate.d.xplane_running"))
        items.append(GateItem(kind="info", key="gate.d.restart_hint"))
    payload = {
        "target": str(target),
        "exists": exists,
        "running": ctx.xplane_running,
        "above": packs_above(target, job.plan.tile) if target else [],
    }
    return items, blocked, payload
