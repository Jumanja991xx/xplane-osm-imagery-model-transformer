"""Runs INSIDE Blender (fixed script shipped with the app, never code from the AI):
a photo-realistic picture of the built scenery with Cycles.

    blender -b --factory-startup --python photo.py -- manifest.json

The manifest names the objects (the preview's mesh JSON, colour texture, normal
map, place and heading), the ground, camera, sun, size and samples. The objects
keep the textures the app baked (with Blender: soft shading, weathering, relief),
lit by a physical sky and sun; the output is one PNG.
"""

from __future__ import annotations

import contextlib
import json
import math
import sys

import bpy


def _args() -> dict:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    with open(argv[0], encoding="utf-8") as f:
        return json.load(f)


def _clear() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _image(path: str | None, color: bool):
    if not path:
        return None
    img = bpy.data.images.load(path, check_existing=True)
    img.colorspace_settings.name = "sRGB" if color else "Non-Color"
    return img


def _material(name: str, tex: str | None, nml: str | None, alpha: bool):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    bsdf.inputs["Roughness"].default_value = 0.8
    color = _image(tex, True)
    if color is not None:
        node = nodes.new("ShaderNodeTexImage")
        node.image = color
        node.interpolation = "Cubic"
        links.new(node.outputs["Color"], bsdf.inputs["Base Color"])
        if alpha:
            links.new(node.outputs["Alpha"], bsdf.inputs["Alpha"])
            mat.blend_method = "HASHED"
    normal = _image(nml, False)
    if normal is not None:
        node = nodes.new("ShaderNodeTexImage")
        node.image = normal
        nmap = nodes.new("ShaderNodeNormalMap")
        nmap.inputs["Strength"].default_value = 0.9
        links.new(node.outputs["Color"], nmap.inputs["Color"])
        links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
        # the normal map's alpha is the gloss (X-Plane): glossy spots are smoother
        rough = nodes.new("ShaderNodeMapRange")
        rough.inputs["To Min"].default_value = 0.85
        rough.inputs["To Max"].default_value = 0.35
        links.new(node.outputs["Alpha"], rough.inputs["Value"])
        links.new(rough.outputs["Result"], bsdf.inputs["Roughness"])
    return mat


def _object(o: dict, n: int) -> None:
    with open(o["mesh"], encoding="utf-8") as f:
        m = json.load(f)
    pos, uvs, idx = m["positions"], m["uvs"], m["index"]
    verts = [(pos[i], pos[i + 1], pos[i + 2]) for i in range(0, len(pos), 3)]
    faces = [(idx[i], idx[i + 1], idx[i + 2]) for i in range(0, len(idx), 3)]
    if not faces:
        return
    mesh = bpy.data.meshes.new(f"obj{n}")
    mesh.from_pydata(verts, [], faces)
    uv = mesh.uv_layers.new(name="UVMap")
    for loop in mesh.loops:
        v = loop.vertex_index
        uv.data[loop.index].uv = (uvs[2 * v], uvs[2 * v + 1])
    nrm = m.get("normals")
    if nrm:
        normals = [(nrm[i], nrm[i + 1], nrm[i + 2]) for i in range(0, len(nrm), 3)]
        if hasattr(mesh, "use_auto_smooth"):  # Blender < 4.1
            mesh.use_auto_smooth = True
        mesh.normals_split_custom_set_from_vertices(normals)
    mat = _material(f"mat{n}", o.get("texture"), o.get("normal"), m.get("alpha", False))
    mesh.materials.append(mat)
    ob = bpy.data.objects.new(f"obj{n}", mesh)
    ob.location = (o["x"], o["y"], 0.0)
    ob.rotation_euler = (0.0, 0.0, -math.radians(o.get("heading", 0.0)))
    bpy.context.scene.collection.objects.link(ob)


def _ground(g: dict) -> None:
    size = g.get("size", 3000.0)
    bpy.ops.mesh.primitive_plane_add(size=size, location=(g.get("x", 0.0), g.get("y", 0.0), -0.05))
    plane = bpy.context.active_object
    mat = bpy.data.materials.new("ground")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*g.get("color", (0.15, 0.15, 0.11)), 1.0)
    bsdf.inputs["Roughness"].default_value = 1.0
    plane.data.materials.append(mat)


def _sky(sun: dict) -> None:
    scene = bpy.context.scene
    world = bpy.data.worlds.new("sky")
    scene.world = world
    world.use_nodes = True
    nodes, links = world.node_tree.nodes, world.node_tree.links
    bg = nodes.get("Background")
    sky = nodes.new("ShaderNodeTexSky")
    elev, azim = math.radians(sun["elevation"]), math.radians(sun["azimuth"])
    try:
        sky.sky_type = "NISHITA"
        sky.sun_elevation = elev
        sky.sun_rotation = math.radians(90.0) - azim  # Blender: from +x, counter-clockwise
        sky.sun_disc = False  # the sun lamp below gives the sharp sun and shadows
    except (AttributeError, TypeError):
        sky.sky_type = "HOSEK_WILKIE"
    links.new(sky.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = 0.35
    light = bpy.data.lights.new("sun", "SUN")
    light.energy = 4.0
    light.angle = math.radians(0.6)
    ob = bpy.data.objects.new("sun", light)
    # the lamp shines along its -z: point it from the sun's direction
    ob.rotation_euler = (math.radians(90.0) - elev, 0.0, -azim + math.pi)
    scene.collection.objects.link(ob)


def _camera(c: dict) -> None:
    scene = bpy.context.scene
    cam = bpy.data.cameras.new("camera")
    cam.lens = c.get("lens", 35.0)
    cam.clip_end = 20000.0
    ob = bpy.data.objects.new("camera", cam)
    tx, ty, tz = c["target"]
    az, el, d = math.radians(c["azimuth"]), math.radians(c["elevation"]), c["distance"]
    # azimuth: where the camera stands, seen from the target (degrees from north)
    ob.location = (
        tx + d * math.cos(el) * math.sin(az),
        ty + d * math.cos(el) * math.cos(az),
        tz + d * math.sin(el),
    )
    direction = (tx - ob.location[0], ty - ob.location[1], tz - ob.location[2])
    from mathutils import Vector

    ob.rotation_euler = Vector(direction).to_track_quat("-Z", "Y").to_euler()
    scene.collection.objects.link(ob)
    scene.camera = ob


def main() -> None:
    a = _args()
    _clear()
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    samples = int(a.get("samples", 64))
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True
    with contextlib.suppress(TypeError):
        scene.cycles.denoiser = "OPENIMAGEDENOISE"
    w, h = a.get("size", [1600, 1000])
    scene.render.resolution_x, scene.render.resolution_y = int(w), int(h)
    scene.render.resolution_percentage = 100
    for transform in ("AgX", "Filmic"):
        try:
            scene.view_settings.view_transform = transform
            break
        except TypeError:
            continue
    scene.view_settings.look = "None"
    for n, o in enumerate(a["objects"]):
        _object(o, n)
    _ground(a.get("ground") or {})
    _sky(a["sun"])
    _camera(a["camera"])
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = a["out"]
    print("XPSB_RENDER_START", flush=True)
    try:
        bpy.ops.render.render(write_still=True)
    except RuntimeError as exc:
        if "Denois" not in str(exc):
            raise
        # some Blender builds (Linux distributions) come without the denoiser:
        # render without it and with more samples instead
        print("XPSB_NO_DENOISER", flush=True)
        scene.cycles.use_denoising = False
        scene.cycles.samples = samples * 4
        bpy.ops.render.render(write_still=True)
    print("XPSB_RENDER_DONE", flush=True)


main()
