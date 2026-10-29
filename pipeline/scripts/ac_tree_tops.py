"""Top views of the tree-card models, for the horizontal canopy cards of the trees near the road
(ac_trees.py: FULL trees get four vertical planes and canopy layers).

Run inside Blender (headless), after ac_tree_cards.py (same models, same settings):
    Blender -b --factory-startup --python scripts/ac_tree_tops.py

Each variant is rendered straight down (orthographic, +Y up in the image), clipped below CUT of its
height (the canopy layers float in the crown: no trunk base, no boulder), transparent film and
the white sky of ac_tree_cards.py, reduced 2 x 2. One sheet of 4096 x 4096 px with 1024 x 1024
cells:
  tree_tops.png      colour (sRGB) + coverage alpha, colour dilated under transparent pixels
  tree_tops_nm.png   tangent-space normals (R = +X, G = 0.5 - 0.5 * (+Y), B = up)
  tree_tops.json     per variant: cell, top_w_m (the card's side in metres at the model's size)
"""

import json
import os
import sys

import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ac_tree_cards as C                                   # noqa: E402

CELL = 1024
SHEET = 4096
CUT = 0.28
C.AXES["top"] = ((1, 0, 0), (0, 1, 0), (0, 0, 1))


def render_top(sc, cam, lo, hi, path):
    ctr = (lo + hi) / 2
    span = max(hi.x - lo.x, hi.y - lo.y) * 1.04
    cam.data.ortho_scale = span
    cam.location = (ctr.x, ctr.y, hi.z + 50)
    cam.rotation_euler = (0, 0, 0)                            # looking down, +Y up in the image
    cam.data.clip_end = 50 + (hi.z - lo.z) * (1 - CUT)        # nothing below CUT of the height: the boulder
                                                              # of the island_tree models, the trunk base
    sc.render.resolution_x = sc.render.resolution_y = CELL * C.SUPER
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    return span


def main():
    tmp = os.path.join(C.OUT, "renders_exr")
    os.makedirs(tmp, exist_ok=True)
    cols = SHEET // CELL
    meta, loaded = {}, None
    for k, (name, model, obj_name, kind) in enumerate(C.VARIANTS):
        if loaded != model:
            bpy.ops.wm.read_factory_settings(use_empty=True)
            sc, cam = C.setup_scene()
            gltf = [f for f in os.listdir(os.path.join(C.SRC, model)) if f.endswith(".gltf")][0]
            bpy.ops.import_scene.gltf(filepath=os.path.join(C.SRC, model, gltf))
            loaded = model
        for o in bpy.data.objects:
            if o.type == "MESH":
                o.hide_render = o.name != obj_name
        lo, hi = C.bbox(bpy.data.objects[obj_name])
        path = os.path.join(tmp, f"{name}_top.exr")
        span = render_top(sc, cam, lo, hi, path) if not os.path.exists(path) else max(hi.x - lo.x, hi.y - lo.y) * 1.04
        meta[name] = {"kind": kind, "cell": [k % cols, k // cols], "top_w_m": span, "height_m": hi.z - lo.z}
        print(f"tree top {name}: {meta[name]}", flush=True)
    sheet = np.zeros((SHEET, SHEET, 4), np.uint8)
    nsheet = np.zeros((SHEET, SHEET, 3), np.uint8)
    nsheet[:] = (128, 128, 255)
    for name, m in meta.items():
        rgba, nrm = C.read_exr(os.path.join(tmp, f"{name}_top.exr"))
        col, nm = C.reduce(rgba, nrm, "top")
        cx, cy = m["cell"]
        ys, xs = slice(cy * CELL, (cy + 1) * CELL), slice(cx * CELL, (cx + 1) * CELL)
        sheet[ys, xs] = C.dilate_rgb(col)
        nm[col[..., 3] < 8] = (128, 128, 255)
        nsheet[ys, xs] = nm
    C.save_png(sheet, os.path.join(C.OUT, "tree_tops.png"))
    C.save_png(nsheet, os.path.join(C.OUT, "tree_tops_nm.png"))
    with open(os.path.join(C.OUT, "tree_tops.json"), "w") as f:
        json.dump({"cell_px": CELL, "sheet_px": SHEET, "variants": meta}, f, indent=1)
    print("TREE TOPS DONE", flush=True)


if __name__ == "__main__":
    main()
