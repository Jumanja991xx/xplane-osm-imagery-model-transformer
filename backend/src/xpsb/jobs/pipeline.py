"""The work behind each approval.

Milestone 1: the analysis is a clearly labelled placeholder (real OSM/imagery/AI
analysis arrives in M2). The build produces the test scenery through the real
toolchain (writers → DSFTool → roundtrip check), and the install step copies it
into X-Plane with a backup of any existing package.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from datetime import datetime
from pathlib import Path

from xpsb import APP_NAME, __version__
from xpsb.jobs.events import EventLog
from xpsb.jobs.models import (
    AnalysisResult,
    BuildPlan,
    BuildResult,
    InstallResult,
    Job,
    PlanItem,
    Question,
    QuestionOption,
)
from xpsb.xplane import dsftool
from xpsb.xplane.dsf_text import Tile
from xpsb.xplane.testscene import build_test_scenery, files_for

TEST_CUBE_QUESTION = "test_cube_height"
TEST_CUBE_HEIGHTS = ("10", "20", "40")


async def run_analysis(job: Job, log: EventLog, step_delay: float) -> AnalysisResult:
    for stage in ("osm", "imagery", "ai"):
        log.publish(type="progress", stage=stage, progress=0.0, key=f"stage.{stage}")
        await asyncio.sleep(step_delay)
        log.publish(type="progress", stage=stage, progress=1.0, key="stage.placeholder_done")
    return AnalysisResult(
        placeholder=True,
        notes=["analysis.m1_placeholder"],
        questions=[
            Question(
                id=TEST_CUBE_QUESTION,
                text_key="question.test_cube_height",
                options=[
                    QuestionOption(value=h, label_key="option.meters", params={"value": h})
                    for h in TEST_CUBE_HEIGHTS
                ],
                suggested="20",
            )
        ],
    )


def make_plan(job: Job) -> BuildPlan:
    assert job.brief is not None
    lon, lat = job.aoi_center
    tile = Tile.containing(lon, lat)
    height = float(job.answers.get(TEST_CUBE_QUESTION, "20"))
    return BuildPlan(
        package_name=job.brief.package_name,
        tile=tile.dsf_relative_path,
        center=(lon, lat),
        test_scenery=True,
        cube_height_m=height,
        items=[
            PlanItem(
                kind="test_object",
                label_key="plan.test_object",
                count=1,
                params={"height_m": height},
            ),
            PlanItem(kind="test_surface", label_key="plan.test_surface", count=2),
        ],
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


def _readme(plan: BuildPlan) -> str:
    return (
        f"{plan.package_name}\n"
        f"Created with {APP_NAME} {__version__} on {datetime.now():%Y-%m-%d}.\n\n"
        "Map data (c) OpenStreetMap contributors, available under the Open Database "
        "License (ODbL): https://www.openstreetmap.org/copyright\n"
    )


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
    text_file = build_test_scenery(package, lon, lat, plan.cube_height_m or 20.0)
    (package / "README.txt").write_text(_readme(plan), encoding="utf-8")

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
