#!/usr/bin/env python3
"""Landmarks along the hill climb.

    landmarks_climb.py overrides     restyle the landmark blocks in buildings_local.geojson
                                     (run after build_buildings.py / roof_analysis.py)

1. Overrides (config.json "landmark_overrides"): the block nearest to "at" (or to the climb at
   "at_route_m") gets the given style, wall material, roof shape / ridge direction / material
   and gable cladding, keeps its generated mesh (no Italy town model), and the blocks listed in
   "merge" are joined to it (a bell tower surveyed as two volumes).
2. Extras (scenery_inputs.py calls build()): parts the block generator does not make, as one
   Collada with its own materials (prefix lmc_) in output/scenery/climb_landmarks/, read by
   ac_scenery.py; their collision boxes in output/climb_landmarks_collision.npz:
     * a mountain refuge (config.json "climb_landmarks" "refuge": {"landmark", "sign"}): glazed
       veranda on the car-park side with the name on its fascia, a timber balcony on the gable
       end, flagpole, picnic tables, the water-filled barriers closing the road beyond it
     * name boards ("name_boards": [[landmark, text], ...]), crosses on the chapels
     * domes ("domes": [{"landmark", "kind": "campanile" | "church"}])
     * wayside shrines (capitelli) from OSM historic=wayside_shrine near the climb, among them
       numbered Via Crucis stations; fountains (man_made=water_tap) and war memorials
3. Ponds (config.json "ponds"): build_terrain.py carves the basin (pond_basins()). The water
   surface itself is not built for Assetto Corsa yet (it was a BeamNG WaterBlock).
"""

import json
import math
import re
import sys

import numpy as np
import shapely
from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import LineString, Point, Polygon, mapping, shape
from shapely.ops import unary_union

import dds
from boundaries import _quad
from buildings_mesh import TexMesh, collada_lods
from climb_report import climb_polyline
from common import DATA, OUTPUT, Config, Heightfield

FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
STEEL = "metal_dark"          # our galvanised steel (output/textures/metal_dark.png)


# ------------------------------------------------------------------ 1. overrides

def _ridge(outline, which):
    rect = outline.minimum_rotated_rectangle
    c = np.asarray(rect.exterior.coords)
    e1, e2 = c[1] - c[0], c[2] - c[1]
    long_, short = (e1, e2) if np.linalg.norm(e1) >= np.linalg.norm(e2) else (e2, e1)
    v = long_ if which == "long" else short
    return (v / np.linalg.norm(v)).tolist()


def _nearest_block(polys, pt, max_d=6.0):
    d = [g.distance(pt) for g in polys]
    i = int(np.argmin(d))
    return i if d[i] <= max_d else None


def apply_overrides():
    cfg = Config()
    path = OUTPUT / "buildings_local.geojson"
    gj = json.loads(path.read_text())
    feats = gj["features"]
    polys = [shape(f["geometry"]) for f in feats]
    c, w, s = climb_polyline()
    drop, done = set(), []
    for o in cfg.raw.get("landmark_overrides", []):
        if "at" in o:
            pt = Point(*o["at"])
        elif "at_route_m" in o:
            pt = Point(np.interp(o["at_route_m"], s, c[:, 0]), np.interp(o["at_route_m"], s, c[:, 1]))
        else:
            continue
        i = _nearest_block(polys, pt, o.get("max_dist", 6.0))
        if i is None:
            print(f"  override {o.get('name')}: no block near {pt.coords[0]}")
            continue
        p = feats[i]["properties"]
        for xy in o.get("remove", []):              # volumes the landmark model replaces
            j = _nearest_block(polys, Point(*xy), 3.0)
            if j is not None and j != i:
                drop.add(j)
        for xy in o.get("merge", []):
            j = _nearest_block(polys, Point(*xy), 3.0)
            if j is None or j == i or j in drop:
                continue
            merged = unary_union([polys[i], polys[j]]).buffer(0.05, join_style="mitre").buffer(-0.05, join_style="mitre")
            if merged.geom_type != "Polygon":
                continue
            p["members"] += feats[j]["properties"]["members"]
            p["eave_z"] = max(p["eave_z"], feats[j]["properties"]["eave_z"])
            p["ground_z"] = min(p["ground_z"], feats[j]["properties"]["ground_z"])
            p["base_z"] = min(p["base_z"], feats[j]["properties"]["base_z"])
            polys[i] = merged
            feats[i]["geometry"] = mapping(merged)
            drop.add(j)
        if "orient_towards" in o:
            # surveyed size and centre, long axis turned towards a point (the 2005 outline of the
            # rebuilt rifugio no longer matches the building seen in 2024-2026)
            g = polys[i]
            rect = g.minimum_rotated_rectangle
            cc = np.asarray(rect.exterior.coords)[:4]
            e1, e2 = np.linalg.norm(cc[1] - cc[0]), np.linalg.norm(cc[2] - cc[1])
            L_, W_ = max(e1, e2), min(e1, e2)
            ctr = np.asarray(g.centroid.coords[0]) + np.asarray(o.get("shift", [0, 0]), float)
            uu = np.asarray(o["orient_towards"], float) - ctr
            uu /= np.linalg.norm(uu)
            vv = np.array([-uu[1], uu[0]])
            ring = [ctr + uu * a_ * L_ / 2 + vv * b_ * W_ / 2 for a_, b_ in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
            newg = Polygon(ring)
            feats[i]["geometry"] = mapping(newg)
            polys[i] = newg
            p["members"] = [dict(p["members"][0], geometry=mapping(newg))]
        if "style" in o:
            p["style"] = o["style"]
        if "wall" in o:
            for m in p["members"]:
                m["wall"] = o["wall"]
        if "roof" in o:
            p["roof"] = o["roof"]
            if o["roof"] == "gable":
                p["ridge"] = _ridge(polys[i], o.get("ridge", "long"))
        for k in ("roof_mat", "gable_mat", "roof_pitch"):
            if k in o:
                p[k] = o[k]
        if "eave_add" in o:
            p["eave_z"] += o["eave_add"]
        p["name"] = o.get("name", p.get("name"))
        p["keep_generated"] = True
        p["landmark"] = o.get("name")
        done.append(o.get("name"))
    gj["features"] = [f for k, f in enumerate(feats) if k not in drop]
    path.write_text(json.dumps(gj))
    print(f"Landmark overrides: {len(done)} blocks ({', '.join(done)}), {len(drop)} merged into towers")


# ------------------------------------------------------------------ textures / materials

def _font(px):
    try:
        return ImageFont.truetype(FONT, px)
    except OSError:
        return ImageFont.load_default()


def _flat(rgb, size=64):
    return Image.new("RGB", (size, size), rgb)


def _board(text, w_m, h_m, bg=(74, 50, 32), fg=(240, 230, 205), px=200):
    W, H = int(w_m * px), int(h_m * px)
    im = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(im)
    size = int(H * 0.6)
    f = _font(size)
    while f.getlength(text) > W * 0.92 and size > 8:
        size -= 2
        f = _font(size)
    d.text((W / 2, H / 2), text, font=f, fill=fg, anchor="mm")
    d.rectangle([2, 2, W - 3, H - 3], outline=(52, 34, 22), width=4)
    tw = 1 << int(math.ceil(math.log2(max(W, 64))))
    return im.resize((min(tw, 2048), max(64, min(tw, 2048) // max(1, int(round(w_m / h_m))))), Image.LANCZOS)


def _icon():
    im = Image.new("RGB", (128, 192), (238, 234, 222))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([14, 14, 114, 178], radius=40, fill=(46, 72, 130))
    d.ellipse([44, 40, 84, 80], fill=(236, 212, 170))
    d.polygon([(34, 170), (64, 78), (94, 170)], fill=(232, 228, 214))
    d.ellipse([38, 30, 90, 46], outline=(220, 190, 80), width=3)
    return im


def _flag():
    im = Image.new("RGB", (192, 128))
    d = ImageDraw.Draw(im)
    for k, col in enumerate(((0, 140, 69), (244, 245, 240), (205, 33, 42))):
        d.rectangle([k * 64, 0, k * 64 + 63, 127], fill=col)
    return im


def _copy(name):
    p = OUTPUT / "textures" / f"{name}.png"
    return Image.open(p).convert("RGB") if p.exists() else _flat((128, 128, 128))


class Mats:
    """Materials of the extras: one DDS colour map each in textures/ (main.materials.json lists them)."""

    def __init__(self, tex_dir):
        self.dir, self.defs = tex_dir, {}

    def __call__(self, name, img_fn):
        if name not in self.defs:
            img = img_fn()
            dds.write(img, self.dir / f"{name}.color.dds", "DXT1")
            self.defs[name] = {"name": name, "Stages": [{"colorMap": f"textures/{name}.color.dds"}]}
        return name


# ------------------------------------------------------------------ mesh helpers

def obox(mesh, mat, c, u, size, z0, z1, uv_scale=1.0, faces="all"):
    """Box centred at c (xy), axes u (unit, along) and v = left of u, size (len_u, len_v), from
    z0 to z1. faces: "all" or a subset of "nesw tb" letters: n = +v, s = -v, e = +u, w = -u."""
    u = np.asarray(u, float) / np.linalg.norm(u)
    v = np.array([-u[1], u[0]])
    a, b = size[0] / 2, size[1] / 2
    corners = [np.asarray(c) + u * sx * a + v * sy * b for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    sides = {"s": (0, 1, -v), "e": (1, 2, u), "n": (2, 3, v), "w": (3, 0, -u)}
    for k, (i, j, n) in sides.items():
        if faces != "all" and k not in faces:
            continue
        p, q = corners[i], corners[j]
        L = np.linalg.norm(q - p)
        _quad(mesh, mat, [[*p, z0], [*q, z0], [*q, z1], [*p, z1]],
              [(0, 0), (L / uv_scale, 0), (L / uv_scale, (z1 - z0) / uv_scale), (0, (z1 - z0) / uv_scale)], [n[0], n[1], 0])
    if faces == "all" or "t" in faces:
        _quad(mesh, mat, [[*corners[0], z1], [*corners[1], z1], [*corners[2], z1], [*corners[3], z1]],
              [(0, 0), (size[0] / uv_scale, 0), (size[0] / uv_scale, size[1] / uv_scale), (0, size[1] / uv_scale)], [0, 0, 1])
    if faces == "all" or "b" in faces:
        _quad(mesh, mat, [[*corners[0], z0], [*corners[1], z0], [*corners[2], z0], [*corners[3], z0]],
              [(0, 0), (1, 0), (1, 1), (0, 1)], [0, 0, -1])


def panel(mesh, mat, p, q, z0, z1, facing, uv=((0, 0), (1, 0), (1, 1), (0, 1))):
    _quad(mesh, mat, [[*p, z0], [*q, z0], [*q, z1], [*p, z1]], list(uv), [facing[0], facing[1], 0])


def gable_roof(mesh, mat, c, u, length, width, z_eave, rise, overhang=0.15):
    u = np.asarray(u, float) / np.linalg.norm(u)
    v = np.array([-u[1], u[0]])
    a, b = length / 2 + overhang, width / 2 + overhang
    for sv in (1, -1):
        e0, e1 = np.asarray(c) - u * a + v * sv * b, np.asarray(c) + u * a + v * sv * b
        r0, r1 = np.asarray(c) - u * a, np.asarray(c) + u * a
        n = np.array([*(v * sv * rise), b])
        _quad(mesh, mat, [[*e0, z_eave], [*e1, z_eave], [*r1, z_eave + rise], [*r0, z_eave + rise]],
              [(0, 0), (2 * a, 0), (2 * a, b), (0, b)], n)


def cross(mesh, mat, p, u, z, h=1.0, arm=0.6, t=0.06):
    obox(mesh, mat, p, u, (t, t), z, z + h)
    obox(mesh, mat, np.asarray(p), np.array([-u[1], u[0]]), (arm, t), z + h * 0.62, z + h * 0.62 + t)


def _balusters():
    im = Image.new("RGB", (256, 128), (86, 58, 38))
    d = ImageDraw.Draw(im)
    for x in range(0, 256, 22):
        d.rectangle([x + 4, 0, x + 14, 127], fill=(118, 82, 52))
    d.rectangle([0, 0, 255, 10], fill=(96, 66, 42))
    return im


def _logo():
    im = Image.new("RGB", (256, 128), (230, 224, 210))
    d = ImageDraw.Draw(im)
    d.polygon([(30, 100), (110, 30), (150, 70), (180, 45), (226, 100)], fill=(74, 92, 120))
    d.polygon([(96, 42), (110, 30), (124, 44)], fill=(246, 246, 246))
    d.rectangle([4, 4, 251, 123], outline=(90, 62, 40), width=6)
    return im


def _dome(mesh, mat, drum_mat, c, r, z0, drum=1.5, statue=False, metal=STEEL, sides=8, rings=5):
    """Octagonal drum and a dome (or cupola) with a small lantern / statue on top."""
    ang = np.linspace(0, 2 * np.pi, sides + 1)
    ring = np.asarray(c) + r * np.column_stack([np.cos(ang), np.sin(ang)])
    for i in range(sides):
        a, b = ring[i], ring[i + 1]
        o = (a + b) / 2 - c
        panel(mesh, drum_mat, a, b, z0, z0 + drum, o)
    prev = [(ring[i], z0 + drum) for i in range(sides + 1)]
    for k in range(1, rings + 1):
        t = k / rings * (np.pi / 2)
        rr, zz = r * math.cos(t), z0 + drum + r * 0.9 * math.sin(t)
        cur = [(np.asarray(c) + rr * np.array([math.cos(a), math.sin(a)]), zz) for a in ang]
        for i in range(sides):
            (p0, z0_), (p1, _) = prev[i], prev[i + 1]
            (q0, z1_), (q1, _) = cur[i], cur[i + 1]
            o = ((p0 + p1) / 2 - c)
            _quad(mesh, mat, [[*p0, z0_], [*p1, z0_], [*q1, z1_], [*q0, z1_]], [(0, 0), (1, 0), (1, 1), (0, 1)],
                  [o[0], o[1], 0.6])
        prev = cur
    top = z0 + drum + r * 0.9
    if statue:
        obox(mesh, metal, c, (1, 0), (0.25, 0.25), top - 0.1, top + 1.2)
        obox(mesh, metal, c, (1, 0), (0.55, 0.12), top + 0.75, top + 0.85)
    else:
        obox(mesh, drum_mat, c, (1, 0), (r * 0.3, r * 0.3), top - 0.2, top + 1.0)
        cross(mesh, metal, c, np.array([1.0, 0.0]), top + 1.0, h=1.4, arm=0.8, t=0.08)


# ------------------------------------------------------------------ 2. extras

def _block_named(feats, name):
    for f in feats:
        if f["properties"].get("landmark") == name:
            return f
    return None


def _frame(g):
    rect = g.minimum_rotated_rectangle
    c = np.asarray(rect.exterior.coords)[:4]
    e1, e2 = c[1] - c[0], c[2] - c[1]
    if np.linalg.norm(e1) < np.linalg.norm(e2):
        e1, e2 = e2, e1
    u = e1 / np.linalg.norm(e1)
    return np.asarray(rect.centroid.coords[0]), u, np.linalg.norm(e1), np.linalg.norm(e2)


def build(cfg, out_dir):
    """The extras as out_dir/climb_landmarks/climb_landmarks.dae + main.materials.json + textures/."""
    feats = json.loads((OUTPUT / "buildings_local.geojson").read_text())["features"]
    hf = Heightfield(cfg, OUTPUT / "dem_final.tif")
    c, w, s = climb_polyline()
    line = LineString(c[:, :2])
    shape_dir = out_dir / "climb_landmarks"
    tex = shape_dir / "textures"
    tex.mkdir(parents=True, exist_ok=True)
    M = Mats(tex)
    glass = M("lmc_glass", lambda: _flat((58, 74, 86)))
    timber = M("lmc_timber", lambda: _copy("timber_dark"))
    wood = M("lmc_wood", lambda: _copy("bench_wood"))
    white = M("lmc_white", lambda: _flat((236, 233, 224)))
    stone = M("lmc_stone", lambda: _copy("bld_stone_rubble"))
    concrete = M("lmc_concrete", lambda: _copy("concrete"))
    orange = M("lmc_orange", lambda: _flat((232, 104, 28)))
    coppi = M("lmc_coppi", lambda: _copy("roof_coppi"))
    icon = M("lmc_icon", _icon)
    flag = M("lmc_flag", _flag)
    water = M("lmc_water", lambda: _flat((52, 70, 64)))
    mesh, col = TexMesh(), TexMesh()
    n_parts = {}

    def z_at(p):
        return float(hf.sample(np.array([p[0]]), np.array([p[1]]))[0])

    lmk = cfg.raw.get("climb_landmarks", {})
    # The refuge (as surveyed on Street View): the gable end faces the car park. In
    # front of it, on a stone-clad semi-basement with an arcade, a terrace with a timber
    # balustrade: on its left an open timber loggia, on its right a glazed, stone-clad dining
    # room under its own small gable. The name is painted on a band across the gable facade;
    # flagpole at the terrace's left corner; terraced lawn below with peeled-log rail fences
    # and a dry-stone wall at the car park.
    ref = lmk.get("refuge") or {}
    rif = _block_named(feats, ref["landmark"]) if ref.get("landmark") else None
    if rif:
        g = shape(rif["geometry"])
        C, u, L, W = _frame(g)
        se = 1.0 if Point(C + u * L / 2).distance(line) < Point(C - u * L / 2).distance(line) else -1.0
        n = u * se                                          # out of the car-park gable
        v = np.array([-n[1], n[0]])                         # along that gable (left as seen from the car park: -v)
        E = C + n * L / 2                                   # centre of the gable face
        z0 = rif["properties"]["ground_z"]
        base_h, depth, room_h = 2.6, 4.6, 2.7
        zt = z0 + base_h                                    # terrace floor
        # stone plinth on the main block's gable (the semi-basement)
        panel(mesh, stone, E + n * 0.03 + v * W / 2, E + n * 0.03 - v * W / 2, z0 - 0.3, zt, n,
              ((0, 0), (W / 2, 0), (W / 2, (base_h + 0.3) / 2), (0, (base_h + 0.3) / 2)))
        # semi-basement under the terrace, with dark arcade openings towards the car park
        bc = E + n * depth / 2
        obox(mesh, stone, bc, n, (depth, W + 0.4), z0 - 0.6, zt, 2.0)
        obox(col, "collision", bc, n, (depth, W + 0.4), z0 - 0.6, zt)
        front = E + n * (depth + 0.02)
        for k in range(3):
            a0 = -W / 2 + 0.6 + k * (W - 1.2) / 3 + 0.25
            a1 = a0 + (W - 1.2) / 3 - 0.5
            panel(mesh, glass, front + v * a0, front + v * a1, z0 + 0.1, z0 + 2.1, n)
        obox(mesh, concrete, bc, n, (depth + 0.2, W + 0.6), zt - 0.05, zt + 0.12)           # terrace slab
        # timber balustrade around the terrace (front and sides)
        rail_z0, rail_z1 = zt + 0.12, zt + 1.05
        fence_t = M("lmc_balusters", _balusters)
        pts = [E - v * (W / 2 + 0.3), E - v * (W / 2 + 0.3) + n * (depth + 0.1),
               E + v * (W / 2 + 0.3) + n * (depth + 0.1), E + v * (W / 2 + 0.3)]
        for a_, b_ in zip(pts[:-1], pts[1:]):
            seg = b_ - a_
            ln = np.linalg.norm(seg)
            nrm = np.array([seg[1], -seg[0]]) / ln
            panel(mesh, fence_t, a_, b_, rail_z0, rail_z1, nrm, ((0, 0), (ln / 1.2, 0), (ln / 1.2, 1), (0, 1)))
            panel(mesh, fence_t, b_, a_, rail_z0, rail_z1, -nrm, ((0, 0), (ln / 1.2, 0), (ln / 1.2, 1), (0, 1)))
            obox(mesh, timber, (a_ + b_) / 2, seg, (ln, 0.1), rail_z1, rail_z1 + 0.08)
        # loggia (left half): timber posts and a timber roof
        lc = E + n * depth * 0.55 - v * W / 4
        for sv in (-1, 1):
            for sn in (-1, 1):
                obox(mesh, timber, lc + v * sv * (W / 4 - 0.3) + n * sn * (depth * 0.45 - 0.2), n, (0.2, 0.2), zt, zt + room_h)
        obox(mesh, timber, lc, n, (depth * 0.95, W / 2), zt + room_h, zt + room_h + 0.25)
        # glazed dining room (right half): stone-clad lower wall, glass, its own gable roof
        rc = E + n * depth * 0.5 + v * W / 4
        obox(mesh, stone, rc, n, (depth, W / 2 - 0.1), zt, zt + 0.9, 1.5)
        obox(mesh, glass, rc, n, (depth - 0.1, W / 2 - 0.2), zt + 0.9, zt + room_h, faces="nesw")
        obox(col, "collision", rc, n, (depth, W / 2), zt, zt + room_h)
        gable_roof(mesh, M("lmc_roof_dark", lambda: _copy("roof_dark")), rc, n, depth + 0.3, W / 2, zt + room_h, 1.3, overhang=0.35)
        gf = rc + n * (depth / 2 + 0.36)                      # timber gable with the logo board
        tri = [[*(gf - v * W / 4), zt + room_h], [*(gf + v * W / 4), zt + room_h], [*gf, zt + room_h + 1.25]]
        mesh.tri(timber, tri, [(0, 0), (1, 0), (0.5, 0.6)])
        mesh.tri(timber, tri[::-1], [(0.5, 0.6), (1, 0), (0, 0)])
        logo = M("lmc_logo", _logo)
        panel(mesh, logo, gf + n * 0.02 - v * 0.8, gf + n * 0.02 + v * 0.8, zt + room_h + 0.05, zt + room_h + 0.75, n)
        # the name painted on the gable facade above the extension
        name = M("lmc_name_rifugio", lambda: _board(ref.get("sign", ""), 7.0, 0.55, bg=(236, 233, 224), fg=(52, 50, 48)))
        nz = zt + room_h + 1.5
        panel(mesh, name, E + n * 0.04 + v * min(3.6, W * 0.38), E + n * 0.04 - v * min(3.6, W * 0.38), nz, nz + 0.55, n)
        # flagpole at the terrace's left corner (as seen from the car park)
        fp = E + n * (depth - 0.3) + v * (W / 2 + 0.1)
        obox(mesh, STEEL, fp, n, (0.1, 0.1), zt, zt + 8.5)
        panel(mesh, flag, fp, fp + n * 1.5, zt + 7.3, zt + 8.3, v)
        panel(mesh, flag, fp + n * 1.5, fp, zt + 7.3, zt + 8.3, -v)
        # terraced lawn below: two peeled-log rail fences and a dry-stone wall above the car park
        logs = M("lmc_logs", lambda: _copy("bench_wood"))
        for dist, half in ((depth + 3.5, W / 2 + 6), (depth + 7.0, W / 2 + 8)):
            a_, b_ = E + n * dist - v * half, E + n * dist + v * half
            for k in range(int(2 * half / 2.0) + 1):
                pp = a_ + (b_ - a_) * k / int(2 * half / 2.0)
                zp = z_at(pp)
                obox(mesh, logs, pp, n, (0.14, 0.14), zp - 0.3, zp + 0.95)
            for hh in (0.45, 0.85):
                za, zb = z_at(a_) + hh, z_at(b_) + hh
                mid = (a_ + b_) / 2
                obox(mesh, logs, mid, v, (2 * half, 0.12), (za + zb) / 2 - 0.06, (za + zb) / 2 + 0.06)
        wa = E + n * (depth + 10.5)
        zw = z_at(wa)
        obox(mesh, stone, wa, v, (2 * (W / 2 + 10), 0.6), zw - 1.4, zw + 0.2, 1.5)
        obox(col, "collision", wa, v, (2 * (W / 2 + 10), 0.6), zw - 1.4, zw + 0.2)
        # picnic tables on the lawn
        for k in range(2):
            tp = E + n * (depth + 5.2) + v * (k - 0.5) * 5.0
            ztb = z_at(tp)
            obox(mesh, wood, tp, v, (1.8, 0.8), ztb + 0.72, ztb + 0.78)
            for sb in (1, -1):
                obox(mesh, wood, tp + n * sb * 0.75, v, (1.8, 0.3), ztb + 0.42, ztb + 0.47)
            obox(col, "collision", tp, v, (1.8, 2.0), ztb, ztb + 0.8)
        n_parts["rifugio"] = 1
    gate = cfg.raw.get("finish_gate")
    if gate:
        n_parts.update(finish_gate(mesh, col, M, gate, c, z_at, feats))
    else:
        # water-filled barriers closing the road beyond the end of the climb
        end, prev = c[-1, :2], c[-6, :2]
        d = (end - prev) / np.linalg.norm(end - prev)
        perp = np.array([-d[1], d[0]])
        for k in range(-2, 2):
            p = end + d * 1.5 + perp * (k + 0.5) * 1.25
            zb = z_at(p)
            obox(mesh, orange, p, perp, (1.2, 0.45), zb, zb + 0.8)
            obox(col, "collision", p, perp, (1.2, 0.45), zb, zb + 0.8)
    # name boards (a small blue board by the door), crosses on chapels
    for nm, text in lmk.get("name_boards", []):
        f = _block_named(feats, nm)
        if not f:
            continue
        g = shape(f["geometry"])
        C, u, L, W = _frame(g)
        v = np.array([-u[1], u[0]])
        q = line.interpolate(line.project(Point(C)))
        fv = v if (np.array([q.x, q.y]) - C) @ v > 0 else -v
        mat = M("lmc_name_" + re.sub(r"\W+", "_", nm.lower()), lambda t=text: _board(t, 1.8, 0.45, bg=(28, 70, 150), fg=(246, 246, 246)))
        b0 = C + fv * (W / 2 + 0.06) + u * (L / 2 - 1.6)
        zb = f["properties"]["ground_z"] + 2.6
        panel(mesh, mat, b0 - u * 0.9, b0 + u * 0.9, zb, zb + 0.45, fv)
    for f in feats:
        p = f["properties"]
        if p.get("style") == "church" and p.get("landmark") and "Cappella" in p["landmark"]:
            g = shape(f["geometry"])
            C, u, L, W = _frame(g)
            top = p["eave_z"] + W / 2 * math.tan(math.radians(30))
            cross(mesh, STEEL, C + u * (L / 2 - 0.2), u, top - 0.2)
    # a campanile ending in an octagonal drum, a small cupola and a statue; a church with a grey
    # dome over its central plan (Street View)
    domes = {d["landmark"]: d["kind"] for d in lmk.get("domes", [])}
    for f in feats:
        p = f["properties"]
        kind = domes.get(p.get("landmark") or "")
        if kind == "campanile":
            g = shape(f["geometry"])
            C, u, L, W = _frame(g)
            r = min(L, W) * 0.36
            _dome(mesh, M("lmc_lead", lambda: _flat((118, 118, 112))), white, C, r, p["eave_z"], drum=1.4, statue=True,
                  metal=STEEL)
        elif kind == "church":
            g = shape(f["geometry"])
            C, u, L, W = _frame(g)
            top = p["eave_z"] + min(L, W) / 2 * math.tan(math.radians(20))
            _dome(mesh, M("lmc_lead", lambda: _flat((118, 118, 112))), white, C, min(L, W) * 0.28, top - 0.4, drum=2.2,
                  statue=False, metal=STEEL)
    # wayside shrines from OSM (Via Crucis stations, capitelli), fountains and memorials
    raw = json.loads((DATA / "osm" / "raw.json").read_text())["elements"]
    pois = json.loads((DATA / "osm" / "pois.json").read_text())["elements"] if (DATA / "osm" / "pois.json").exists() else []
    seen, shrines, extra = set(), 0, 0
    polys = [shape(f["geometry"]) for f in feats]
    from shapely import STRtree
    bt = STRtree(polys)
    roads = json.loads((OUTPUT / "roads_local.geojson").read_text())["features"]
    rl = [LineString(np.asarray(f["geometry"]["coordinates"])[:, :2]) for f in roads]
    rt = STRtree(rl)
    for e in raw + pois:
        if e["type"] != "node" or e["id"] in seen:
            continue
        t = e.get("tags", {})
        seen.add(e["id"])
        x, y = cfg.lonlat_to_local(e["lon"], e["lat"])
        p = np.array([float(x), float(y)])
        if line.distance(Point(p)) > 250:
            continue
        if t.get("historic") == "wayside_shrine" or t.get("man_made") == "water_tap" or t.get("historic") == "memorial":
            if len(bt.query(Point(p).buffer(1.5))) and t.get("historic") == "wayside_shrine":
                continue                       # already a surveyed building (capitello block)
            j = rt.query_nearest(Point(p))
            qq = rl[j[0]].interpolate(rl[j[0]].project(Point(p))) if len(j) else Point(p + [0, 1])
            fdir = np.array([qq.x, qq.y]) - p
            fdir = fdir / (np.linalg.norm(fdir) or 1)
            u = np.array([fdir[1], -fdir[0]])
            z = z_at(p)
            if t.get("historic") == "wayside_shrine" and str(t.get("name", "")).isdigit():
                # Via Crucis station: a small roofed timber box with the picture, on a post
                obox(mesh, timber, p, u, (0.12, 0.12), z - 0.4, z + 1.25)
                obox(mesh, timber, p, u, (0.5, 0.25), z + 1.15, z + 1.75)
                panel(mesh, icon, p + fdir * 0.13 - u * 0.17, p + fdir * 0.13 + u * 0.17, z + 1.22, z + 1.68, fdir)
                gable_roof(mesh, timber, p, u, 0.3, 0.6, z + 1.75, 0.18, overhang=0.08)
                obox(col, "collision", p, u, (0.5, 0.3), z - 0.4, z + 1.8)
                shrines += 1
            elif t.get("historic") == "wayside_shrine":
                obox(mesh, white, p, u, (0.75, 0.5), z - 0.3, z + 1.55, 0.5)
                obox(mesh, stone, p, u, (0.9, 0.62), z - 0.3, z + 0.35, 0.5)
                panel(mesh, icon, p + fdir * 0.26 - u * 0.22, p + fdir * 0.26 + u * 0.22, z + 0.6, z + 1.3, fdir)
                gable_roof(mesh, coppi, p, fdir, 0.6, 0.85, z + 1.55, 0.3, overhang=0.12)
                cross(mesh, STEEL, p, u, z + 1.85, h=0.45, arm=0.28, t=0.035)
                obox(col, "collision", p, u, (0.9, 0.62), z - 0.3, z + 1.6)
                shrines += 1
            elif t.get("man_made") == "water_tap":
                obox(mesh, stone, p, u, (2.0, 0.7), z - 0.3, z + 0.75, 0.6)
                panel(mesh, water, p - u * 0.85 - fdir * 0.25, p + u * 0.85 - fdir * 0.25, z + 0.62, z + 0.62, fdir)
                obox(mesh, STEEL, p - fdir * 0.32, u, (0.05, 0.05), z + 0.75, z + 1.05)
                obox(col, "collision", p, u, (2.0, 0.7), z - 0.3, z + 0.75)
                extra += 1
            else:
                obox(mesh, stone, p, u, (1.1, 0.35), z - 0.3, z + 1.7, 0.6)
                panel(mesh, M("lmc_plaque", lambda: _board("AI CADUTI", 1.0, 0.6, bg=(200, 196, 186), fg=(40, 40, 40))),
                      p + fdir * 0.18 - u * 0.4, p + fdir * 0.18 + u * 0.4, z + 0.8, z + 1.3, fdir)
                obox(col, "collision", p, u, (1.1, 0.35), z - 0.3, z + 1.7)
                extra += 1
    n_parts["shrines"] = shrines
    n_parts["fountains/memorials"] = extra
    (shape_dir / "climb_landmarks.dae").write_text(collada_lods("climb_landmarks", [(1, mesh)], col, "textures", "dds"))
    if col.pos:                                   # the collision boxes, for the Assetto Corsa export
        np.savez(OUTPUT / "climb_landmarks_collision.npz", pos=np.asarray(col.pos, float),
                 tri=np.asarray([t for ts in col.tris.values() for t in ts], np.int64))
    (shape_dir / "main.materials.json").write_text(json.dumps(M.defs, indent=1))
    print(f"Climb landmarks: {n_parts}")



# ------------------------------------------------------------------ the finish: the old base's gate

def _camo(size=512, metres=4.0):
    """Camouflage paint (green / sand / brown blotches, ~1 m), tileable; `metres` per image."""
    rng = np.random.default_rng(7)
    f = np.fft.fftfreq(size)
    fr = np.sqrt(f[:, None] ** 2 + f[None, :] ** 2)
    fr[0, 0] = 1

    def blobs(seed):
        r = np.random.default_rng(seed)
        spec = (r.normal(size=(size, size)) + 1j * r.normal(size=(size, size))) / fr ** 1.6
        spec[fr * size > size * 1.2 / metres] = 0                     # nothing finer than ~0.8 m
        spec[0, 0] = 0
        a = np.real(np.fft.ifft2(spec))
        return (a - a.mean()) / a.std()
    a, b = blobs(1), blobs(2)
    cols = np.array([[196, 184, 136], [104, 132, 70], [118, 92, 58], [66, 88, 52]], float)
    k = np.where(a > 0.35, 1, np.where(b > 0.5, 2, np.where((a < -0.9) & (b < 0), 3, 0)))
    img = cols[k] * (0.9 + 0.1 * rng.random((size, size, 1)))
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))


def _rust(size=256):
    rng = np.random.default_rng(3)
    base = np.array([112, 64, 38], float)
    n = rng.normal(0, 1, (size // 8, size // 8))
    n = np.kron(n, np.ones((8, 8)))
    img = base * (0.85 + 0.12 * n[..., None]) + rng.normal(0, 6, (size, size, 3))
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))


def _banner(lines):
    im = Image.new("RGB", (1024, 512), (244, 244, 238))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 1023, 90], fill=(96, 142, 120))
    d.text((40, 45), "SAFE", font=_font(60), fill=(250, 250, 250), anchor="lm")
    for k, line in enumerate(lines[:4]):
        d.text((512, 160 + k * 88), line, font=_font(62), fill=(40, 40, 40), anchor="mm")
    d.rectangle([6, 6, 1017, 505], outline=(96, 142, 120), width=10)
    return im


def _bars(mesh, mat, a, b, z0, z1, step, t, u):
    """Vertical bars every `step` m between points a and b (thin boxes)."""
    L = float(np.linalg.norm(b - a))
    for k in range(1, int(L / step)):
        obox(mesh, mat, a + (b - a) * k * step / L, u, (t, t), z0, z1)


def finish_gate(mesh, col, M, gate, c, z_at, feats):
    """The end of the climb (Street View, Aug 2023): the closed gate of a former military base, a
    rusty steel double gate between camouflage-painted masonry pillars, camouflage walls with a
    small camouflage booth (dark corrugated roof) on the left, a banner on the gate; to the right
    the white tubular gate of the restaurant's yard. Behind, a lattice telecom mast on the
    summit (Street View notes; its exact position is not surveyed: the highest ground nearby
    that is clear of buildings)."""
    camo = M("lmc_camo", _camo)
    rust = M("lmc_rust", _rust)
    white = M("lmc_white_tube", lambda: _flat((232, 232, 228)))
    conc = M("lmc_concrete", lambda: _copy("concrete"))
    roof = M("lmc_corrugated", lambda: _flat((84, 64, 50)))
    galv = M("lmc_galv", lambda: _flat((150, 152, 150)))
    banner = M("lmc_banner_base", lambda: _banner(gate.get("banner", [])))
    end = np.asarray(gate["at"], float)
    k = int(np.argmin(np.linalg.norm(c[:, :2] - end, axis=1)))
    d = c[k, :2] - c[max(k - 8, 0), :2]
    d /= np.linalg.norm(d)                       # into the base
    v = np.array([-d[1], d[0]])                  # left, driving up
    g = end + d * 0.8
    z = z_at(g)
    half = 2.9                                   # half the opening
    # pillars with concrete caps
    for sv in (1, -1):
        pc = g + v * sv * (half + 0.3)
        obox(mesh, camo, pc, d, (0.6, 0.6), z - 0.4, z + 2.6, 4.0)
        obox(mesh, conc, pc, d, (0.72, 0.72), z + 2.6, z + 2.72)
        obox(col, "collision", pc, d, (0.6, 0.6), z - 0.4, z + 2.7)
    # the two leaves (closed): frame, bars, a diagonal brace, the banner on the right leaf
    for sv in (1, -1):
        a_, b_ = g + v * sv * 0.02, g + v * sv * half
        mid, ln = (a_ + b_) / 2, half - 0.02
        for zz in (0.08, 1.05, 2.0):
            obox(mesh, rust, mid, v, (ln, 0.06), z + zz - 0.04, z + zz + 0.04)
        for e in (a_, b_):
            obox(mesh, rust, e, v, (0.07, 0.07), z + 0.02, z + 2.05)
        _bars(mesh, rust, a_, b_, 0.13, 0.025, z + 0.05, z + 2.0, v)
        for t in np.linspace(0.05, 0.95, 12):    # brace from the hinge at the bottom to the latch at the top
            p = a_ + (b_ - a_) * (1 - t)
            obox(mesh, rust, p, v, (0.2, 0.04), z + 0.1 + t * 1.85, z + 0.18 + t * 1.85)
    bc = g - v * half * 0.5 - d * 0.05
    panel(mesh, banner, bc + v * 1.1, bc - v * 1.1, z + 0.75, z + 1.85, -d)           # u to the reader's right
    panel(mesh, banner, bc - v * 1.1 + d * 0.01, bc + v * 1.1 + d * 0.01, z + 0.75, z + 1.85, d)
    obox(col, "collision", g, v, (2 * half, 0.12), z - 0.2, z + 2.1)
    # left: camouflage wall to a booth with a dark corrugated roof
    bw = g + v * (half + 0.6 + 1.5)
    obox(mesh, camo, bw, v, (3.0, 0.3), z - 0.4, z + 2.3, 4.0)
    obox(col, "collision", bw, v, (3.0, 0.3), z - 0.4, z + 2.3)
    bo = g + v * (half + 0.6 + 3.0 + 1.4) + d * 0.9
    zb = min(z, z_at(bo))
    obox(mesh, camo, bo, d, (2.8, 2.8), zb - 0.4, zb + 2.5, 4.0)
    obox(mesh, roof, bo, d, (3.3, 3.3), zb + 2.5, zb + 2.62)
    obox(col, "collision", bo, d, (2.8, 2.8), zb - 0.4, zb + 2.6)
    panel(mesh, M("lmc_glass", lambda: _flat((58, 74, 86))), bo - d * 1.41 + v * 0.6, bo - d * 1.41 - v * 0.6,
          zb + 1.0, zb + 1.9, -d)
    # right: camouflage wall, then the restaurant yard's white tubular gate
    rw = g - v * (half + 0.6 + 3.0)
    obox(mesh, camo, rw, v, (6.0, 0.3), z - 0.4, z + 2.3, 4.0)
    obox(col, "collision", rw, v, (6.0, 0.3), z - 0.4, z + 2.3)
    w0, w1 = g - v * (half + 6.6), g - v * (half + 10.6)
    zw = z_at((w0 + w1) / 2)
    for zz in (0.15, 1.3):
        obox(mesh, white, (w0 + w1) / 2, v, (4.0, 0.06), zw + zz - 0.03, zw + zz + 0.03)
    _bars(mesh, white, w0, w1, 0.2, 0.035, zw + 0.1, zw + 1.33, v)
    for e in (w0, w1):
        obox(mesh, white, e, v, (0.1, 0.1), zw - 0.3, zw + 1.45)
    obox(col, "collision", (w0 + w1) / 2, v, (4.0, 0.1), zw - 0.2, zw + 1.4)
    # lattice telecom mast: the highest ground within 120 m of the gate, clear of buildings
    polys = [shape(f["geometry"]).buffer(4.0) for f in feats]
    from shapely import STRtree
    bt = STRtree(polys)
    best = None
    for dx in range(-120, 121, 6):
        for dy in range(-120, 121, 6):
            q = g + np.array([dx, dy], float)
            if np.hypot(dx, dy) > 120 or np.hypot(dx, dy) < 25 or len(bt.query(Point(q), predicate="intersects")):
                continue
            zq = z_at(q)
            if best is None or zq > best[0]:
                best = (zq, q)
    if best is not None:
        zq, q = best
        H, b0, b1 = 36.0, 2.6, 0.9
        corners = [np.array([sx, sy]) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        for cn in corners:                                   # legs
            for i in range(12):
                h0, h1 = H * i / 12, H * (i + 1) / 12
                r0, r1 = b0 + (b1 - b0) * i / 12, b0 + (b1 - b0) * (i + 1) / 12
                p0, p1 = q + cn * r0 / 2, q + cn * r1 / 2
                obox(mesh, galv, (p0 + p1) / 2, (1, 0), (0.09, 0.09), zq + h0 - (0.5 if i == 0 else 0), zq + h1)
        for i in range(1, 13):                               # horizontal rings
            h = H * i / 12
            r = b0 + (b1 - b0) * i / 12
            for ax in ((1, 0), (0, 1)):
                for sg in (1, -1):
                    cc = q + np.array([ax[1], ax[0]]) * sg * r / 2
                    obox(mesh, galv, cc, ax, (r, 0.06), zq + h - 0.03, zq + h + 0.03)
        for (pz, sz) in ((H - 1.5, 1.4), (H - 4.0, 1.4)):    # antenna panels near the top
            for ang in (0.0, 2.1, 4.2):
                dirv = np.array([np.cos(ang), np.sin(ang)])
                obox(mesh, white, q + dirv * (b1 / 2 + 0.35), dirv, (0.12, 0.35), zq + pz - sz / 2, zq + pz + sz / 2)
        obox(col, "collision", q, (1, 0), (b0, b0), zq - 0.5, zq + 3.0)
    return {"finish gate": 1, "telecom mast": int(best is not None)}


# ------------------------------------------------------------------ 3. ponds

def pond_polygons(cfg=None):
    cfg = cfg or Config()
    ponds = cfg.raw.get("ponds", [])
    if not ponds:
        return []
    raw = json.loads((DATA / "osm" / "raw.json").read_text())["elements"]
    ways = {e["id"]: e for e in raw if e["type"] == "way"}
    out = []
    for p in ponds:
        e = ways.get(p["osm_way"])
        if not e:
            continue
        x, y = cfg.lonlat_to_local(np.array([g["lon"] for g in e["geometry"]]), np.array([g["lat"] for g in e["geometry"]]))
        out.append((p, Polygon(np.column_stack([x, y])).buffer(0)))
    return out


def pond_basins(cfg, dem, local_transform):
    """Carve each pond into the terrain: water level from config, the bed ~depth below it
    (shallower at the shore), and every terrain cell inside the WaterBlock's rectangle but
    outside the pond at least 15 cm above the water (banks, a dam at the outlet)."""
    from rasterio.features import rasterize
    from scipy.ndimage import distance_transform_edt
    n = cfg.size_px
    for p, poly in pond_polygons(cfg):
        rect = poly.minimum_rotated_rectangle.buffer(1.5)
        x0, y0, x1, y1 = rect.bounds
        c0, r0 = ~local_transform * (x0, y1)
        c1, r1 = ~local_transform * (x1, y0)
        c0, r0, c1, r1 = max(int(c0) - 2, 0), max(int(r0) - 2, 0), min(int(c1) + 3, n), min(int(r1) + 3, n)
        tr = local_transform * local_transform.translation(c0, r0)
        shp = (r1 - r0, c1 - c0)
        inside = rasterize([(poly, 1)], out_shape=shp, transform=tr, fill=0, dtype="uint8").astype(bool)
        inrect = rasterize([(rect, 1)], out_shape=shp, transform=tr, fill=0, dtype="uint8").astype(bool)
        lvl, depth = float(p["level"]), float(p.get("depth", 2.0))
        dist = distance_transform_edt(inside) * cfg.mpp
        bed = lvl - np.minimum(0.25 + dist * 0.35, depth)
        sub = dem[r0:r1, c0:c1]
        sub[inside] = np.minimum(sub[inside], bed[inside])
        bank = inrect & ~inside
        sub[bank] = np.maximum(sub[bank], lvl + 0.15)
        print(f"Pond {p['name']}: water at {lvl:.1f} m, {inside.sum()} m2, bed down to {depth:.1f} m")
    return dem


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "overrides":
        apply_overrides()
