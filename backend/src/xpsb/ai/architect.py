"""AI architect: on the user's request, Claude writes an own build script for one
object (Starlark, the same sandbox and tools as the default scripts).

Loop: write → run in the sandbox (errors go back to the model) → render views →
the model reviews its result against the request and may improve it once.
Every call is reserved against the budget the user confirmed beforehand.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from xpsb.ai.analyze import LANG_NAMES, AiAuthError, AiCallError, AiRun, Budget, png_block
from xpsb.ai.usage import cost_usd
from xpsb.build import tools
from xpsb.build.materials import MATERIAL_HELP
from xpsb.build.mesh import Mesh
from xpsb.build.render import views_png
from xpsb.build.sandbox import GEOMETRY_TOOLS, HELPER_TOOLS, ScriptError, ScriptInputs, run_script
from xpsb.jobs.models import AiRunInfo

MAX_CALLS = 4  # writing incl. fixes, plus one review
EFFORT = "medium"
MAX_SCRIPT_CHARS = 30_000
# Rough token counts for the estimate (system prompt with the tool reference,
# script, images, and a full script plus thinking as output).
SYSTEM_TOKENS_EST = 4500
IMAGE_TOKENS_EST = 400
OUTPUT_TOKENS_EST = 7000


class ArchitectScript(BaseModel):
    script: str = Field(description="The complete Starlark build script (not a diff).")
    summary: str = Field(
        description="One or two sentences for the user, in their language: what was built "
        "or changed."
    )


class ArchitectReview(BaseModel):
    ok: bool = Field(description="True if the views show what the user asked for.")
    problems: str = Field(description="What does not match yet (empty if ok).")
    script: str | None = Field(
        default=None, description="If not ok: the complete improved script, else null."
    )
    summary: str = Field(description="One or two sentences for the user, in their language.")


@dataclass
class DesignResult:
    script: str | None = None
    summary: str = ""
    mesh: Mesh | None = None
    info: AiRunInfo = field(default_factory=lambda: AiRunInfo(model=""))
    calls: int = 0
    error_key: str | None = None
    error_detail: str | None = None


def tool_reference() -> str:
    """Signatures and docstrings of everything a script may call."""
    lines = []
    t = tools.Tools
    for name in GEOMETRY_TOOLS:
        fn = getattr(t, name)
        sig = str(inspect.signature(fn)).replace("(self, ", "(").replace("(self)", "()")
        lines.append(f"{name}{sig}\n    {inspect.getdoc(fn) or ''}")
    for name in HELPER_TOOLS:
        fn = getattr(tools, name)
        lines.append(f"{name}{inspect.signature(fn)}\n    {inspect.getdoc(fn) or ''}")
    lines.append(
        "Math (angles in degrees): "
        + ", ".join(sorted(tools.MATH))
        + "; constant PI. Built-ins: len, range, min, max, abs, int, float, str, list, dict, "
        "enumerate, zip, sorted, reversed, any, all."
    )
    mats = "\n".join(f"  {k}: {v}" for k, v in MATERIAL_HELP.items())
    return "\n".join(lines) + "\nMaterials (mat=...):\n" + mats


def system_prompt(language: str) -> str:
    return f"""You are the architect of an X-Plane 12 scenery tool. You write build scripts
that turn one real-world object into a 3D model. The user is a flight simmer who wants
the object to look like the real one, recognisable from the air and from the ground,
with sensible detail but no excess (it is scenery, not CAD).

LANGUAGE: Starlark (Python-like). No imports/load, no while loops, no recursion, no
classes, no exceptions, no f-strings (use "%s" % x or str()). Use for loops over
range(). Functions with def are fine. Top-level code runs.

COORDINATES: metres; x east, y north, z up; z=0 is the ground. The object's local
origin is the centre of its outline. `footprint` is the outline polygon as
[[x, y], ...] counter-clockwise (or None for points/lines), `path` the polyline of line
objects, `params` a dict with: {
        ", ".join(
            sorted(
                [
                    "height",
                    "levels",
                    "roof_shape",
                    "roof_color",
                    "wall_color",
                    "seat_color",
                    "covered",
                    "roof_material",
                    "material",
                    "name",
                    "facing",
                    "flip",
                    "rows",
                    "detail",
                ]
            )
        )
    }. facing is a unit vector [x, y] towards the
field/track if known (stands face it). Values may be None: choose good defaults.

RULES:
- Stay within the footprint (overhangs of roofs up to ~2 m are fine). Respect a given
  height unless the user asks otherwise.
- Faces are one-sided like in X-Plane: the tools orient them correctly; for free faces
  (quad/face) use two_sided=True when both sides are visible.
- Keep the triangle count modest: walls are cheap, round things need segments; the
  budget is given below. Exceeding it is an error.
- Colours are "#RRGGBB" strings. Prefer materials that match the real object.
- Write the COMPLETE script every time (it replaces the old one).
- The summary is for the user, in {LANG_NAMES.get(language, "English")}, short, no code.

TOOLS:
{tool_reference()}
"""


def _estimate(model: str, script_chars: int, images: int) -> float:
    input_est = SYSTEM_TOKENS_EST + script_chars / 3.2 + images * IMAGE_TOKENS_EST + 600
    return cost_usd(model, int(input_est), OUTPUT_TOKENS_EST)


def cost_estimate(model: str, script_chars: int) -> tuple[float, float]:
    """(typical: write + review, at most: all calls) in USD."""
    write = _estimate(model, max(script_chars, 4000), 4)
    review = _estimate(model, 8000, 4)
    return write + review, write * (MAX_CALLS - 1) + review


def _clean(script: str) -> str:
    s = script.strip()
    if s.startswith("```"):  # tolerate a fenced answer
        s = s.split("\n", 1)[1] if "\n" in s else ""
        s = s.rsplit("```", 1)[0]
    return s.strip() + "\n"


def design(
    *,
    request: str,
    current_script: str,
    inputs: ScriptInputs,
    max_triangles: int,
    aerial_png: bytes | None,
    cfg: AiRun,
    object_label: str,
    on_step: Callable[[str, dict], None] = lambda key, params: None,
) -> DesignResult:
    result = DesignResult(info=AiRunInfo(model=cfg.model))
    budget = Budget(cfg.budget_usd, result.info, cfg.on_cost)
    system = system_prompt(cfg.language)
    facts = (
        f"Object: {object_label}\n"
        f"params = {json.dumps(inputs.params, ensure_ascii=False)}\n"
        f"footprint = {json.dumps(inputs.footprint)}\n"
        f"path = {json.dumps(inputs.path)}\n"
        f"Triangle budget: {max_triangles}\n"
    )
    if inputs.footprint:
        r = tools.oriented_rect(inputs.footprint)
        facts += (
            f"Outline: about {r['length']:.1f} m x {r['width']:.1f} m, long side heading "
            f"{r['heading']:.0f} deg.\n"
        )

    def views_blocks(mesh: Mesh) -> list[dict]:
        blocks: list[dict] = []
        for name, png in views_png(mesh, inputs.footprint, 448):
            blocks += [{"type": "text", "text": f"{name} (red line = outline):"}, png_block(png)]
        return blocks

    def call(content: list[dict], fmt) -> object | None:
        chars = sum(len(b.get("text", "")) for b in content)
        images = sum(1 for b in content if b["type"] == "image")
        est = _estimate(cfg.model, chars, images)
        if result.calls >= MAX_CALLS or not budget.reserve(est):
            return None
        response = None
        try:
            response = cfg.transport(
                model=cfg.model, system=system, content=content, output_format=fmt, effort=EFFORT
            )
        finally:
            budget.settle(est, response)
            result.calls += 1
        if response.refused or response.parsed is None:
            raise AiCallError("refused")
        return response.parsed

    def try_run(script: str) -> tuple[Mesh | None, str | None]:
        if len(script) > MAX_SCRIPT_CHARS:
            return None, f"script longer than {MAX_SCRIPT_CHARS} characters"
        try:
            return run_script(script, inputs, max_triangles, name="architect.star"), None
        except ScriptError as exc:
            return None, str(exc)[:1500]

    # The current state: how it looks now (if it builds) and the aerial image.
    content: list[dict] = [{"type": "text", "text": f"The user asks: {request}"}]
    content.append({"type": "text", "text": facts})
    if aerial_png:
        content += [
            {"type": "text", "text": "Aerial image (red outline = this object, north up):"},
            png_block(aerial_png),
        ]
    current_mesh, _ = try_run(current_script)
    if current_mesh is not None:
        content.append({"type": "text", "text": "How the object looks now:"})
        content += views_blocks(current_mesh)
    content.append({"type": "text", "text": "Current script:\n" + current_script})

    try:
        on_step("design.step.writing", {})
        out = call(content, ArchitectScript)
        error: str | None = None
        mesh: Mesh | None = None
        script = ""
        while out is not None:
            script = _clean(out.script)  # type: ignore[attr-defined]
            result.summary = out.summary  # type: ignore[attr-defined]
            on_step("design.step.testing", {})
            mesh, error = try_run(script)
            if mesh is not None:
                break
            on_step("design.step.fixing", {"attempt": result.calls + 1})
            fix = [
                {"type": "text", "text": f"The user asks: {request}"},
                {"type": "text", "text": facts},
                {"type": "text", "text": "Your script:\n" + script},
                {"type": "text", "text": "It failed in the sandbox:\n" + (error or "")},
                {"type": "text", "text": "Fix it and return the complete script."},
            ]
            out = call(fix, ArchitectScript)
        if mesh is None:
            result.error_key = "error.architect_failed" if error else "error.ai_budget_exhausted"
            result.error_detail = error
            return result
        result.script, result.mesh = script, mesh

        # Self-check: the model looks at its result once and may improve it.
        on_step("design.step.checking", {})
        review = call(
            [
                {"type": "text", "text": f"The user asked: {request}"},
                {"type": "text", "text": facts},
                *(
                    [{"type": "text", "text": "Aerial image:"}, png_block(aerial_png)]
                    if aerial_png
                    else []
                ),
                {"type": "text", "text": "Your result:"},
                *views_blocks(mesh),
                {"type": "text", "text": "Your script:\n" + script},
                {
                    "type": "text",
                    "text": "Check the views against the request and the aerial image. If "
                    "something is clearly wrong (missing parts, wrong side, floating or "
                    "inside-out parts), return ok=false with the complete improved script.",
                },
            ],
            ArchitectReview,
        )
        if review is not None:
            result.summary = review.summary or result.summary  # type: ignore[attr-defined]
            better = getattr(review, "script", None)
            if not review.ok and better:  # type: ignore[attr-defined]
                on_step("design.step.improving", {})
                better = _clean(better)
                mesh2, _ = try_run(better)
                if mesh2 is not None:  # otherwise keep the first working version
                    result.script, result.mesh = better, mesh2
    except AiAuthError:
        result.error_key = "error.ai_auth_failed"
    except AiCallError as exc:
        result.error_key, result.error_detail = "error.ai_call_failed", str(exc)
    return result
