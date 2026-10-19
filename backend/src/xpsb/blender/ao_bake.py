"""Runs INSIDE Blender (blender --background --factory-startup --python ao_bake.py -- IN OUT).

A fixed, reviewed script that ships with the app - the AI never writes code for
Blender. It bakes ambient occlusion (the soft shadow in corners, under cornices,
inside window reveals and near the ground) of an object into an image laid out
like the object's texture atlas.

IN: .npz with positions (N, 3; metres, z up), uvs (N, 2; atlas coordinates, t up),
receivers (M, 3; triangles that own their piece of the atlas), occluders (K, 3;
everything else that casts shadow), size (2,), samples (), distance ().
OUT: greyscale PNG (white = open, dark = occluded).
"""

import sys

import bpy
import numpy as np


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


def bake(src: str, dst: str) -> None:
    d = np.load(src)
    positions = d["positions"].astype(np.float64)
    w, h = (int(v) for v in d["size"])
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = int(d["samples"])
    scene.cycles.use_denoising = False
    world = bpy.data.worlds.new("world")
    world.light_settings.distance = float(d["distance"])
    scene.world = world

    receivers = _mesh("receivers", positions, d["receivers"], d["uvs"])
    if len(d["occluders"]):
        _mesh("occluders", positions, d["occluders"])
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
    receivers.data.materials.append(mat)

    bpy.ops.object.select_all(action="DESELECT")
    receivers.select_set(True)
    bpy.context.view_layer.objects.active = receivers
    bpy.ops.object.bake(type="AO", margin=4, use_clear=True)
    image.filepath_raw = dst
    image.file_format = "PNG"
    image.save()


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else sys.argv[1:]
    bake(args[0], args[1])
