"""Driver's-eye previews of the Assetto Corsa track, rendered in Blender from the parts export_ac
packs into the .kn5 (output/ac/<part>.npz + .json, textures as PNG), with AC's shaders emulated:

  ksMultilayer                   base x sum(mask x detail) x magicMult, details in world XZ
  ksPerPixelMultiMap_NMDetail    diffuse x lerp(detail, 1, diffuse alpha), normal + detail normal
  ksPerPixelNM / ksPerPixelAT_NM diffuse + normal map (alpha-tested)
  ksPerPixel / ksPerPixelAT      diffuse
Colour maths in gamma space like AC, then to linear for the renderer. No CSP 3D grass.

Run headless:
    Blender -b --factory-startup --python scripts/ac_preview.py -- NAME:ROUTE_M[:LATERAL[:LOOK_DEG]] ...
Output: output/ac/preview/<NAME>.png (1600 x 900).
"""

import json
import os
import sys

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AC = os.path.join(ROOT, "output", "ac")
TEX = os.path.join(AC, "textures")
OUT = os.path.join(AC, "preview")
PARTS = ("geometry", "scenery", "trees", "vines")
RADIUS = 450.0


def tex_path(f):
    return f if os.path.isabs(f) else os.path.join(TEX, f)


_IMG = {}


def image(f):
    p = tex_path(f)
    if p not in _IMG:
        im = bpy.data.images.load(p, check_existing=True)
        im.colorspace_settings.name = "Non-Color"
        _IMG[p] = im
    return _IMG[p]


def node_tex(nt, f, vector=None, x=0):
    n = nt.nodes.new("ShaderNodeTexImage")
    n.image = image(f)
    n.interpolation = "Linear"
    n.location = (x, 0)
    if vector is not None:
        nt.links.new(vector, n.inputs["Vector"])
    return n


def math(nt, op, a, b=None):
    n = nt.nodes.new("ShaderNodeVectorMath" if op.startswith("v_") else "ShaderNodeMath")
    n.operation = op[2:] if op.startswith("v_") else op
    for k, v in enumerate((a, b)):
        if v is None:
            continue
        if isinstance(v, (int, float)):
            if op.startswith("v_"):
                n.inputs[k].default_value = (v, v, v)
            else:
                n.inputs[k].default_value = v
        elif isinstance(v, tuple):
            n.inputs[k].default_value = v
        else:
            nt.links.new(v, n.inputs[k])
    return n.outputs[0] if not op.startswith("v_") else n.outputs["Vector"]


def to_linear(nt, col):
    g = nt.nodes.new("ShaderNodeGamma")
    g.inputs["Gamma"].default_value = 2.2
    nt.links.new(col, g.inputs["Color"])
    return g.outputs["Color"]


def normal_from(nt, colour_out, flip_green):
    """AC-encoded tangent normal -> Blender (OpenGL, v up) normal map node output."""
    col = colour_out
    if flip_green:
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        nt.links.new(col, sep.inputs["Color"])
        inv = math(nt, "SUBTRACT", 1.0, sep.outputs["Green"])
        com = nt.nodes.new("ShaderNodeCombineColor")
        nt.links.new(sep.outputs["Red"], com.inputs["Red"])
        nt.links.new(inv, com.inputs["Green"])
        nt.links.new(sep.outputs["Blue"], com.inputs["Blue"])
        col = com.outputs["Color"]
    nm = nt.nodes.new("ShaderNodeNormalMap")
    nm.space = "TANGENT"
    nm.uv_map = "UVMap"
    nt.links.new(col, nm.inputs["Color"])
    return nm.outputs["Normal"]


def make_material(name, spec):
    mat = bpy.data.materials.new(name)
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    shader = spec["shader"]
    props = spec.get("props", {})
    tex = {}
    for t in spec["textures"]:
        if isinstance(t, dict):
            tex[t["sampler"]] = t["file"]
        else:
            tex[t[0]] = t[1]
    uv = nt.nodes.new("ShaderNodeUVMap")
    uv.uv_map = "UVMap"
    bsdf.inputs["Roughness"].default_value = 0.85
    bsdf.inputs["Specular IOR Level"].default_value = 0.3
    flip = name != "road"                           # the road's B points down the image (see ac_textures)
    if shader == "ksMultilayer":
        base = node_tex(nt, tex["txDiffuse"], uv.outputs["UV"])
        mask = node_tex(nt, tex["txMask"], uv.outputs["UV"])
        coord = nt.nodes.new("ShaderNodeTexCoord")
        sep_m = nt.nodes.new("ShaderNodeSeparateColor")
        nt.links.new(mask.outputs["Color"], sep_m.inputs["Color"])
        chans = [sep_m.outputs["Red"], sep_m.outputs["Green"], sep_m.outputs["Blue"], mask.outputs["Alpha"]]
        acc = None
        for k, c in enumerate("RGBA"):
            mult = props.get(f"mult{c}", 1.0)
            mult = mult[0] if isinstance(mult, (list, tuple)) else mult
            v = math(nt, "v_MULTIPLY", coord.outputs["Object"], (mult, mult, 1.0))
            d = node_tex(nt, tex[f"txDetail{c}"], v)
            sc = nt.nodes.new("ShaderNodeVectorMath")
            sc.operation = "SCALE"
            nt.links.new(d.outputs["Color"], sc.inputs[0])
            nt.links.new(chans[k], sc.inputs["Scale"])
            acc = sc.outputs["Vector"] if acc is None else math(nt, "v_ADD", acc, sc.outputs["Vector"])
        col = math(nt, "v_MULTIPLY", base.outputs["Color"], acc)
        sc = nt.nodes.new("ShaderNodeVectorMath")
        sc.operation = "SCALE"
        nt.links.new(col, sc.inputs[0])
        sc.inputs["Scale"].default_value = props.get("magicMult", 1.0)
        nt.links.new(to_linear(nt, sc.outputs["Vector"]), bsdf.inputs["Base Color"])
        bsdf.inputs["Roughness"].default_value = 0.95
    elif shader.startswith("ksPerPixelMultiMap"):
        dif = node_tex(nt, tex["txDiffuse"], uv.outputs["UV"])
        col = dif.outputs["Color"]
        if "txDetail" in tex and props.get("useDetail", 0) > 0:
            dm = props.get("detailUVMultiplier", 1.0)
            v = math(nt, "v_MULTIPLY", uv.outputs["UV"], (dm, dm, 1.0))
            det = node_tex(nt, tex["txDetail"], v)
            mix = nt.nodes.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            nt.links.new(dif.outputs["Alpha"], mix.inputs["Factor"])
            nt.links.new(det.outputs["Color"], mix.inputs[6])
            mix.inputs[7].default_value = (1, 1, 1, 1)
            col = math(nt, "v_MULTIPLY", col, mix.outputs[2])
        nt.links.new(to_linear(nt, col), bsdf.inputs["Base Color"])
        if "txNormal" in tex:
            n_main = node_tex(nt, tex["txNormal"], uv.outputs["UV"])
            nrm = normal_from(nt, n_main.outputs["Color"], flip)
            nt.links.new(nrm, bsdf.inputs["Normal"])
        if "txMaps" in tex:
            mp = node_tex(nt, tex["txMaps"], uv.outputs["UV"])
            sep = nt.nodes.new("ShaderNodeSeparateColor")
            nt.links.new(mp.outputs["Color"], sep.inputs["Color"])
            r = math(nt, "SUBTRACT", 0.95, math(nt, "MULTIPLY", sep.outputs["Green"], 0.6))
            nt.links.new(r, bsdf.inputs["Roughness"])
    else:
        dif = node_tex(nt, tex["txDiffuse"], uv.outputs["UV"])
        nt.links.new(to_linear(nt, dif.outputs["Color"]), bsdf.inputs["Base Color"])
        if "txNormal" in tex and not (os.environ.get("PREVIEW_NO_TREE_NM") and shader == "ksPerPixelAT_NM"):
            n_main = node_tex(nt, tex["txNormal"], uv.outputs["UV"])
            nt.links.new(normal_from(nt, n_main.outputs["Color"], flip), bsdf.inputs["Normal"])
        if shader.startswith("ksPerPixelAT"):
            bsdf.inputs["Specular IOR Level"].default_value = 0.0          # AC's tree / alpha cards: ~no specular
        if spec.get("alpha_tested"):
            ref = props.get("ksAlphaRef", 0.5)
            a = math(nt, "GREATER_THAN", dif.outputs["Alpha"], ref)
            nt.links.new(a, bsdf.inputs["Alpha"])
            mat.surface_render_method = "DITHERED"
    return mat


def load(cam_xy):
    mats = {}
    count = 0
    for part in PARTS:
        meta = json.load(open(os.path.join(AC, f"{part}.json")))
        G = np.load(os.path.join(AC, f"{part}.npz"))
        for k, m in enumerate(meta["meshes"]):
            if m.get("renderable") is False:
                continue
            pos = G[f"m{k}_pos"]
            lo, hi = pos[:, :2].min(0), pos[:, :2].max(0)
            if np.any(cam_xy < lo - RADIUS) or np.any(cam_xy > hi + RADIUS):
                continue
            tri = G[f"m{k}_tri"]
            cen = pos[tri].mean(1)
            keep = np.linalg.norm(cen[:, :2] - cam_xy, axis=1) < RADIUS
            if not keep.any():
                continue
            tri = tri[keep]
            used, inv = np.unique(tri.ravel(), return_inverse=True)
            P, N, U = pos[used], G[f"m{k}_nrm"][used], G[f"m{k}_uv"][used]
            T = inv.reshape(-1, 3)
            name = m["material"]
            if name not in mats:
                mats[name] = make_material(name, meta["materials"][name])
            me = bpy.data.meshes.new(m["name"])
            me.vertices.add(len(P))
            me.vertices.foreach_set("co", P.astype(np.float32).ravel())
            me.loops.add(len(T) * 3)
            me.loops.foreach_set("vertex_index", T.astype(np.int32).ravel())
            me.polygons.add(len(T))
            me.polygons.foreach_set("loop_start", np.arange(0, len(T) * 3, 3, dtype=np.int32))
            me.polygons.foreach_set("loop_total", np.full(len(T), 3, np.int32))
            uvl = me.uv_layers.new(name="UVMap")
            luv = U[T.ravel()].astype(np.float32).copy()
            luv[:, 1] = 1.0 - luv[:, 1]
            uvl.data.foreach_set("uv", luv.ravel())
            me.update()
            me.polygons.foreach_set("use_smooth", np.ones(len(T), bool))
            me.normals_split_custom_set_from_vertices(N.astype(np.float32).tolist())
            me.materials.append(mats[name])
            ob = bpy.data.objects.new(m["name"], me)
            bpy.context.scene.collection.objects.link(ob)
            count += len(T)
    print(f"loaded {count:,} triangles, {len(mats)} materials", flush=True)


def setup(sc):
    for eng in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
        try:
            sc.render.engine = eng
            break
        except TypeError:
            continue
    sc.render.resolution_x, sc.render.resolution_y = 1600, 900
    sc.view_settings.view_transform = "AgX"
    sc.view_settings.look = "AgX - Base Contrast"
    world = bpy.data.worlds.new("sky")
    sc.world = world
    world.use_nodes = True
    nt = world.node_tree
    sky = nt.nodes.new("ShaderNodeTexSky")
    try:
        sky.sky_type = "NISHITA"
    except TypeError:
        pass
    for attr, val in (("sun_elevation", np.radians(38)), ("sun_rotation", np.radians(215))):
        if hasattr(sky, attr):
            setattr(sky, attr, val)
    nt.links.new(sky.outputs["Color"], nt.nodes["Background"].inputs["Color"])
    nt.nodes["Background"].inputs["Strength"].default_value = 0.35
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 3.2
    sun.data.angle = np.radians(1.0)
    sun.rotation_euler = (np.radians(52), 0, np.radians(215 + 90))
    sc.collection.objects.link(sun)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.data.lens = 22
    cam.data.clip_end = 3000
    sc.collection.objects.link(cam)
    sc.camera = cam
    return cam


def main():
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else ["start:1810"]
    geo = json.load(open(os.path.join(AC, "geometry.json")))
    cl = np.asarray(geo["centreline"])                      # every 10th station (5 m)
    route = json.load(open(os.path.join(AC, "preview_route.json")))   # the same stations: s, route m, z
    s = np.asarray(route["s"])
    os.makedirs(OUT, exist_ok=True)
    for a in args:
        f = a.split(":")
        name, rm = f[0], float(f[1])
        lat = float(f[2]) if len(f) > 2 else -0.9
        look = float(f[3]) if len(f) > 3 else 0.0
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _IMG.clear()
        sc = bpy.context.scene
        cam = setup(sc)
        st = float(np.interp(rm, route["route_m"], route["s"]))
        p = np.array([np.interp(st, s, cl[:, 0]), np.interp(st, s, cl[:, 1])])
        q = np.array([np.interp(st + 2, s, cl[:, 0]), np.interp(st + 2, s, cl[:, 1])])
        t = (q - p) / np.linalg.norm(q - p)
        n = np.array([-t[1], t[0]])
        z = float(np.interp(st, route["s"], route["z"]))
        eye = p + n * lat
        load(eye)
        ahead = float(np.interp(st + 22, route["s"], route["z"]))
        tgt = np.array([np.interp(st + 22, s, cl[:, 0]), np.interp(st + 22, s, cl[:, 1])]) + n * lat * 0.5
        d = np.array([*(tgt - eye), (ahead + 0.6) - (z + 1.15)])
        ang = np.radians(look)
        d[:2] = [d[0] * np.cos(ang) - d[1] * np.sin(ang), d[0] * np.sin(ang) + d[1] * np.cos(ang)]
        cam.location = (eye[0], eye[1], z + 1.15)
        from mathutils import Vector
        cam.rotation_euler = Vector(d).to_track_quat("-Z", "Y").to_euler()
        sc.render.filepath = os.path.join(OUT, f"{name}.png")
        bpy.ops.render.render(write_still=True)
        print(f"PREVIEW {name} route {rm} m -> {sc.render.filepath}", flush=True)


if __name__ == "__main__":
    main()
