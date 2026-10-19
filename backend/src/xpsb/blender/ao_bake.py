"""Runs INSIDE Blender (blender --background --factory-startup --python ao_bake.py -- LIST).

A fixed, reviewed script that ships with the app - the AI never writes code for
Blender. It bakes ambient occlusion (the soft shadow in corners, under cornices,
inside window reveals and near the ground) of objects into images laid out like
their texture atlases. One Blender start for all objects of a package; the
graphics card is used when Cycles supports it (OptiX/CUDA, HIP, oneAPI, Metal).

LIST: a text file, one "IN.npz<TAB>AO.png<TAB>NORMAL.png" per line.
IN: positions (N, 3; metres, z up), uvs (N, 2; atlas coordinates, t up), receivers
(M, 3; triangles that own their piece of the atlas), occluders (K, 3; everything
else that casts shadow), size (2,), samples (), distance ().
Optional high_positions/high_tris: fine detail (window surrounds, dentils ...)
whose relief is baked onto the receivers as a tangent-space normal map
(high-to-low, "selected to active").
AO: greyscale PNG (white = open, dark = occluded). NORMAL: RGB PNG, flat
(128, 128, 255) where no detail was hit; only written when there is detail.
Prints "XPSB_DEVICE <kind> <name>" once and "XPSB_DONE <line number>" per object.
"""

import sys

import bpy
import numpy as np

GPU_KINDS = ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL")


def _use_gpu(scene) -> str:
    """Switches Cycles to the graphics card if there is a supported one."""
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except (KeyError, AttributeError):
        return "CPU"
    for kind in GPU_KINDS:
        try:
            prefs.compute_device_type = kind
            prefs.get_devices()
        except (TypeError, ValueError, AttributeError):
            continue
        gpus = [d for d in prefs.devices if d.type == kind]
        if gpus:
            for d in prefs.devices:
                d.use = d.type == kind
            scene.cycles.device = "GPU"
            return f"{kind} {gpus[0].name}"
    scene.cycles.device = "CPU"
    return "CPU"


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


def bake_one(scene, src: str, dst: str, dst_normal: str | None = None) -> None:
    with np.load(src) as d:
        positions = d["positions"].astype(np.float64)
        uvs, receivers, occluders = d["uvs"], d["receivers"], d["occluders"]
        w, h = (int(v) for v in d["size"])
        scene.cycles.samples = int(d["samples"])
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

    if high is None or not dst_normal:
        return
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


def main(list_file: str) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.use_denoising = False
    scene.world = bpy.data.worlds.new("world")
    print("XPSB_DEVICE", _use_gpu(scene), flush=True)
    with open(list_file, encoding="utf-8") as f:
        jobs = [line.rstrip("\n").split("\t") for line in f if line.strip()]
    for n, job in enumerate(jobs):
        try:
            bake_one(scene, job[0], job[1], job[2] if len(job) > 2 else None)
        except Exception as exc:  # one broken object must not stop the others
            print("XPSB_FAILED", n, exc, flush=True)
        print("XPSB_DONE", n, flush=True)


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else sys.argv[1:]
    main(args[0])
