"""Runs a build script in Starlark: Python-like, but without files, network,
imports, `while` loops or recursion. The only effect a script can have is to
add geometry through the tools; on top come a time limit and a triangle budget.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import starlark as sl

from xpsb.build import tools
from xpsb.build.mesh import GeometryError, Mesh

MAX_SOURCE_BYTES = 100_000
DEFAULT_TIMEOUT_S = 5.0


class ScriptError(RuntimeError):
    """Script failed; the message (with line numbers) is meant for the author/AI."""


@dataclass
class ScriptInputs:
    footprint: list[list[float]] | None  # polygon in local metres, counter-clockwise
    path: list[list[float]] | None  # for line objects (fences, walls)
    params: dict[str, Any]


def _dialect() -> sl.Dialect:
    d = sl.Dialect.standard()
    d.enable_load = False
    d.enable_top_level_stmt = True  # plain loops/ifs at the top level, like Python
    return d


def run_script(
    source: str,
    inputs: ScriptInputs,
    max_triangles: int,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    name: str = "build.star",
) -> Mesh:
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ScriptError("script too long")
    mesh = Mesh(max_triangles=max_triangles)
    t = tools.Tools(mesh)
    module = sl.Module()
    for fname in ("extrude", "box", "cylinder", "tube", "sweep", "revolve", "quad", "face"):
        module.add_callable(fname, getattr(t, fname))
    for fname in (
        "oriented_rect",
        "offset_polygon",
        "polygon_area",
        "polygon_centroid",
        "resample",
    ):
        module.add_callable(fname, getattr(tools, fname))
    for fname, fn in tools.MATH.items():
        module.add_callable(fname, fn)
    module["PI"] = 3.141592653589793
    module["footprint"] = inputs.footprint
    module["path"] = inputs.path
    module["params"] = inputs.params

    try:
        ast = sl.parse(name, source, _dialect())
    except sl.StarlarkError as exc:
        raise ScriptError(str(exc)) from None

    deadline = time.monotonic() + timeout_s
    options = sl.EvalOptions(
        check_cancelled=lambda: time.monotonic() > deadline, max_callstack_size=50
    )
    try:
        sl.eval_with(options, module, ast, sl.Globals.standard())
    except sl.StarlarkError as exc:
        if time.monotonic() > deadline:
            raise ScriptError(f"script took longer than {timeout_s:.0f} s") from None
        raise ScriptError(str(exc)) from None
    except GeometryError as exc:
        raise ScriptError(str(exc)) from None
    if not mesh.patches:
        raise ScriptError("the script produced no geometry")
    return mesh
