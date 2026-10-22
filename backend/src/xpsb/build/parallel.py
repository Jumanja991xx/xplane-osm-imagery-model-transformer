"""Builds the objects of a job on all CPU cores.

Running a build script and baking its textures is pure Python/numpy work for one
object at a time, so the objects are spread over worker processes (threads would
wait for each other on Python's global lock). The pool lives only for one build:
no processes stay behind when the app closes. If processes cannot be started (or
one dies), the rest is built in this process as before.

XPSB_BUILD_WORKERS sets the number of processes (1 = no extra processes);
XPSB_BUILD_STRICT=1 turns a failing pool into an error instead of the fallback.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xpsb.build.assemble import ItemResult
    from xpsb.scene import SceneItem
    from xpsb.xplane.library import Catalog

log = logging.getLogger(__name__)

MAX_WORKERS = 8
MIN_OBJECTS = 4  # below this, starting the processes takes longer than it saves

_ctx: dict = {}  # in a worker: what all objects of this build share


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


def _init(items, detail, out_dir, preview, library, photo_dir, blender_exe) -> None:
    _ctx.update(
        items=items,
        by_id={it.id: it for it in items},
        args=(detail, out_dir, preview, library, photo_dir, blender_exe),
    )


def _one(item_id: str, stem: str) -> ItemResult:
    from xpsb.build.assemble import generate_one

    detail, out_dir, preview, library, photo_dir, blender_exe = _ctx["args"]
    return generate_one(
        _ctx["by_id"][item_id],
        _ctx["items"],
        detail,
        out_dir,
        stem,
        preview,
        library,
        photo_dir,
        blender_exe,
    )


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
    on_done(count done, item) is called in this process as each one finishes.
    What could not be built in the processes is left out (the caller builds it)."""
    results: dict[str, ItemResult] = {}
    pool = None
    log.info("building %d objects in %d processes", len(todo), n_workers)
    try:
        pool = ProcessPoolExecutor(
            max_workers=n_workers,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_init,
            initargs=(items, detail, out_dir, preview, library, photo_dir, blender_exe),
        )
        futures = {pool.submit(_one, it.id, stem): (it, stem) for it, stem in todo}
        for fut in as_completed(futures):
            it, stem = futures[fut]
            try:
                results[stem] = fut.result()
            except BrokenProcessPool:
                raise
            except Exception:  # noqa: BLE001 - built again here, where errors show
                log.exception("building %s in a worker failed", it.id)
                continue
            log.info("built %s (%d of %d)", stem, len(results), len(todo))
            on_done(len(results), it)
    except (BrokenProcessPool, OSError, RuntimeError) as exc:
        if os.environ.get("XPSB_BUILD_STRICT"):  # smoke test of the packaged app
            raise
        log.warning("parallel build stopped (%s); building the rest in one process", exc)
    finally:
        if pool is not None:  # on any error: nothing waiting is started any more
            pool.shutdown(wait=True, cancel_futures=True)
    return results
