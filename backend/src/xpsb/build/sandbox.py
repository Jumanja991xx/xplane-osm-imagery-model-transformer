"""Runs a build script in Starlark: Python-like, but without files, network,
imports, `while` loops or recursion. The only effect a script can have is to
add geometry through the tools; on top come a time limit and a triangle budget.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import starlark as sl

from xpsb.build import tools
from xpsb.build.mesh import GeometryError, Mesh
from xpsb.build.parts import PartTracker

MAX_SOURCE_BYTES = 100_000
GEOMETRY_TOOLS = (
    "extrude",
    "facade",
    "parapet",
    "chimneys",
    "mansard",
    "box",
    "cylinder",
    "tube",
    "sweep",
    "revolve",
    "quad",
    "face",
    "place",
    "light",
    "floodlight",
    "lamp_post",
    "floodlight_mast",
    "flag",
    "figure",
    "pedestal",
    "scan",
)
HELPER_TOOLS = ("oriented_rect", "offset_polygon", "polygon_area", "polygon_centroid", "resample")
DEFAULT_TIMEOUT_S = 5.0


class ScriptError(RuntimeError):
    """Script failed; the message (with line numbers) is meant for the author/AI."""


@dataclass
class ScriptInputs:
    footprint: list[list[float]] | None  # polygon in local metres, counter-clockwise
    path: list[list[float]] | None  # for line objects (fences, walls)
    params: dict[str, Any]
    courtyards: list[list[list[float]]] = field(default_factory=list)  # holes, clockwise
    # Photo textures of the object: name -> {"width", "height", "repeat", "walls"}
    # (used as mat="photo:<name>").
    textures: dict[str, dict] = field(default_factory=dict)
    scan: str | None = None  # path of the object's prepared 3D scan (.npz), if it has one


def _dialect() -> sl.Dialect:
    d = sl.Dialect.standard()
    d.enable_load = False
    d.enable_top_level_stmt = True  # plain loops/ifs at the top level, like Python
    return d


def _tracked(tracker: PartTracker, name: str, fn):
    def call(*args, **kwargs):
        return tracker.call(name, fn, args, kwargs)

    call.__doc__ = fn.__doc__
    return call


def run_script(
    source: str,
    inputs: ScriptInputs,
    max_triangles: int,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    name: str = "build.star",
    library: frozenset[str] | None = None,
) -> Mesh:
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ScriptError("script too long")
    mesh = Mesh(max_triangles=max_triangles)
    t = tools.Tools(mesh, library, frozenset(inputs.textures), inputs.scan)
    module = sl.Module()
    tracker = PartTracker(mesh)
    for fname in GEOMETRY_TOOLS:
        module.add_callable(fname, _tracked(tracker, fname, getattr(t, fname)))
    module.add_callable("part", tracker.name)
    for fname in HELPER_TOOLS:
        module.add_callable(fname, getattr(tools, fname))
    for fname, fn in tools.MATH.items():
        module.add_callable(fname, fn)
    module["PI"] = 3.141592653589793
    module["footprint"] = inputs.footprint
    module["path"] = inputs.path
    module["courtyards"] = inputs.courtyards
    module["params"] = inputs.params
    module["textures"] = inputs.textures
    module["has_scan"] = inputs.scan is not None

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
    if time.monotonic() > deadline:  # finished, but between two cancel checks too late
        raise ScriptError(f"script took longer than {timeout_s:.0f} s")
    if not mesh.patches and not mesh.placements:
        raise ScriptError("the script produced no geometry")
    return mesh
