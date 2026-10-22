"""Builds the objects of a job on all CPU cores.

Running a build script and baking its textures is pure Python/numpy work for one
object at a time, so the objects are spread over worker processes (threads would
wait for each other on Python's global lock).

The workers are this program started again with `--build-worker <dir>` - plain
processes, no multiprocessing machinery (which hangs in the packaged Windows app).
The directory holds what all objects share (context.pkl, with the object of each
task) and one empty task file per object; each worker takes the next free task by
renaming its file (atomic, so no object is built twice) and leaves the result next
to it. The task file is never opened: on Windows a virus scanner or a second worker
may hold a just-renamed file for a moment, so every file access retries a little.
The workers end when no task is left, so none stay behind. Whatever was not built
by them (a worker could not start or died) is built in this process as before.

XPSB_BUILD_WORKERS sets the number of processes (1 = no extra processes);
XPSB_BUILD_STRICT=1 turns a failing worker into an error instead of the fallback.
"""

from __future__ import annotations

import logging
import os
import pickle
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xpsb.build.assemble import ItemResult
    from xpsb.scene import SceneItem
    from xpsb.xplane.library import Catalog

log = logging.getLogger(__name__)

MAX_WORKERS = 8
MIN_OBJECTS = 4  # below this, starting the processes takes longer than it saves
POLL_S = 0.2
CREATE_NO_WINDOW = 0x0800_0000  # Windows: no console window for the workers


def workers(objects: int) -> int:
    """How many processes for this many objects (1 = build here)."""
    wanted = os.environ.get("XPSB_BUILD_WORKERS", "")
    if wanted.isdigit():
        return max(1, min(int(wanted), objects))
    if objects < MIN_OBJECTS:
        return 1
    cores = os.cpu_count() or 1
    # one core stays free for the app (and the 3D preview)
    return max(1, min(MAX_WORKERS, cores - 1, objects))


def worker_command() -> list[str]:
    """This program again: the packaged exe, or Python running the xpsb package."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "xpsb"]


def _retry(fn, attempts: int = 40):
    """Windows: a file can be locked for a moment (virus scanner, another process
    renaming it); wait a little and try again."""
    for n in range(attempts):
        try:
            return fn()
        except PermissionError:
            if n == attempts - 1:
                raise
            time.sleep(min(0.5, 0.02 * (n + 1)))
    return None


def _dump(obj, path: Path) -> None:
    tmp = path.with_suffix(".part")
    data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    _retry(lambda: tmp.write_bytes(data))
    _retry(lambda: tmp.replace(path))  # readers never see half a file


def _load(path: Path):
    return pickle.loads(_retry(path.read_bytes))


def _strict() -> bool:
    return bool(os.environ.get("XPSB_BUILD_STRICT"))


def build(
    todo: list[tuple[SceneItem, str]],
    items: list[SceneItem],
    detail: str,
    out_dir: Path,
    preview: bool,
    library: Catalog | None,
    photo_dir: Path | None,
    blender_exe: str | None,
    on_done: Callable[[int, SceneItem], None],
    n_workers: int,
) -> dict[str, ItemResult]:
    """Builds (item, stem) pairs in n_workers processes; returns results by stem.
    on_done(count done, item) is called here as each one finishes. What could not
    be built in the processes is left out (the caller builds it)."""
    results: dict[str, ItemResult] = {}
    by_stem = {stem: it for it, stem in todo}
    log.info("building %d objects in %d processes", len(todo), n_workers)
    # cleanup errors: a scanner may still hold a file; the system cleans up later
    with tempfile.TemporaryDirectory(prefix="xpsb-build-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        (work / "tasks").mkdir()
        _dump(
            {
                "items": items,
                "tasks": {stem: it.id for it, stem in todo},
                "args": (detail, str(out_dir), preview, library, photo_dir, blender_exe),
            },
            work / "context.pkl",
        )
        for _, stem in todo:
            (work / "tasks" / f"{stem}.task").touch()
        procs: list[tuple[subprocess.Popen, Path]] = []
        try:
            for n in range(n_workers):
                err = work / f"worker{n}.log"
                with open(err, "wb") as err_file:
                    procs.append(
                        (
                            subprocess.Popen(
                                [*worker_command(), "--build-worker", str(work)],
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=err_file,
                                creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
                            ),
                            err,
                        )
                    )
            while True:
                running = any(p.poll() is None for p, _ in procs)
                for res in sorted(work.glob("*.result")):
                    if res.stem not in results:
                        results[res.stem] = _load(res)
                        log.info("built %s (%d of %d)", res.stem, len(results), len(todo))
                        on_done(len(results), by_stem[res.stem])
                if not running:
                    break
                time.sleep(POLL_S)
        except OSError as exc:
            if _strict():
                raise
            log.warning("build processes could not start (%s); building here", exc)
        finally:
            for p, _ in procs:
                if p.poll() is None:  # an error here: no worker builds on
                    p.kill()
                    p.wait()
        failed = [
            (p.returncode, err.read_text(encoding="utf-8", errors="replace")[-3000:])
            for p, err in procs
            if p.returncode != 0
        ]
        if failed:
            code, tail = failed[0]
            if _strict():
                raise RuntimeError(f"build worker failed (exit {code}): {tail}")
            log.warning("a build process failed (exit %s): %s", code, tail)
    return results


def worker(work: Path) -> None:
    """Main of a worker process: takes tasks until none is left."""
    from xpsb.build.assemble import generate_one

    ctx = _load(work / "context.pkl")
    items = ctx["items"]
    by_id = {it.id: it for it in items}
    detail, out_dir, preview, library, photo_dir, blender_exe = ctx["args"]
    mine = work / f"claimed-{os.getpid()}"
    mine.mkdir()
    while True:
        task = None
        for candidate in sorted((work / "tasks").glob("*.task")):
            try:
                task = candidate.replace(mine / candidate.name)  # atomic: only one wins
                break
            except OSError:
                continue  # another worker was faster (or holds it right now)
        if task is None:
            if any((work / "tasks").glob("*.task")):
                time.sleep(0.05)  # only held for a moment by someone: look again
                continue
            return
        stem = task.stem
        result = generate_one(
            by_id[ctx["tasks"][stem]],
            items,
            detail,
            Path(out_dir),
            stem,
            preview,
            library,
            photo_dir,
            blender_exe,
        )
        _dump(result, work / f"{stem}.result")
