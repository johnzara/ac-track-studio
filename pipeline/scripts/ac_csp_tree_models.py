"""Low-polygon 3D trees for Custom Shaders Patch, from the CC0 Poly Haven models (the same ones the
tree cards are rendered from). Run inside Blender (headless):

    Blender -b --factory-startup --python scripts/ac_csp_tree_models.py

The Poly Haven trees are millions of triangles with every leaf modelled. For each variant:
  * the wood (trunk, branches) is decimated to WOOD_TRIS triangles per level of detail;
  * the foliage is grouped into clusters (k-means on the leaf triangles); each cluster becomes two
    crossed square cards, textured with a picture of a real cluster of that tree: TEMPLATES of
    them are rendered here (orthographic, flat light, transparent background) into one atlas.
ac_csp_trees.py (ordinary Python) turns the result into CSP's .bin tree files.
Output: output/ac/csp_trees/<variant>.npz, <variant>_leaves.png, models.json.
"""

import json
import os
import sys

import bpy
import numpy as np
from mathutils import Matrix, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "ac_assets", "polyhaven")
OUT = os.path.join(ROOT, "output", "ac", "csp_trees")
VARIANTS = [   # (variant, model, mesh object, foliage material keywords)
    ("broad_d", "tree_small_02", "tree_small_02_LOD0", ("leaves",)),
    ("broad_a", "island_tree_01", "island_tree_01_LOD0", ("leaves",)),
    ("broad_b", "island_tree_02", "island_tree_02_LOD0", ("leaves",)),
    ("broad_c", "island_tree_03", "island_tree_03_LOD0", ("leaves",)),
    ("fir_c", "fir_tree_01", "fir_tree_01_c_LOD0", ("twig",)),
]
WOOD_TRIS = (2600, 1300, 600)          # per level of detail
WOOD_KEEP = (0.06, 0.2, 0.3)           # wood pieces smaller than this share of the tree's height are left out
CLUSTERS = (520, 260, 110)
TEMPLATES, CELL = 8, 256               # atlas: 4 x 2 cells
CARD_STD = 2.1                         # card half-size in standard deviations of its cluster
SAMPLE = 120_000
rng = np.random.default_rng(7)


def mesh_arrays(obj):
    """World-space triangles of a mesh object: positions, per-corner uv, material index."""
    me = obj.data
    me.calc_loop_triangles()
    nv, nt = len(me.vertices), len(me.loop_triangles)
    co = np.empty(nv * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    M = np.array(obj.matrix_world, np.float64)
    co = (co @ M[:3, :3].T + M[:3, 3]).astype(np.float32)
    tv = np.empty(nt * 3, np.int32)
    me.loop_triangles.foreach_get("vertices", tv)
    tl = np.empty(nt * 3, np.int32)
    me.loop_triangles.foreach_get("loops", tl)
    tm = np.empty(nt, np.int32)
    me.loop_triangles.foreach_get("material_index", tm)
    uv = np.zeros((len(me.loops), 2), np.float32)
    if me.uv_layers.active:
        flat = np.empty(len(me.loops) * 2, np.float32)
        me.uv_layers.active.data.foreach_get("uv", flat)
        uv = flat.reshape(-1, 2)
    return co, tv.reshape(-1, 3), uv[tl.reshape(-1, 3)], tm


def new_mesh(name, co, tri, uv, material=None):
    """A mesh object from unwelded-uv triangles (uv: n x 3 x 2)."""
    me = bpy.data.meshes.new(name)
    used, inv = np.unique(tri, return_inverse=True)
    me.vertices.add(len(used))
    me.vertices.foreach_set("co", co[used].astype(np.float32).ravel())
    n = len(tri)
    me.loops.add(n * 3)
    me.loops.foreach_set("vertex_index", inv.astype(np.int32).ravel())
    me.polygons.add(n)
    me.polygons.foreach_set("loop_start", np.arange(n, dtype=np.int32) * 3)
    me.polygons.foreach_set("loop_total", np.full(n, 3, np.int32))
    me.uv_layers.new(name="uv").data.foreach_set("uv", uv.astype(np.float32).ravel())
    me.update()
    me.validate()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    if material:
        me.materials.append(material)
    return ob


def decimated(ob, target):
    """(positions, normals per vertex, triangles, per-corner uv) after collapsing to ~target triangles."""
    n = len(ob.data.polygons)
    mod = ob.modifiers.new("dec", "DECIMATE")
    mod.ratio = min(1.0, target / max(n, 1))
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    ev = ob.evaluated_get(dg)
    me = ev.to_mesh()
    me.calc_loop_triangles()
    nv, nt = len(me.vertices), len(me.loop_triangles)
    co = np.empty(nv * 3, np.float32)
    me.vertices.foreach_get("co", co)
    no = np.empty(nv * 3, np.float32)
    me.vertices.foreach_get("normal", no)
    tv = np.empty(nt * 3, np.int32)
    me.loop_triangles.foreach_get("vertices", tv)
    tl = np.empty(nt * 3, np.int32)
    me.loop_triangles.foreach_get("loops", tl)
    flat = np.empty(len(me.loops) * 2, np.float32)
    me.uv_layers.active.data.foreach_get("uv", flat)
    uv = flat.reshape(-1, 2)[tl.reshape(-1, 3)]
    out = co.reshape(-1, 3).copy(), no.reshape(-1, 3).copy(), tv.reshape(-1, 3).copy(), uv.copy()
    ev.to_mesh_clear()
    ob.modifiers.remove(mod)
    return out


def piece_sizes(co, tri):
    """Size (bounding-box diagonal) of the connected piece each triangle belongs to. Vertices are
    joined by position, so pieces are not cut along UV seams."""
    _, vid = np.unique(np.round(co, 4), axis=0, return_inverse=True)
    t = vid[tri]
    lab = np.arange(vid.max() + 1)
    edges = np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    for _ in range(400):
        m = np.minimum(lab[edges[:, 0]], lab[edges[:, 1]])
        new = lab.copy()
        np.minimum.at(new, edges[:, 0], m)
        np.minimum.at(new, edges[:, 1], m)
        for _ in range(6):
            new = new[new]
        if np.array_equal(new, lab):
            break
        lab = new
    tl = lab[t[:, 0]]
    ids, inv = np.unique(tl, return_inverse=True)
    lo = np.full((len(ids), 3), np.inf)
    hi = np.full((len(ids), 3), -np.inf)
    for k in range(3):
        np.minimum.at(lo, inv, co[tri[:, k]])
        np.maximum.at(hi, inv, co[tri[:, k]])
    return np.linalg.norm(hi - lo, axis=1)[inv]


def kmeans(pts, k, iters=14):
    c = pts[rng.choice(len(pts), k, replace=False)].copy()
    for _ in range(iters):
        lab = nearest(pts, c)
        for j in range(k):
            m = lab == j
            if m.any():
                c[j] = pts[m].mean(0)
            else:
                c[j] = pts[rng.integers(len(pts))]
    return c, nearest(pts, c)


def nearest(pts, c, chunk=20000):
    lab = np.empty(len(pts), np.int32)
    c2 = (c * c).sum(1)
    for i in range(0, len(pts), chunk):
        p = pts[i:i + chunk]
        lab[i:i + chunk] = np.argmin(c2[None, :] - 2 * p @ c.T, axis=1)
    return lab


def cluster_frames(pts, lab, k):
    """Per cluster: centre, principal axes (rows, largest first), standard deviations."""
    cen, axes, std = np.zeros((k, 3)), np.zeros((k, 3, 3)), np.zeros((k, 3))
    for j in range(k):
        p = pts[lab == j]
        if len(p) < 4:
            cen[j] = p.mean(0) if len(p) else 0
            axes[j] = np.eye(3)
            std[j] = 0.05
            continue
        cen[j] = p.mean(0)
        w, v = np.linalg.eigh(np.cov((p - cen[j]).T))
        axes[j] = v[:, ::-1].T
        std[j] = np.sqrt(np.maximum(w[::-1], 1e-8))
    return cen, axes, std


def image_material(name, path):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(path)
    nt.links.new(tex.outputs["Color"], nt.nodes["Principled BSDF"].inputs["Base Color"])
    nt.nodes.active = tex
    return mat


def setup_render():
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.display.shading.light = "FLAT"
    sc.display.shading.color_type = "TEXTURE"
    sc.display.render_aa = "16"
    sc.render.film_transparent = True
    sc.view_settings.view_transform = "Standard"
    sc.render.resolution_x = sc.render.resolution_y = CELL
    sc.render.resolution_percentage = 100
    try:
        sc.render.image_settings.media_type = "IMAGE"
    except (AttributeError, TypeError):
        pass
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA"
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.data.type = "ORTHO"
    sc.collection.objects.link(cam)
    sc.camera = cam
    return sc, cam


def image_of(material, role="diff"):
    """File of the base colour (or normal) texture of a Poly Haven material."""
    for node in material.node_tree.nodes:
        if node.type == "TEX_IMAGE" and node.image and f"_{role}_" in os.path.basename(node.image.filepath):
            return bpy.path.abspath(node.image.filepath)
    return None


def build(variant, model, obj_name, leaf_keys, sc, cam):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc, cam = setup_render()
    path = next(os.path.join(SRC, model, f) for f in os.listdir(os.path.join(SRC, model)) if f.endswith(".gltf"))
    bpy.ops.import_scene.gltf(filepath=path)
    src = bpy.data.objects[obj_name]
    mats = [s.material for s in src.material_slots]
    co, tri, uv, tm = mesh_arrays(src)
    leaf_ids = [i for i, m in enumerate(mats) if any(k in m.name for k in leaf_keys)]
    is_leaf = np.isin(tm, leaf_ids)
    # Blender is Z up; put the foot of the trunk at the origin: the axis is where the wood is at
    # 10-20 % of the height (the island_tree models stand on a mound of soil and rock, which is
    # wood-coloured geometry far from that axis: left out below)
    wood_v = co[np.unique(tri[~is_leaf])]
    z0, zh = co[:, 2].min(), np.ptp(co[:, 2])
    band = wood_v[(wood_v[:, 2] > z0 + 0.10 * zh) & (wood_v[:, 2] < z0 + 0.20 * zh)]
    origin = np.array([np.median(band[:, 0]), np.median(band[:, 1]), z0], np.float32)
    co = co - origin
    height = float(co[:, 2].max())
    tc = co[tri]
    mound = ~is_leaf & (tc[:, :, 2].max(1) < 0.08 * height) & (np.hypot(tc[:, :, 0], tc[:, :, 1]).min(1) > 0.10 * height)
    tri, uv, tm, is_leaf = tri[~mound], uv[~mound], tm[~mound], is_leaf[~mound]
    used = np.unique(tri)
    co_used = co[used]
    radius = float(np.abs(co_used[:, :2]).max())
    print(f"{variant}: {len(tri):,} triangles ({is_leaf.sum():,} foliage), height {height:.2f} m, radius {radius:.2f} m")
    for o in list(bpy.data.objects):
        if o.type == "MESH":
            o.hide_render = True

    out = {"height": height, "radius": radius}
    # ---- wood: the material with the most wood triangles gives the bark texture
    wood_mat = np.bincount(tm[~is_leaf]).argmax()
    wtri, wuv = tri[~is_leaf], uv[~is_leaf]
    size = piece_sizes(co, wtri)
    for lod, target in enumerate(WOOD_TRIS):
        keep = size >= WOOD_KEEP[lod] * height
        wob = new_mesh("wood", co, wtri[keep], wuv[keep])
        wob.hide_render = True
        p, n, t, u = decimated(wob, target)
        out[f"wood{lod}_pos"], out[f"wood{lod}_nrm"], out[f"wood{lod}_tri"], out[f"wood{lod}_uv"] = p, n, t, u
        print(f"  wood LOD{lod}: {keep.sum():,} of {len(wtri):,} triangles kept -> {len(t):,}")
        bpy.data.objects.remove(wob)

    # ---- foliage clusters
    ltri, luv = tri[is_leaf], uv[is_leaf]
    cent = co[ltri].mean(1)
    e1, e2 = co[ltri[:, 1]] - co[ltri[:, 0]], co[ltri[:, 2]] - co[ltri[:, 0]]
    area = np.linalg.norm(np.cross(e1, e2), axis=1) + 1e-12
    pick = rng.choice(len(cent), min(SAMPLE, len(cent)), replace=False, p=area / area.sum())
    pts = cent[pick].astype(np.float64)
    frames = []
    for lod, k in enumerate(CLUSTERS):
        c, lab = kmeans(pts, k)
        cen, axes, std = cluster_frames(pts, lab, k)
        frames.append((c, cen, axes, std))
        out[f"leaf{lod}_cen"], out[f"leaf{lod}_axes"], out[f"leaf{lod}_std"] = cen, axes, std

    # ---- pictures of TEMPLATES real clusters (from the finest clustering), seen along their thin axis
    c, cen, axes, std = frames[0]
    full_lab = nearest(cent.astype(np.float64), c)
    counts = np.bincount(full_lab, minlength=len(c))
    order = np.argsort(np.abs(std[:, 0] - np.median(std[:, 0])) + 0.5 * np.abs(counts - np.median(counts)) / max(np.median(counts), 1) * std[:, 0])
    chosen = order[:TEMPLATES]
    leaf_mat = image_material("leafmat", image_of(mats[leaf_ids[0]]))
    atlas = np.zeros((CELL * 2, CELL * 4, 4), np.float32)
    for n, j in enumerate(chosen):
        m = full_lab == j
        ob = new_mesh(f"tpl{n}", co, ltri[m], luv[m], leaf_mat)
        a0, a1, a2 = axes[j]
        half = CARD_STD * std[j, 0]
        cam.data.ortho_scale = 2 * half
        cam.data.clip_start, cam.data.clip_end = 0.01, 100
        rot = Matrix((a0, a1, a2)).transposed().to_4x4()       # camera x = a0, y = a1, looks along -a2
        cam.matrix_world = Matrix.Translation(Vector(cen[j]) + Vector(a2) * 20) @ rot
        tmp = os.path.join(OUT, f"_{variant}_{n}.png")
        sc.render.filepath = tmp
        bpy.ops.render.render(write_still=True)
        img = bpy.data.images.load(tmp)
        px = np.array(img.pixels[:], np.float32).reshape(CELL, CELL, 4)[::-1]       # row 0 at the top
        bpy.data.images.remove(img)
        os.remove(tmp)
        r, cc = divmod(n, 4)
        atlas[r * CELL:(r + 1) * CELL, cc * CELL:(cc + 1) * CELL] = px
        bpy.data.objects.remove(ob)
    out["atlas"] = (np.clip(atlas, 0, 1) * 255 + 0.5).astype(np.uint8)
    np.savez_compressed(os.path.join(OUT, f"{variant}.npz"), **out)
    cover = float((atlas[..., 3] > 0.5).mean())
    print(f"  foliage: clusters {CLUSTERS}, template cover {cover:.0%}")
    return {"height_m": height, "radius_m": radius, "bark_diff": image_of(mats[wood_mat], "diff"),
            "bark_nor": image_of(mats[wood_mat], "nor_gl"), "template_cover": cover,
            "card_std": CARD_STD, "templates": TEMPLATES, "cell": CELL}


def main():
    os.makedirs(OUT, exist_ok=True)
    only = [a for a in sys.argv[sys.argv.index("--") + 1:]] if "--" in sys.argv else []
    path = os.path.join(OUT, "models.json")
    meta = json.load(open(path)) if os.path.exists(path) else {}
    for variant, model, obj_name, leaf_keys in VARIANTS:
        if only and variant not in only:
            continue
        meta[variant] = build(variant, model, obj_name, leaf_keys, None, None)
        json.dump(meta, open(path, "w"), indent=1)


if __name__ == "__main__":
    main()
