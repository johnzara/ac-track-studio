"""Tree-card images for the Assetto Corsa track, rendered in Blender from CC0 Poly Haven models.

Run inside Blender (headless):
    Blender -b --factory-startup --python scripts/ac_tree_cards.py

Each variant (one tree of a model) is rendered from the front (-Y) and the side (+X) with an
orthographic camera, transparent film and a uniform white sky, so the colour is the leaves' colour
times their self-shadowing and AC does the lighting. Every view is rendered at twice the cell size
into a multilayer EXR (colour + normal pass) and reduced 2 x 2 (alpha-weighted).
Two sheets of 8192 x 8192 px with 1024 x 2048 cells (two cells per variant):
  tree_cards.png      colour (sRGB) + coverage alpha; the colour under transparent pixels is filled
                      from the nearest leaves so mipmaps do not get dark fringes
  tree_cards_nm.png   tangent-space normals as AC decodes them (ksPerPixelAT_NM): R = towards the
                      viewer's right, G = 0.5 - 0.5 * up, B = towards the viewer; flat where empty
Output: output/ac/trees/tree_cards.png, tree_cards_nm.png and tree_cards.json (cell, size in metres
per variant).
"""

import json
import os

import bpy
import numpy as np
import OpenImageIO as oiio
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "ac_assets", "polyhaven")
OUT = os.path.join(ROOT, "output", "ac", "trees")
CELL = (1024, 2048)
SHEET = 8192
SUPER = 2                     # render at SUPER x the cell size
TONE = 0.86                   # linear brightness: matches the v0.5 sheet's mean colour
VARIANTS = [   # (variant name, model, mesh object, kind)
    ("fir_a", "fir_tree_01", "fir_tree_01_a_LOD0", "conifer"),
    ("fir_b", "fir_tree_01", "fir_tree_01_b_LOD0", "conifer"),
    ("fir_c", "fir_tree_01", "fir_tree_01_c_LOD0", "conifer"),
    ("pine_a", "pine_tree_01", "pine_tree_01_a_LOD0", "pine"),
    ("pine_b", "pine_tree_01", "pine_tree_01_b_LOD0", "pine"),
    ("pine_c", "pine_tree_01", "pine_tree_01_c_LOD0", "pine"),
    ("broad_a", "island_tree_01", "island_tree_01_LOD0", "broadleaf"),
    ("broad_b", "island_tree_02", "island_tree_02_LOD0", "broadleaf"),
    ("broad_c", "island_tree_03", "island_tree_03_LOD0", "broadleaf"),
    ("broad_d", "tree_small_02", "tree_small_02_LOD0", "broadleaf"),
    ("shrub_a", "shrub_02", "shrub_02_a", "shrub"),
    ("shrub_b", "shrub_02", "shrub_02_b", "shrub"),
    ("shrub_c", "shrub_02", "shrub_02_c", "shrub"),
    ("shrub_d", "shrub_02", "shrub_02_d", "shrub"),
]
# camera axes per view in world space: (right, up, towards the viewer)
AXES = {"front": ((1, 0, 0), (0, 0, 1), (0, -1, 0)), "side": ((0, 1, 0), (0, 0, 1), (1, 0, 0))}


def setup_scene():
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for dev_type in ("METAL", "NONE"):
        try:
            prefs.compute_device_type = dev_type
            prefs.get_devices()
            for d in prefs.devices:
                d.use = True
            sc.cycles.device = "GPU" if dev_type != "NONE" else "CPU"
            break
        except TypeError:
            continue
    sc.cycles.samples = 96
    sc.cycles.use_denoising = True
    sc.render.film_transparent = True
    sc.view_settings.view_transform = "Standard"
    sc.view_settings.look = "None"
    sc.render.image_settings.media_type = "MULTI_LAYER_IMAGE"
    sc.render.image_settings.file_format = "OPEN_EXR_MULTILAYER"
    sc.render.image_settings.color_depth = "32"
    sc.render.image_settings.exr_codec = "ZIP"
    sc.view_layers[0].use_pass_normal = True
    world = bpy.data.worlds.new("sky")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (1, 1, 1, 1)
    bg.inputs["Strength"].default_value = 1.0
    sc.world = world
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.data.type = "ORTHO"
    sc.collection.objects.link(cam)
    sc.camera = cam
    return sc, cam


def bbox(obj):
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


def card_span(lo, hi):
    """Card width in metres (same for both views, so the crossed cards match)."""
    return max(hi.x - lo.x, hi.y - lo.y, (hi.z - lo.z) * CELL[0] / CELL[1]) * 1.04


def render_view(sc, cam, lo, hi, view, path):
    """Orthographic view of the box lo..hi from the front (-Y) or the side (+X), base at the
    bottom edge, centred horizontally."""
    ctr = (lo + hi) / 2
    h = hi.z - lo.z
    span_w = card_span(lo, hi)
    span_h = span_w * CELL[1] / CELL[0]
    cam.data.ortho_scale = max(span_w, span_h)
    zc = lo.z + span_h / 2 - 0.02 * h                              # tree base on the bottom edge
    if view == "front":
        cam.location = (ctr.x, lo.y - 50, zc)
        cam.rotation_euler = (np.pi / 2, 0, 0)
    else:
        cam.location = (hi.x + 50, ctr.y, zc)
        cam.rotation_euler = (np.pi / 2, 0, np.pi / 2)
    cam.data.clip_end = 200
    sc.render.resolution_x, sc.render.resolution_y = CELL[0] * SUPER, CELL[1] * SUPER
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)


def read_exr(path):
    """(rgba premultiplied linear, normal world) as float arrays, row 0 at the top."""
    inp = oiio.ImageInput.open(path)
    out = {}
    try:
        for sub in range(64):
            if not inp.seek_subimage(sub, 0):
                break
            spec = inp.spec()
            px = inp.read_image(sub, 0, 0, spec.nchannels, oiio.FLOAT)
            for k, name in enumerate(spec.channelnames):
                out[name] = px[..., k]
    finally:
        inp.close()

    def chan(prefix, comps):
        keys = {k.split(".")[-1]: k for k in out if k.split(".")[-2:-1] == [prefix] or k.startswith(prefix + ".")}
        return np.stack([out[keys[c]] for c in comps], -1)
    return chan("Combined", "RGBA"), chan("Normal", "XYZ")


def to_srgb(lin):
    lin = np.clip(lin, 0, 1)
    return np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * np.power(lin, 1 / 2.4) - 0.055)


def reduce(rgba, nrm, view):
    """SUPER x SUPER reduction: straight sRGB colour + coverage, and tangent-space normal RGB."""
    h, w = rgba.shape[0] // SUPER, rgba.shape[1] // SUPER
    q = rgba.reshape(h, SUPER, w, SUPER, 4)
    a = q[..., 3].sum((1, 3))
    rgb = q[..., :3].sum((1, 3)) / np.maximum(a, 1e-6)[..., None]      # premultiplied -> straight
    rgb *= TONE      # averaging in linear light leaves the leaf edges paler than v0.5's sRGB-averaged cards
    alpha = a / SUPER ** 2
    right, up, toward = (np.array(v, np.float32) for v in AXES[view])
    n = nrm.reshape(h, SUPER, w, SUPER, 3).sum((1, 3))
    t = np.stack([n @ right, n @ up, n @ toward], -1)
    ln = np.linalg.norm(t, axis=-1, keepdims=True)
    t = np.where(ln > 1e-6, t / np.maximum(ln, 1e-6), np.array([0, 0, 1], np.float32))
    t[..., 2] = np.abs(t[..., 2])                                   # leaves seen from behind: towards the viewer
    t /= np.linalg.norm(t, axis=-1, keepdims=True)
    nm = np.stack([(t[..., 0] + 1) / 2, (1 - t[..., 1]) / 2, (t[..., 2] + 1) / 2], -1)
    colour = np.concatenate([to_srgb(rgb), alpha[..., None]], -1)
    return (colour * 255).round().clip(0, 255).astype(np.uint8), (nm * 255).round().clip(0, 255).astype(np.uint8)


def dilate_rgb(rgba, iters=48):
    """Fill RGB under transparent pixels from opaque neighbours (avoids dark mip fringes)."""
    rgb = rgba[..., :3].astype(np.float32)
    known = rgba[..., 3] > 8
    acc = rgb * known[..., None]
    for _ in range(iters):
        if known.all():
            break
        s = np.zeros_like(acc)
        n = np.zeros(known.shape, np.float32)
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            s += np.roll(acc, (dy, dx), (0, 1))
            n += np.roll(known, (dy, dx), (0, 1))
        grow = (~known) & (n > 0)
        acc[grow] = s[grow] / n[grow, None]
        known = known | grow
    rgb[~(rgba[..., 3] > 8)] = acc[~(rgba[..., 3] > 8)]
    return np.concatenate([rgb, rgba[..., 3:4].astype(np.float32)], -1).clip(0, 255).astype(np.uint8)


def save_png(arr, path):
    h, w, c = arr.shape
    out = oiio.ImageOutput.create(path)
    out.open(path, oiio.ImageSpec(w, h, c, oiio.UINT8))
    out.write_image(arr)
    out.close()


def main():
    os.makedirs(OUT, exist_ok=True)
    tmp = os.path.join(OUT, "renders_exr")
    os.makedirs(tmp, exist_ok=True)
    meta = {}
    cols = SHEET // CELL[0]
    loaded = None
    for k, (name, model, obj_name, kind) in enumerate(VARIANTS):
        if loaded != model:
            bpy.ops.wm.read_factory_settings(use_empty=True)
            sc, cam = setup_scene()
            gltf = [f for f in os.listdir(os.path.join(SRC, model)) if f.endswith(".gltf")][0]
            bpy.ops.import_scene.gltf(filepath=os.path.join(SRC, model, gltf))
            loaded = model
        for o in bpy.data.objects:
            if o.type == "MESH":
                o.hide_render = o.name != obj_name
        obj = bpy.data.objects[obj_name]
        lo, hi = bbox(obj)
        cells = []
        for v, view in enumerate(("front", "side")):
            path = os.path.join(tmp, f"{name}_{view}.exr")
            if not os.path.exists(path):
                render_view(sc, cam, lo, hi, view, path)
            cell = 2 * k + v
            cells.append([cell % cols, cell // cols])
        span_w = card_span(lo, hi)
        meta[name] = {"kind": kind, "cells": cells, "height_m": hi.z - lo.z,
                      "card_w_m": span_w, "card_h_m": span_w * CELL[1] / CELL[0],
                      "crown_w_m": max(hi.x - lo.x, hi.y - lo.y)}
        print(f"tree card {name}: {meta[name]}", flush=True)
    sheet = np.zeros((SHEET, SHEET, 4), np.uint8)
    nsheet = np.zeros((SHEET, SHEET, 3), np.uint8)
    nsheet[:] = (128, 128, 255)
    for name, m in meta.items():
        for (cx, cy), view in zip(m["cells"], ("front", "side")):
            rgba, nrm = read_exr(os.path.join(tmp, f"{name}_{view}.exr"))
            col, nm = reduce(rgba, nrm, view)
            ys, xs = slice(cy * CELL[1], (cy + 1) * CELL[1]), slice(cx * CELL[0], (cx + 1) * CELL[0])
            sheet[ys, xs] = dilate_rgb(col)
            flat = col[..., 3] < 8
            nm[flat] = (128, 128, 255)
            nsheet[ys, xs] = nm
    save_png(sheet, os.path.join(OUT, "tree_cards.png"))
    save_png(nsheet, os.path.join(OUT, "tree_cards_nm.png"))
    with open(os.path.join(OUT, "tree_cards.json"), "w") as f:
        json.dump({"cell_px": CELL, "sheet_px": SHEET, "variants": meta}, f, indent=1)
    print("TREE CARDS DONE", flush=True)


if __name__ == "__main__":
    main()
