"""Runs INSIDE Blender (blender --background --factory-startup --python ao_bake.py -- LIST).

A fixed, reviewed script that ships with the app - the AI never writes code for
Blender. It bakes ambient occlusion (the soft shadow in corners, under cornices,
inside window reveals and near the ground) of objects into images laid out like
their texture atlases. One Blender start for all objects of a package; the
graphics card is used when Cycles supports it (OptiX/CUDA, HIP, oneAPI, Metal).

LIST: a text file, one "IN.npz<TAB>AO.png<TAB>NORMAL.png" per line. "--cpu" before
it skips the search for a graphics card (used when that search hangs).
IN: positions (N, 3; metres, z up), uvs (N, 2; atlas coordinates, t up), receivers
(M, 3; triangles that own their piece of the atlas), occluders (K, 3; everything
else that casts shadow), size (2,), samples (), distance ().
Optional high_positions/high_tris: fine detail (window surrounds, dentils ...)
whose relief is baked onto the receivers as a tangent-space normal map
(high-to-low, "selected to active").
AO: greyscale PNG (white = open, dark = occluded). NORMAL: RGB PNG, flat
(128, 128, 255) where no detail was hit; only written when there is detail.
Also per object, next to AO: AO_weather.png (red: how weathered - dirt in corners,
splashes, rain streaks, blotches, moss; green: worn edges) and AO_bevel.png (rounded
edges as a tangent-space normal map).
Prints "XPSB_START" first, "XPSB_DEVICE <kind> <name>" once, then per object
"XPSB_BEGIN <n>", "XPSB_WEATHER <n>" (weathering and edges), "XPSB_RELIEF <n>" (when
the detail bake starts) and "XPSB_DONE <n>".
"""

import contextlib
import sys

import bpy
import numpy as np

GPU_KINDS = ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL")


INTEGRATED = (
    "radeon(tm) graphics",
    "radeon graphics",
    "radeon vega",
    "radeon 780m",
    "radeon 760m",
    "radeon 890m",
    "radeon 880m",
    "intel(r) uhd",
    "intel(r) iris",
    "intel(r) arc(tm) graphics",
    "intel(r) graphics",
)


def integrated(name: str) -> bool:
    """Graphics built into the processor (AMD Ryzen "Radeon(TM) Graphics", Intel UHD/
    Iris/Arc Graphics) rather than a graphics card of its own."""
    n = " ".join(name.lower().split())
    n = n.removeprefix("amd ")
    return any(n.startswith(k) for k in INTEGRATED)


def _use_gpu(scene) -> str:
    """Switches Cycles to the graphics card if there is a supported one; says
    which, or why not (for the progress line)."""
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except (KeyError, AttributeError) as exc:
        return f"CPU (Cycles settings not available: {exc})"
    tried = []
    for kind in GPU_KINDS:
        try:
            prefs.compute_device_type = kind
        except (TypeError, ValueError):
            continue  # not built into this Blender / this system
        with contextlib.suppress(AttributeError):
            prefs.refresh_devices()
        try:
            devices = list(prefs.get_devices_for_type(kind))
        except (AttributeError, TypeError, ValueError):
            prefs.get_devices()
            devices = [d for d in prefs.devices if d.type == kind]
        gpus = [d for d in devices if d.type == kind]
        tried.append(f"{kind}:{len(gpus)}")
        if gpus:
            # A graphics card of its own beats the graphics unit built into the
            # processor (and baking on both at once is slower than on the card).
            dedicated = [d for d in gpus if not integrated(d.name)]
            chosen = dedicated or gpus
            for d in prefs.devices:
                d.use = d in chosen
            scene.cycles.device = "GPU"
            names = ", ".join(dict.fromkeys(d.name for d in chosen))
            kind_word = "graphics card" if dedicated else "processor graphics"
            return f"{kind} {names} ({kind_word})"
    scene.cycles.device = "CPU"
    return "CPU (no graphics card Cycles can use: " + (", ".join(tried) or "none") + ")"


def _mesh(name, positions, tris, uvs=None):
    me = bpy.data.meshes.new(name)
    me.from_pydata(positions.tolist(), [], tris.tolist())
    if uvs is not None:
        layer = me.uv_layers.new(name="UV")
        loop_vertex = np.empty(len(me.loops), dtype=np.int32)
        me.loops.foreach_get("vertex_index", loop_vertex)
        layer.data.foreach_set("uv", uvs[loop_vertex].astype(np.float32).ravel())
    me.update()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def _clear(scene) -> None:
    for ob in list(scene.collection.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.images, bpy.data.materials):
        for block in list(coll):
            coll.remove(block)


def _extra(dst: str, kind: str) -> str:
    """Where the extra bakes of an object go: next to its AO image."""
    stem = dst[:-4] if dst.lower().endswith(".png") else dst
    return f"{stem}_{kind}.png"


def _node(tree, kind, **inputs):
    n = tree.nodes.new(kind)
    for key, value in inputs.items():
        if key.startswith("_"):
            setattr(n, key[1:], value)
        else:
            n.inputs[key].default_value = value
    return n


def _output(tree):
    """The material's output node (the one Blender renders and bakes)."""
    for n in tree.nodes:
        if n.type == "OUTPUT_MATERIAL":
            n.is_active_output = True
            return n
    return tree.nodes.new("ShaderNodeOutputMaterial")


def _math(tree, op, a, b=None, clamp=False):
    n = tree.nodes.new("ShaderNodeMath")
    n.operation = op
    n.use_clamp = clamp
    for i, v in enumerate((a, b)):
        if v is None:
            continue
        if isinstance(v, (int, float)):
            n.inputs[i].default_value = v
        else:
            tree.links.new(v, n.inputs[i])
    return n.outputs[0]


def _range(tree, value, a, b, lo=0.0, hi=1.0):
    n = tree.nodes.new("ShaderNodeMapRange")
    n.clamp = True
    tree.links.new(value, n.inputs["Value"])
    n.inputs["From Min"].default_value = a
    n.inputs["From Max"].default_value = b
    n.inputs["To Min"].default_value = lo
    n.inputs["To Max"].default_value = hi
    return n.outputs["Result"]


def _noise(tree, position, scale, stretch=(1.0, 1.0, 1.0), detail=3.0):
    mapping = tree.nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = stretch
    tree.links.new(position, mapping.inputs["Vector"])
    noise = _node(tree, "ShaderNodeTexNoise", Scale=scale, Detail=detail)
    tree.links.new(mapping.outputs["Vector"], noise.inputs["Vector"])
    return noise.outputs["Fac"]


def _bake_weather(scene, rec, mat, node, w, h, dst) -> None:
    """How weathered each spot is, the way real buildings age: dirt in corners and
    joints, splashes at the foot of the walls, rain streaks running down the walls,
    blotches on walls and roofs (moss, lichen) - red channel; worn, lighter edges -
    green channel. Painted into the colour texture afterwards."""
    tree = mat.node_tree
    for link in list(tree.links):
        tree.links.remove(link)
    geo = tree.nodes.new("ShaderNodeNewGeometry")
    pos = geo.outputs["Position"]
    xyz = tree.nodes.new("ShaderNodeSeparateXYZ")
    tree.links.new(geo.outputs["Normal"], xyz.inputs["Vector"])
    nz = xyz.outputs["Z"]
    wall = _math(tree, "SUBTRACT", 1.0, _math(tree, "ABSOLUTE", nz), clamp=True)
    up = _math(tree, "MAXIMUM", nz, 0.0)
    height = tree.nodes.new("ShaderNodeSeparateXYZ")
    tree.links.new(pos, height.inputs["Vector"])

    ao = _node(tree, "ShaderNodeAmbientOcclusion", Distance=0.8, _samples=8)
    corners = _range(tree, ao.outputs["AO"], 0.35, 0.95, 1.0, 0.0)
    splash = _math(tree, "MULTIPLY", _range(tree, height.outputs["Z"], 0.0, 1.4, 1.0, 0.0), wall)
    # Streaks: noise stretched along the height (rain runs down), only on walls.
    streaks = _range(tree, _noise(tree, pos, 3.0, (1.6, 1.6, 0.12), 4.0), 0.5, 0.78)
    streaks = _math(tree, "MULTIPLY", streaks, wall)
    blotches = _range(tree, _noise(tree, pos, 0.35, (1.0, 1.0, 1.0), 5.0), 0.45, 0.75)
    moss = _math(tree, "MULTIPLY", _range(tree, _noise(tree, pos, 1.2, detail=6.0), 0.5, 0.8), up)

    dirt = _math(tree, "MULTIPLY", corners, 0.8)
    for part, k in ((splash, 0.45), (streaks, 0.4), (blotches, 0.22), (moss, 0.45)):
        dirt = _math(tree, "ADD", dirt, _math(tree, "MULTIPLY", part, k))
    dirt = _math(tree, "MINIMUM", dirt, 1.0)

    bevel = _node(tree, "ShaderNodeBevel", Radius=0.06, _samples=8)
    dot = tree.nodes.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    tree.links.new(bevel.outputs["Normal"], dot.inputs[0])
    tree.links.new(geo.outputs["Normal"], dot.inputs[1])
    edges = _range(tree, dot.outputs["Value"], 0.995, 0.85)

    combine = tree.nodes.new("ShaderNodeCombineXYZ")
    tree.links.new(dirt, combine.inputs["X"])
    tree.links.new(edges, combine.inputs["Y"])
    emit = tree.nodes.new("ShaderNodeEmission")
    tree.links.new(combine.outputs["Vector"], emit.inputs["Color"])
    out = _output(tree)
    tree.links.new(emit.outputs["Emission"], out.inputs["Surface"])

    image = bpy.data.images.new("weather", w, h, alpha=False, float_buffer=False)
    image.colorspace_settings.name = "Non-Color"
    node.image = image
    tree.nodes.active = node
    scene.cycles.samples = 8
    bpy.ops.object.select_all(action="DESELECT")
    rec.select_set(True)
    bpy.context.view_layer.objects.active = rec
    bpy.ops.object.bake(type="EMIT", margin=4, use_clear=True)
    image.filepath_raw = dst
    image.file_format = "PNG"
    image.save()


def _bake_bevel(scene, rec, mat, node, w, h, dst) -> None:
    """Rounded edges as a normal map: real corners are never razor sharp, they catch
    the light along a thin line - one of the things that make a model look built."""
    tree = mat.node_tree
    for link in list(tree.links):
        tree.links.remove(link)
    bevel = _node(tree, "ShaderNodeBevel", Radius=0.04, _samples=8)
    bsdf = tree.nodes.new("ShaderNodeBsdfDiffuse")
    tree.links.new(bevel.outputs["Normal"], bsdf.inputs["Normal"])
    out = _output(tree)
    tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    image = bpy.data.images.new("bevel", w, h, alpha=False)
    image.colorspace_settings.name = "Non-Color"
    image.generated_color = (0.5, 0.5, 1.0, 1.0)
    node.image = image
    tree.nodes.active = node
    scene.cycles.samples = 8
    scene.render.bake.normal_space = "TANGENT"
    bpy.ops.object.select_all(action="DESELECT")
    rec.select_set(True)
    bpy.context.view_layer.objects.active = rec
    bpy.ops.object.bake(type="NORMAL", margin=4, use_clear=True)
    image.filepath_raw = dst
    image.file_format = "PNG"
    image.save()


def bake_one(scene, src: str, dst: str, dst_normal: str | None = None, n: int = 0) -> None:
    with np.load(src) as d:
        positions = d["positions"].astype(np.float64)
        uvs, receivers, occluders = d["uvs"], d["receivers"], d["occluders"]
        w, h = (int(v) for v in d["size"])
        samples = int(d["samples"])
        scene.cycles.samples = samples
        scene.world.light_settings.distance = float(d["distance"])
        high_pos = d["high_positions"].astype(np.float64) if "high_positions" in d else None
        high_tris = d.get("high_tris")
        dw, dh = (int(v) for v in d["detail_size"]) if "detail_size" in d else (w, h)
    _clear(scene)
    rec = _mesh("receivers", positions, receivers, uvs)
    if len(occluders):
        _mesh("occluders", positions, occluders)
    high = None
    if high_pos is not None and len(high_tris):
        high = _mesh("detail", high_pos, high_tris)  # casts shade in the AO bake too
    # The ground darkens the foot of the walls.
    lo, hi = positions.min(axis=0), positions.max(axis=0)
    span = float(max(hi[0] - lo[0], hi[1] - lo[1])) + 40.0
    cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
    ground = np.array(
        [
            [cx - span, cy - span, -0.02],
            [cx + span, cy - span, -0.02],
            [cx + span, cy + span, -0.02],
            [cx - span, cy + span, -0.02],
        ]
    )
    _mesh("ground", ground, np.array([[0, 1, 2], [0, 2, 3]]))

    image = bpy.data.images.new("ao", w, h, alpha=False)
    mat = bpy.data.materials.new("bake")
    mat.use_nodes = True
    node = mat.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = image
    mat.node_tree.nodes.active = node
    rec.data.materials.append(mat)

    bpy.ops.object.select_all(action="DESELECT")
    rec.select_set(True)
    bpy.context.view_layer.objects.active = rec
    bpy.ops.object.bake(type="AO", margin=4, use_clear=True)
    image.filepath_raw = dst
    image.file_format = "PNG"
    image.save()

    print("XPSB_WEATHER", n, flush=True)
    _bake_weather(scene, rec, mat, node, dw, dh, _extra(dst, "weather"))
    _bake_bevel(scene, rec, mat, node, dw, dh, _extra(dst, "bevel"))
    if high is None or not dst_normal:
        return
    node.image = image
    for link in list(mat.node_tree.links):
        mat.node_tree.links.remove(link)
    scene.cycles.samples = samples
    print("XPSB_RELIEF", n, flush=True)
    # High-to-low: the relief of the fine detail onto the walls behind it.
    normal = bpy.data.images.new("normal", dw, dh, alpha=False)
    normal.generated_color = (0.5, 0.5, 1.0, 1.0)  # flat where nothing is hit
    node.image = normal
    scene.render.bake.use_selected_to_active = True
    scene.render.bake.cage_extrusion = 0.35
    scene.render.bake.max_ray_distance = 0.5
    scene.render.bake.normal_space = "TANGENT"
    bpy.ops.object.select_all(action="DESELECT")
    high.select_set(True)
    rec.select_set(True)
    bpy.context.view_layer.objects.active = rec
    bpy.ops.object.bake(type="NORMAL", margin=4, use_clear=False, use_selected_to_active=True)
    scene.render.bake.use_selected_to_active = False
    normal.filepath_raw = dst_normal
    normal.file_format = "PNG"
    normal.save()


def main(list_file: str, cpu: bool = False) -> None:
    print("XPSB_START", bpy.app.version_string, flush=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.use_denoising = False
    scene.world = bpy.data.worlds.new("world")
    if cpu:
        scene.cycles.device = "CPU"
        print("XPSB_DEVICE CPU (graphics card search skipped)", flush=True)
    else:
        print("XPSB_DEVICE", _use_gpu(scene), flush=True)
    with open(list_file, encoding="utf-8") as f:
        jobs = [line.rstrip("\n").split("\t") for line in f if line.strip()]
    for n, job in enumerate(jobs):
        print("XPSB_BEGIN", n, flush=True)
        try:
            bake_one(scene, job[0], job[1], job[2] if len(job) > 2 else None, n)
        except Exception as exc:  # one broken object must not stop the others
            print("XPSB_FAILED", n, exc, flush=True)
        print("XPSB_DONE", n, flush=True)


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else sys.argv[1:]
    main(args[-1], cpu="--cpu" in args)
