"""AI architect: on the user's request, Claude writes an own build script for one
object (Starlark, the same sandbox and tools as the default scripts).

Loop: write → run in the sandbox (errors go back to the model) → render views →
the model reviews its result against the request and may improve it once.
Every call is reserved against the budget the user confirmed beforehand.
"""

from __future__ import annotations

import base64
import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from pydantic import BaseModel, Field

from xpsb.ai.analyze import LANG_NAMES, AiAuthError, AiCallError, AiRun, Budget, png_block
from xpsb.ai.usage import cost_usd
from xpsb.build import tools
from xpsb.build.materials import MATERIAL_HELP
from xpsb.build.mesh import Mesh
from xpsb.build.render import views_png
from xpsb.build.sandbox import GEOMETRY_TOOLS, HELPER_TOOLS, ScriptError, ScriptInputs, run_script
from xpsb.jobs.models import AiRunInfo
from xpsb.scene import PhotoTexture
from xpsb.xplane.library import Catalog

MAX_CALLS = 4  # writing incl. fixes, plus one review
EFFORT = "medium"
MAX_SCRIPT_CHARS = 30_000
# Rough token counts for the estimate (system prompt with the tool reference,
# script, images, and a full script plus thinking as output).
SYSTEM_TOKENS_EST = 5500
IMAGE_TOKENS_EST = 400
OUTPUT_TOKENS_EST = 12000  # thinking + a detailed script
# Output limit per answer (thinking + script); streamed, so long answers are fine.
MAX_OUTPUT = 48_000
MIN_OUTPUT = 6_000
FIRST_SHARE = 0.6
TOO_LONG = (
    "Your previous answer was cut off at the output limit before it was complete. Write "
    "the COMPLETE script again, much more compactly: helper functions and loops instead "
    "of repeating similar calls, fewer separate parts, no comments. Keep the main shape "
    "and the most visible details."
)


class TextureSpec(BaseModel):
    """A facade cut out of a photo by the AI (becomes a PhotoTexture of the item)."""

    name: str = Field(description="Short lowercase name, e.g. front, side_east, stand_bays.")
    photo_id: str = Field(description="id of the photo it is cut from.")
    corners: list[list[float]] = Field(
        description="Four corners of the facade in the photo as [x, y] fractions of the "
        "photo's width and height from its top-left corner, in the order top-left, "
        "top-right, bottom-right, bottom-left of the facade."
    )
    width_m: float = Field(description="Real width of the marked part in metres.")
    height_m: float = Field(description="Real height of the marked part in metres.")
    repeat: bool = Field(
        description="True for a sample of a repeating facade (tiled at its real size), "
        "false for a whole facade (fitted onto the face it is used on)."
    )


class ArchitectScript(BaseModel):
    script: str = Field(description="The complete Starlark build script (not a diff).")
    textures: list[TextureSpec] = Field(
        default_factory=list,
        description="Photo textures the script uses as mat='photo:<name>' (empty if none). "
        "Repeat ones you still need; textures already listed in the facts stay available.",
    )
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
    textures: list[TextureSpec] = Field(
        default_factory=list, description="New or changed photo textures of that script."
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
    textures: list[PhotoTexture] = field(default_factory=list)  # made by the AI


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
[[x, y], ...] counter-clockwise (or None for points/lines), `courtyards` the inner
courtyards of the outline (list of polygons, clockwise; pass them as holes=courtyards so
they stay open), `path` the polyline of line objects, `params` a dict with: {
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
                    "building",
                    "tags",
                    "min_height",
                    "parts",
                ]
            )
        )
    }. facing is a unit vector [x, y] towards the
field/track if known (stands face it). Values may be None: choose good defaults.
`tags` are the OSM tags (site:name/site:type: the grounds the object stands on, e.g. a
stadium or castle; site:derived: the outline was estimated from the grounds - trust the
aerial image more than the outline then). `parts` are OSM building parts (each with
footprint, height, min_height, roof_shape, colours): a mapper's description of towers,
wings and raised roofs - use them as the shape if present.

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

BUILDING KIT - use it for every building with windows: it builds the details with real
depth (windows set into the wall with reveals and panes, pilasters, string courses, main
cornice, rusticated ground storey, doors), keeps your script short (cheaper) and looks
consistent. facade() for the walls (style "classic"/"arched"/"house"/"modern"; set
storeys and bay = side length / window axes from the photos), mansard() for the roofs
of big blocks and wings around courtyards, parapet(mat="balustrade", statues=n) for
palaces with balustrades and figures, chimneys() for houses and old buildings. Build
towers, domes, porticos, pediments, quadrigas and special shapes with the basic tools
on top. If facade() returns "flat", the triangle budget was too small for real windows.

HOW TO BUILD COMMON THINGS (adapt to the facts and photos):
- Stadium/arena: a bowl of stands along the outline (sweep along the closed outline,
  closed_path=True, a stepped profile going inwards = rows of seats), an outer facade,
  the roof as a ring over the stands or a full roof as on the photos. The pitch itself is
  ground, not your job. Big stadiums: 2 tiers, 25-45 m high.
- Hall/warehouse/sports hall: walls (metal_sheet or the photo's facade), flat or low
  pitched roof (roof_metal), gates and doors as paint panels, skylights, gutters.
- House: facade(style="house", top=None), pitched roof (roof_tiles) with faces/quads,
  chimneys(); apartment blocks: facade(style="modern"), balconies as thin boxes.
- Church: nave with steep roof, tower with spire (box + pyramid faces or cylinder with
  radius_top), apse.
- Palace, town hall, court, museum, station (historic, monumental): facade(style=
  "classic" or "arched", storeys and bay counted on the photos, holes=courtyards),
  mansard() or hipped roofs, parapet(mat="balustrade", statues=...) if the photos show
  one; a portico of cylinders with a triangular pediment (face), corner pavilions and
  towers as separate higher blocks with their own facade() and roofs, statues on the
  roof or in front with figure(), a dome with revolve().
- Statue/monument: pedestal() (a stepped plinth; round=True for a drum) and figure() on
  its top with the pose, robe and held thing the photos and facts show (e.g. a woman
  holding a palm frond high above her head: pose="arms_raised", holding="palm",
  robe=True; an equestrian statue: pose="rider"). Use the real heights from the facts
  (pedestal and figure separately if known) and turn it to face where the real statue
  faces. Bronze statues mat="bronze" (green patina: color="#5E8C77"), marble or
  sandstone mat="stone". Several figures (groups, reliefs around a pedestal): several
  figure() calls, smaller. Obelisks and columns with the basic tools. If has_scan is
  True, the object has a real 3D scan: prefer scan() (it is the statue itself, usually
  with its base) and add only what the scan lacks (a higher pedestal, the plaza).
- Painted facade materials (only where real windows are not wanted): windows (modern),
  windows_house, windows_classic, windows_arched; plain walls: plaster, stone, brick,
  glass, metal_sheet, concrete.
- Grandstand: rows of seats (sweep a stepped profile along the front), roof on columns.
- Use offset_polygon for setbacks and upper storeys, sweep for cornices, roof edges and
  canopies, the X-Plane library (if listed) for lamps, fences, trees, vehicles, flags -
  but only things that are really there (aerial image, photos), no decoration for its
  own sake, and nothing outside the object's own plot.
- Colours: take wall and roof colours from the photos if there are any (walls are rarely
  visible from above); without photos use the typical colours of the region and type.
- Night (X-Plane shows it): windows light up by themselves (some rooms lit). Add what
  the real object has: floodlight() for monuments, palaces, churches and bridges that
  are lit at night, floodlight_mast() or light() along the roof edge for sports
  grounds, lamp_post() for lamps on the object's own plot. Flags that are really there:
  flag() with the real colours (it waves and turns with the wind).
- Stadium stands: rows as "seats" treads and "seat_riser" risers (the seat backs seen
  from the pitch), "stand_fascia" for the front of an upper tier, translucent inner roof
  edges as "roof_clear", modern outer facades as "lamella" or a photo texture.

PHOTO TEXTURES - the way hand-made sceneries get their look: simple shapes, real
photos. If a photo shows a facade straight enough and unobstructed (no trees/cars over
most of it) and the facts say it may be used as texture, cut it out: return it in
`textures` (photo id, the four corners of the facade in the photo, its real width and
height in metres) and use mat="photo:<name>" on the wall it shows - a quad() or face()
for one wall, extrude() for all walls of a block. A whole facade (repeat=false) is
fitted onto each face it is used on, so use it on faces of about its size. For
repeating facades (stadium lamellas, office grids, arcades) mark a clean sample of a
few bays (repeat=true) and use it on long walls: it tiles at its real size. Photo walls
need no facade() windows (the photo shows them); keep facade() for walls no photo
shows. Textures listed under "Photo textures" in the facts already exist and can be
used as they are. In the check views photo walls look grey; that is expected.

TOOLS:
{tool_reference()}
"""


def _estimate(model: str, script_chars: int, images: int) -> float:
    input_est = SYSTEM_TOKENS_EST + script_chars / 3.2 + images * IMAGE_TOKENS_EST + 600
    return cost_usd(model, int(input_est), OUTPUT_TOKENS_EST)


PHOTO_TOKENS_EST = 1100  # a 1024 px photo


def cost_estimate(
    model: str, script_chars: int, photos: int = 0, library_lines: int = 0
) -> tuple[float, float]:
    """(typical: write + review, at most: all calls) in USD."""
    extra = cost_usd(model, photos * PHOTO_TOKENS_EST + library_lines * 18, 0)
    write = _estimate(model, max(script_chars, 4000), 4) + extra
    review = _estimate(model, 8000, 4) + extra
    return write + review, write * (MAX_CALLS - 1) + review


def jpeg_block(jpeg: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.b64encode(jpeg).decode("ascii"),
        },
    }


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
    photos: list[tuple[str, bytes]] | None = None,
    library: Catalog | None = None,
    research: str | None = None,
    photo_ids: dict[str, bool] | None = None,
) -> DesignResult:
    """`photo_ids`: the item's photos -> may be used as texture (licence)."""
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
    if inputs.textures:
        facts += "Photo textures (mat='photo:<name>'): " + json.dumps(inputs.textures) + "\n"
    if research:
        facts += (
            "What is known about the real object (Wikidata, Wikipedia, OSM; may be "
            "incomplete - photos and aerial image win where they disagree):\n"
            + research.strip()
            + "\n"
        )
    offer = library.offer(request + " " + (research or "")) if library else []
    lib_set = frozenset(library.objects) if library else None
    sizes = {o.path: o.size for o in offer if o.size}
    if offer:
        facts += (
            "X-Plane library objects you may place with place(path, x, y, heading) - "
            "ready-made, textured, free of the triangle budget; prefer them for lamps, "
            "fences, trees, vehicles, benches, signs, flags and similar furniture "
            "(size: width x depth x height in m, if known):\n"
            + "\n".join(
                f"  {o.path}"
                + (f"  ({o.size[0]:g} x {o.size[1]:g} x {o.size[2]:g})" if o.size else "")
                for o in offer
            )
            + "\n"
        )
    else:
        facts += "No X-Plane library is available: do not use place().\n"
    if inputs.footprint:
        r = tools.oriented_rect(inputs.footprint)
        facts += (
            f"Outline: about {r['length']:.1f} m x {r['width']:.1f} m, long side heading "
            f"{r['heading']:.0f} deg.\n"
        )

    def views_blocks(mesh: Mesh) -> list[dict]:
        blocks: list[dict] = []
        for name, png in views_png(mesh, inputs.footprint, 448, sizes):
            blocks += [{"type": "text", "text": f"{name} (red line = outline):"}, png_block(png)]
        return blocks

    def call(content: list[dict], fmt) -> object | None:
        """One request. A script cut off at the output limit is asked for once more,
        written more compactly; the answer length never exceeds the confirmed budget."""
        retried = False
        while True:
            chars = sum(len(b.get("text", "")) for b in content)
            images = sum(1 for b in content if b["type"] == "image")
            est = _estimate(cfg.model, chars, images)
            # Longest answer the rest of this budget pays for (input first).
            in_cost = est - cost_usd(cfg.model, 0, OUTPUT_TOKENS_EST)
            per_token = cost_usd(cfg.model, 0, 1_000_000) / 1_000_000
            room = int((budget.free() - in_cost) / per_token) if per_token > 0 else MAX_OUTPUT
            # The first try leaves room for a second, more compact one.
            max_out = min(MAX_OUTPUT, room if retried else int(room * FIRST_SHARE))
            if result.calls >= MAX_CALLS or max_out < MIN_OUTPUT or not budget.reserve(est):
                if retried:  # cut off and no budget left for another try
                    raise AiCallError("output limit", key="error.ai_too_long")
                return None
            response = None
            try:
                response = cfg.transport(
                    model=cfg.model,
                    system=system,
                    content=content,
                    output_format=fmt,
                    effort=EFFORT,
                    max_tokens=max_out,
                )
            finally:
                budget.settle(est, response)
                result.calls += 1
            if response.stop_reason == "max_tokens":
                if retried:
                    raise AiCallError("output limit", key="error.ai_too_long")
                retried = True
                content = [*content, {"type": "text", "text": TOO_LONG}]
                continue
            if response.refused:
                raise AiCallError("refused", key="error.ai_refused")
            if response.parsed is None:
                raise AiCallError("no valid answer", key="error.ai_bad_answer")
            return response.parsed

    usable = {pid for pid, ok in (photo_ids or {}).items() if ok}
    made: dict[str, PhotoTexture] = {}

    def take_textures(specs) -> str | None:
        """The AI's photo textures; an error text if one is unusable."""
        for spec in specs or []:
            if spec.photo_id not in usable:
                return f"photo {spec.photo_id!r} cannot be used as texture"
            try:
                made[spec.name] = PhotoTexture(
                    name=spec.name,
                    photo_id=spec.photo_id,
                    corners=spec.corners,
                    width_m=spec.width_m,
                    height_m=spec.height_m,
                    repeat=spec.repeat,
                    source="ai",
                )
            except ValueError as exc:
                return f"texture {spec.name!r}: {str(exc)[:300]}"
        return None

    def try_run(script: str) -> tuple[Mesh | None, str | None]:
        if len(script) > MAX_SCRIPT_CHARS:
            return None, f"script longer than {MAX_SCRIPT_CHARS} characters"
        textures = dict(inputs.textures)
        for t in made.values():
            textures[t.name] = {"width": t.width_m, "height": t.height_m, "repeat": t.repeat}
        try:
            mesh = run_script(
                script,
                replace(inputs, textures=textures),
                max_triangles,
                name="architect.star",
                library=lib_set,
            )
            return mesh, None
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
    photo_blocks: list[dict] = []
    for caption, jpeg in photos or []:
        photo_blocks += [{"type": "text", "text": caption}, jpeg_block(jpeg)]
    if photo_blocks:
        content.append(
            {
                "type": "text",
                "text": "Photos of the real object (from the user or open sources; may show "
                "it from any side and partly other things - use them for shape, materials, "
                "colours and details):",
            }
        )
        content += photo_blocks
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
            error = take_textures(getattr(out, "textures", None))
            mesh = None
            if error is None:
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
        result.textures = list(made.values())

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
                *photo_blocks,
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
                kept = dict(made)
                mesh2 = None
                if take_textures(getattr(review, "textures", None)) is None:
                    mesh2, _ = try_run(better)
                if mesh2 is not None:  # otherwise keep the first working version
                    result.script, result.mesh = better, mesh2
                    result.textures = list(made.values())
                else:
                    made.clear()
                    made.update(kept)
    except AiAuthError:
        result.error_key = "error.ai_auth_failed"
    except AiCallError as exc:
        result.error_key = exc.key or "error.ai_call_failed"
        result.error_detail = str(exc)
    return result
