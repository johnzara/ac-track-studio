#!/usr/bin/env python3
"""Trees for the Assetto Corsa track: cards (alpha-tested quads with normal maps,
ksPerPixelAT_NM) with the images rendered by ac_tree_cards.py, at the planted positions
(output/forest_instances.npz from scenery_inputs.py: crowns traced on the orthophoto).

Species: the planting catalogue's types (vegetation.py) map onto our cards (pines and cypresses -> fir /
pine, mostly fir above 900 m; holm oaks and "scraggly" trees -> broadleaf; bushes -> shrubs);
the variant is picked by a hash of the position. Height = species base x the instance's scale.
Detail by distance d from the climb's centreline:
  d < NEAR_M       two crossed cards, both faces (front and side images)
  d < MID_M        one card facing the road, both faces
  d < FAR_M        one card facing the road
  beyond           one card facing the road, every other tree
Near the road (FULL_M, v1.2) a tree with crossed cards also gets two diagonal planes (four planes,
eight faces) and horizontal canopy cards with its top view (ac_tree_tops.py: tree_tops.png) at two
heights, both faces, so it has volume from every side and from above; those extras are drawn within
FULL_LOD m of the camera.
Clear road: no opaque column of any card image may come within TREE_CLEAR of the asphalt, at any
height (a flat card leaning over the road reads as a wall of leaves). Near the road a tree whose
crossed cards would reach that far gets one card parallel to the road, then a slightly turned one,
then a smaller one; if none fits it is left out.
Trees stand on the AC terrain mesh itself (not on the 1 m DEM, which the coarser terrain away
from the road can sit metres below). No trunk on the carriageway or verge. Within TRUNK_M of the
road each trunk gets an invisible collision post (WALL). Vertex normals lean upward so the cards
light like a canopy; each face's u runs to its viewer's right, so the normal map's up is up.
Custom Shaders Patch 3D trees (config.json assetto_corsa.csp_trees, models from
ac_csp_tree_models.py): a tree within the brief's 3D radius of the road (studio.detail.
tree_mesh_radius_m) whose variant has a model becomes a real 3D tree in CSP when its crown,
narrowed down to CSP_MIN_WIDTH of its width if need be, stays TREE_CLEAR off the asphalt. Its cards
are still built, in meshes named trees_cspfb_* / treetops_cspfb_*, which the CSP config hides: so
vanilla AC shows cards everywhere. The list goes to output/ac/csp_trees/instances.json for
ac_csp_trees.py.
Output: output/ac/trees.npz + trees.json.
"""

import json
from collections import defaultdict

import numpy as np
from scipy.spatial import Delaunay

from ac_track import Road, save_part
from common import OUTPUT, Config

CARDS = OUTPUT / "ac" / "trees"
NEAR_M, MID_M, FAR_M = 120.0, 600.0, 1200.0
TRUNK_M = 15.0
TREE_CLEAR = 0.4              # m past the asphalt edge kept free of foliage (any height)
SINK_M = 0.35                 # card base below the terrain (no root flare hanging over a slope)
GROUND_BAND = 0.05            # card height fraction above its lowest pixel that counts as touching the ground
ALPHA_REF = 0.45
TILE_M = 1024.0
TURNS = (15, -15, 30, -30, 45, -45)     # deg: single-card orientations tried after road-parallel
CSP_DIR = OUTPUT / "ac" / "csp_trees"
CSP_MIN_WIDTH = 0.4           # a 3D crown is narrowed at most to this share of its width to clear the road
CSP_MODEL = {"fir_a": "fir_c", "fir_b": "fir_c"}     # card variants without a 3D model of their own
FULL_M = 120.0                # trees this close to the climb with crossed cards get the FULL treatment:
FULL_LOD = 420.0              # two more vertical planes (45 / 135 deg) and horizontal canopy layers,
                              # drawn while the camera is within FULL_LOD m
CANOPY = {"broadleaf": ((0.55, 1.0), (0.78, 0.7)),      # (height fraction, size fraction of the top view)
          "conifer": ((0.36, 0.95), (0.62, 0.6)), "shrub": ()}
SHRINK = (0.85, 0.7, 0.55)
SPECIES = {   # planting type (vegetation.py) -> (kind, base height m)
    "scots_pine_tree": ("conifer", 21.0), "scots_pine_filler": ("conifer", 16.0), "cypress_tree": ("conifer", 12.0),
    "holm_oak_city_tall": ("broadleaf", 15.0), "scraggly_tree": ("broadleaf", 14.0),
    "holm_oak_filler_1": ("broadleaf", 12.0), "holm_oak_filler_2": ("broadleaf", 12.0),
    "holm_oak_filler_center": ("broadleaf", 13.0), "scraggly_tree_filler_1": ("broadleaf", 11.0),
    "holm_oak_city_small": ("broadleaf", 8.0), "olive_tree": ("broadleaf", 6.0),
    "fluffy_bush": ("shrub", 2.0), "generibush": ("shrub", 1.8), "scraggly_bush": ("shrub", 2.2),
    "holm_oak_bush": ("shrub", 2.4), "grape_vine_group": ("shrub", 1.6),
}


def _hash(x, y):
    h = (np.floor(x * 7.31).astype(np.int64) * 73856093) ^ (np.floor(y * 3.17).astype(np.int64) * 19349663)
    return (h & 0xFFFF) / 65535.0


MIX = {   # kind -> [(card variant, weight)]; broadleaf leans on the slender young tree (reads as
          # beech / chestnut), the olive-like island trees add variety; bushes are mostly the low,
          # bushy island_tree_03
    "broadleaf": [("broad_d", 0.6), ("broad_a", 0.2), ("broad_b", 0.2)],
    "shrub": [("broad_c", 0.7), ("shrub_a", 0.15), ("shrub_c", 0.15)],
}


def _pick(pool, u):
    names, w = zip(*pool)
    edges = np.cumsum(w) / np.sum(w)
    return np.array(names)[np.minimum(np.searchsorted(edges, u), len(names) - 1)]


def variants_for(kind, z, u, names):
    if kind == "conifer":                        # mostly fir (spruce / silver fir) above 900 m
        fir = u < np.where(z > 900, 0.7, 0.4)
        v = (u * 997) % 1.0
        return np.where(fir, _pick([(n, 1) for n in names if n.startswith("fir")], v),
                        _pick([(n, 1) for n in names if n.startswith("pine")], v))
    return _pick(MIX[kind], u)


_SURFACE = {}


def terrain_height(x, y):
    """Height of the AC terrain mesh at (x, y): the same Delaunay triangulation as ac_track.py
    (rebuilt once from its saved vertices), interpolated inside each triangle."""
    if not _SURFACE:
        P = np.load(OUTPUT / "ac" / "terrain_points.npy")
        _SURFACE.update(P=P, dt=Delaunay(P[:, :2]))
    P, dt = _SURFACE["P"], _SURFACE["dt"]
    q = np.stack([np.asarray(x, float).ravel(), np.asarray(y, float).ravel()], 1)
    q = np.clip(q, P[:, :2].min(0) + 1e-3, P[:, :2].max(0) - 1e-3)     # cards reaching past the map edge
    s = dt.find_simplex(q)
    if (s < 0).any():
        raise ValueError(f"{(s < 0).sum()} points outside the terrain")
    T = dt.transform[s]
    b = np.einsum("nij,nj->ni", T[:, :2], q - T[:, 2])
    return (P[dt.simplices[s], 2] * np.c_[b, 1 - b.sum(1)]).sum(1).reshape(np.shape(x))


def card_profiles(meta, bins=12, cols=48):
    """{(variant, view): (u, y, valid, reach)} from the sheet's alpha. For `bins` slices across a card
    (u in -0.5..0.5 of its width): the height of the lowest leaf / trunk pixel as a fraction of the
    card height, only where it touches the ground (y, valid). reach: |u| of the outer edge of every
    one of `cols` column bins with any opaque pixel (how far the image reaches either side)."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    sheet = np.asarray(Image.open(CARDS / "tree_cards.png"))[..., 3]
    cw, ch = meta["cell_px"]
    out = {}
    for v, vm in meta["variants"].items():
        for view, (cx, cy) in zip(("front", "side"), vm["cells"]):
            a = sheet[cy * ch:(cy + 1) * ch, cx * cw:(cx + 1) * cw] > ALPHA_REF * 255
            parts = np.array_split(np.arange(cw), bins)
            u = np.array([(c.mean() + 0.5) / cw - 0.5 for c in parts])
            y, valid = np.zeros(bins), np.zeros(bins, bool)
            for k, c in enumerate(parts):
                rows = np.flatnonzero(a[:, c].any(1))
                if len(rows):
                    valid[k] = True
                    y[k] = (ch - 1 - rows.max()) / ch          # lowest opaque row, from the bottom
            # only what touches the ground in the image (trunk base, foliage down to the bottom edge)
            # must not float; branches higher up may well hang over a slope
            valid &= y <= y[valid].min() + GROUND_BAND if valid.any() else valid
            occ = a.any(0)
            edges = np.linspace(0, cw, cols + 1).astype(int)
            reach = np.array([max(abs(e0 / cw - 0.5), abs(e1 / cw - 0.5))
                              for e0, e1 in zip(edges[:-1], edges[1:]) if occ[e0:e1].any()])
            out[(v, view)] = (u, y, valid, reach)
    return out


def card_gap(xy, bottom, along, w, h, prof):
    """How far the parts of cards standing at xy (n, 2), bottom edge at `bottom`, that touch the
    ground in the image float above the terrain (max over the slices; either face)."""
    u, yf, valid, _ = prof
    u, yf, valid = np.r_[u, -u], np.r_[yf, yf], np.r_[valid, valid]
    pts = xy[:, None, :] + along[:, None, :] * (u[None, :, None] * w[:, None, None])
    zc = bottom[:, None] + yf[None, :] * h[:, None]
    T = terrain_height(pts[..., 0], pts[..., 1])
    return np.where(valid[None, :], zc - T, -np.inf).max(1)


def intrudes(road, xy, along, w, prof):
    """True where an opaque column of the card (either face: the image and its mirror) comes within
    TREE_CLEAR of the asphalt."""
    reach = prof[3]
    if not len(xy) or not len(reach):
        return np.zeros(len(xy), bool)
    r = np.r_[reach, -reach]
    pts = (xy[:, None, :] + along[:, None, :] * (r[None, :, None] * w[:, None, None])).reshape(-1, 2)
    lat, st = road.locate(pts)
    j = np.where(st >= 0, st, 0)
    pen = np.where(st >= 0, road.hw[j] + TREE_CLEAR - np.abs(lat), -np.inf).reshape(len(xy), -1)
    return pen.max(1) > 0


def rotate(d, deg):
    c, s = np.cos(np.radians(deg)), np.sin(np.radians(deg))
    return np.stack([c * d[:, 0] - s * d[:, 1], s * d[:, 0] + c * d[:, 1]], 1)


def card_uv(meta, cell):
    cw, ch = meta["cell_px"]
    S = meta["sheet_px"]
    u0, v0 = (cell[0] * cw + 0.5) / S, (cell[1] * ch + 0.5) / S
    u1, v1 = ((cell[0] + 1) * cw - 0.5) / S, ((cell[1] + 1) * ch - 0.5) / S
    return np.array([[u0, v1], [u1, v1], [u1, v0], [u0, v0]])       # bottom-left, bottom-right, top-right, top-left


def quads(base, along, width, height, normal, uv):
    """Quads standing on `base` (n, 3), spanning `along` (n, 2 unit) by width x height, facing
    `normal` (n, 2 unit), counter-clockwise from the front; the image's u runs to the viewer's
    right (so the normal map's B = N x T points up). Returns pos, nrm, uv, tri."""
    n = len(base)
    right = np.stack([-normal[:, 1], normal[:, 0]], 1)                  # up x normal
    along = along * np.where(np.einsum("ij,ij->i", along, right) < 0, -1.0, 1.0)[:, None]
    a3 = np.concatenate([along, np.zeros((n, 1))], 1) * (width / 2)[:, None]
    up = np.zeros((n, 3))
    up[:, 2] = height
    corners = np.stack([base - a3, base + a3, base + a3 + up, base - a3 + up], 1)   # (n, 4, 3)
    f = np.concatenate([normal, np.zeros((n, 1))], 1)
    nv = 0.45 * f + np.array([0, 0, 0.9])
    nv /= np.linalg.norm(nv, axis=1, keepdims=True)
    uvq = np.broadcast_to(uv, (n, 4, 2)).copy()
    # winding: counter-clockwise seen from `normal` (u0 left, u1 right as the viewer sees it)
    e1 = corners[:, 1] - corners[:, 0]
    e2 = corners[:, 2] - corners[:, 0]
    flip = np.einsum("ij,ij->i", np.cross(e1, e2), f) < 0
    corners[flip] = corners[flip][:, [1, 0, 3, 2]]
    uvq[flip] = uvq[flip][:, [1, 0, 3, 2]]
    pos = corners.reshape(-1, 3)
    tri = (np.arange(n)[:, None, None] * 4 + np.array([[0, 1, 2], [0, 2, 3]])[None]).reshape(-1, 3)
    return pos, np.repeat(nv, 4, 0), uvq.reshape(-1, 2), tri


def top_reach(tops):
    """{variant: radius of the opaque crown in the top view, as a fraction of half the card}."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    sheet = np.asarray(Image.open(CARDS / "tree_tops.png"))[..., 3]
    c = tops["cell_px"]
    out = {}
    for v, vm in tops["variants"].items():
        cx, cy = vm["cell"]
        a = sheet[cy * c:(cy + 1) * c, cx * c:(cx + 1) * c] > ALPHA_REF * 255
        yy, xx = np.nonzero(a)
        out[v] = float(np.hypot(xx - c / 2, yy - c / 2).max() / (c / 2)) if len(xx) else 0.0
    return out


def hquads(centre, width, yaw, cell, tops):
    """Horizontal canopy cards (top face and bottom face) centred at `centre` (n, 3), side `width`,
    turned by `yaw`. The image's top towards the card's +y, u to its +x (B = N x T up the image)."""
    c = tops["cell_px"]
    S = tops["sheet_px"]
    u0, v0 = (cell[0] * c + 0.5) / S, (cell[1] * c + 0.5) / S
    u1, v1 = ((cell[0] + 1) * c - 0.5) / S, ((cell[1] + 1) * c - 0.5) / S
    n = len(centre)
    ex = np.stack([np.cos(yaw), np.sin(yaw), np.zeros(n)], 1) * (width / 2)[:, None]
    ey = np.stack([-np.sin(yaw), np.cos(yaw), np.zeros(n)], 1) * (width / 2)[:, None]
    BL, BR, TR, TL = centre - ex - ey, centre + ex - ey, centre + ex + ey, centre - ex + ey
    top = np.stack([BL, BR, TR, TL], 1)
    bot = np.stack([BR, BL, TL, TR], 1)                          # mirrored in u, seen from below
    uv_t = np.array([[u0, v1], [u1, v1], [u1, v0], [u0, v0]])
    uv_b = np.array([[u0, v1], [u1, v1], [u1, v0], [u0, v0]])
    pos = np.concatenate([top, bot], 0).reshape(-1, 3)
    nrm = np.concatenate([np.tile([0.0, 0.0, 1.0], (4 * n, 1)), np.tile([0.0, 0.0, -1.0], (4 * n, 1))])
    uv = np.concatenate([np.broadcast_to(uv_t, (n, 4, 2)), np.broadcast_to(uv_b, (n, 4, 2))]).reshape(-1, 2)
    tri = (np.arange(2 * n)[:, None, None] * 4 + np.array([[0, 1, 2], [0, 2, 3]])[None]).reshape(-1, 3)
    return pos, nrm, uv, tri


def main():
    meta = json.loads((CARDS / "tree_cards.json").read_text())
    tops = json.loads((CARDS / "tree_tops.json").read_text()) if (CARDS / "tree_tops.json").exists() else None
    reach_top = top_reach(tops) if tops else {}
    top_groups = defaultdict(list)
    n_full = n_canopy = n_canopy_small = 0
    names = sorted(meta["variants"])
    d = np.load(OUTPUT / "forest_instances.npz")
    types, data = d["types"], d["data"].astype(np.float64)
    keep = np.array([t in SPECIES for t in types])
    types, data = types[keep], data[keep]
    x, y, yaw, scale = data[:, 0], data[:, 1], data[:, 3], np.clip(data[:, 5], 0.7, 1.3)
    z = terrain_height(x, y)        # the AC terrain is coarser than the 1 m DEM away from the road
    road = Road()
    dist, near_i = road.tree.query(np.stack([x, y], 1))
    lat, st = road.locate(np.stack([x, y], 1))
    j = np.where(st >= 0, st, 0)
    vw = np.where(lat >= 0, road.verge[1][j], road.verge[-1][j])
    on_road = (st >= 0) & (np.abs(lat) < road.hw[j] + vw + 0.6)
    u = _hash(x, y)
    thin = (dist >= FAR_M) & (_hash(y, x) < 0.5)
    import ac_vineyards
    vine_rows = (types == "grape_vine_group") & (dist < ac_vineyards.NEAR_M + 35)    # rows in 3D there
    ok = ~on_road & ~thin & ~vine_rows
    print(f"trees: {len(types):,} instances, {on_road.sum()} on the road/verge dropped, {thin.sum():,} thinned far away, "
          f"{vine_rows.sum():,} vine cards replaced by 3D rows")
    kind = np.array([SPECIES[t][0] for t in types])
    base_h = np.array([SPECIES[t][1] for t in types])
    variant = np.empty(len(types), object)
    for k in ("conifer", "broadleaf", "shrub"):
        m = kind == k
        variant[m] = variants_for(k, z[m], u[m], names)
    height = base_h * scale * (0.9 + 0.2 * _hash(x + 1.7, y))
    toward = road.xy[near_i] - np.stack([x, y], 1)
    toward /= np.maximum(np.linalg.norm(toward, axis=1, keepdims=True), 1e-6)

    # Custom Shaders Patch 3D trees near the road
    raw = Config().raw
    models = {}
    if raw.get("assetto_corsa", {}).get("csp_trees") and (CSP_DIR / "models.json").exists():
        models = json.loads((CSP_DIR / "models.json").read_text())
    csp_r = float(raw.get("studio", {}).get("detail", {}).get("tree_mesh_radius_m", 60))
    csp = np.zeros(len(types), bool)
    csp_width = np.ones(len(types))
    csp_model = np.array([CSP_MODEL.get(v, v) for v in variant], object)
    gap_edge = np.where(st >= 0, np.abs(lat), dist) - road.hw[np.where(st >= 0, st, near_i)]
    for name_m, mm in models.items():
        m = (csp_model == name_m) & ok & (dist < csp_r)
        crown = mm["radius_m"] / mm["height_m"] * height[m]
        f = (gap_edge[m] - TREE_CLEAR) / np.maximum(crown, 1e-3)
        csp[m] = f >= CSP_MIN_WIDTH
        csp_width[m] = np.clip(f, CSP_MIN_WIDTH, 1.0)

    profiles = card_profiles(meta)
    groups = defaultdict(list)                       # (tile, lod) -> list of (pos, nrm, uv, tri)
    n_sunk = n_contour = n_dropped = n_parallel = n_turned = n_shrunk = n_clear_drop = 0
    kept = np.zeros(len(types), bool)
    for v in names:
        vm = meta["variants"][v]
        sel = np.flatnonzero(ok & (variant == v))
        if not len(sel):
            continue
        s = height[sel] / vm["height_m"]
        w, h = vm["card_w_m"] * s, vm["card_h_m"] * s
        xy = np.stack([x[sel], y[sel]], 1)
        lod = np.select([dist[sel] < NEAR_M, dist[sel] < MID_M], [0, 1], 2)
        a = np.stack([np.cos(yaw[sel]), np.sin(yaw[sel])], 1)       # front card along the yaw
        b = np.stack([-a[:, 1], a[:, 0]], 1)
        t = toward[sel]
        tr = np.stack([-t[:, 1], t[:, 0]], 1)                       # road-facing card plane
        pf, ps = profiles[(v, "front")], profiles[(v, "side")]
        # near the road: crossed cards, else one card parallel to the road, turned, smaller, or none
        close = (lod == 0) & (dist[sel] < road.hw.max() + TREE_CLEAR + 0.5 * w)    # w: this tree's card width
        crossed = (lod == 0).copy()
        single = np.zeros(len(sel), bool)
        pdir = tr.copy()
        sf = np.ones(len(sel))
        gone = np.zeros(len(sel), bool)
        c = np.flatnonzero(close)
        if len(c):
            bad = intrudes(road, xy[c], a[c], w[c], pf) | intrudes(road, xy[c], b[c], w[c], ps)
            crossed[c[bad]] = False
            todo = c[bad]
            fit = ~intrudes(road, xy[todo], tr[todo], w[todo], pf)
            single[todo[fit]] = True
            n_parallel += int(fit.sum())
            todo = todo[~fit]
            for deg in TURNS:
                if not len(todo):
                    break
                dr = rotate(tr[todo], deg)
                fit = ~intrudes(road, xy[todo], dr, w[todo], pf)
                single[todo[fit]] = True
                pdir[todo[fit]] = dr[fit]
                n_turned += int(fit.sum())
                todo = todo[~fit]
            for f_ in SHRINK:
                if not len(todo):
                    break
                fit = ~intrudes(road, xy[todo], tr[todo], w[todo] * f_, pf)
                single[todo[fit]] = True
                sf[todo[fit]] = f_
                n_shrunk += int(fit.sum())
                todo = todo[~fit]
            gone[todo] = True
            n_clear_drop += len(todo)
        # FULL trees: two more planes at 45 / 135 deg where they keep clear of the road too
        d1, d2 = rotate(a, 45), rotate(a, 135)
        full = crossed & (dist[sel] < FULL_M)
        cf = np.flatnonzero(full & close)
        if len(cf):
            full[cf[intrudes(road, xy[cf], d1[cf], w[cf], pf) | intrudes(road, xy[cf], d2[cf], w[cf], ps)]] = False
        w, h = w * sf, h * sf
        hgt = height[sel] * sf
        bottom = z[sel] - SINK_M - 0.02 * hgt
        g_a = card_gap(xy, bottom, a, w, h, pf)
        g_b = card_gap(xy, bottom, b, w, h, ps)
        g_t = card_gap(xy, bottom, pdir, w, h, pf)
        g_d = np.full(len(sel), -np.inf)
        if full.any():
            g_d[full] = np.maximum(card_gap(xy[full], bottom[full], d1[full], w[full], h[full], pf),
                                   card_gap(xy[full], bottom[full], d2[full], w[full], h[full], ps))
        gap = np.where(crossed, np.maximum(np.maximum(g_a, g_b), g_d), g_t)
        cap = 0.3 * hgt
        # too steep for its card: one card along the contour line (level ground across it), else drop
        contour = (gap + 0.03 > cap) & ~gone
        cdir = pdir.copy()
        if contour.any():
            cx, cy = xy[contour, 0], xy[contour, 1]
            gx = terrain_height(cx + 1, cy) - terrain_height(cx - 1, cy)
            gy = terrain_height(cx, cy + 1) - terrain_height(cx, cy - 1)
            gl = np.hypot(gx, gy)
            cd = np.where(gl[:, None] > 1e-6, np.stack([-gy, gx], 1) / np.maximum(gl, 1e-6)[:, None], pdir[contour])
            g_c = card_gap(xy[contour], bottom[contour], cd, w[contour], h[contour], pf)
            fits = (g_c + 0.03 <= cap[contour]) & ~intrudes(road, xy[contour], cd, w[contour], pf)
            cdir[contour] = cd
            gap[contour] = np.where(fits, g_c, np.inf)
            n_contour += int(fits.sum())
            n_dropped += int((~fits).sum())
        gone |= ~np.isfinite(gap)
        crossed &= ~contour & ~gone
        full &= crossed
        extra = np.clip(np.where(gone, 0, gap) + 0.03, 0, cap)
        n_sunk += int(((gap > 0) & ~gone).sum())
        base = np.concatenate([xy, (bottom - extra)[:, None]], 1)
        tile = np.floor(xy / TILE_M).astype(int)
        cs = csp[sel]
        uv_f, uv_s = card_uv(meta, vm["cells"][0]), card_uv(meta, vm["cells"][1])
        facing = (((lod == 1) | ((lod == 0) & ~crossed)) & ~contour & ~gone)
        dn = np.stack([-pdir[:, 1], pdir[:, 0]], 1)                     # normal of the single card plane
        cn = np.stack([-cdir[:, 1], cdir[:, 0]], 1)
        on_c = contour & ~gone
        far2 = (lod == 2) & ~contour & ~gone
        kept[sel] = ~gone
        cards = [(crossed, a, b, uv_f, 0), (crossed, a, -b, uv_f, 0), (crossed, b, -a, uv_s, 0), (crossed, b, a, uv_s, 0),
                 (facing, pdir, dn, uv_f, None), (facing, pdir, -dn, uv_f, None), (far2, tr, t, uv_f, 2),
                 (on_c, cdir, cn, uv_f, None), (on_c, cdir, -cn, uv_f, None),
                 (full, d1, d2, uv_f, 3), (full, d1, -d2, uv_f, 3), (full, d2, -d1, uv_s, 3), (full, d2, d1, uv_s, 3)]
        n_full += int(full.sum())
        # canopy layers of the FULL trees: the top view at two heights, kept TREE_CLEAR off the asphalt
        kind_v = vm["kind"] if vm["kind"] in CANOPY else ("conifer" if vm["kind"] == "pine" else "broadleaf")
        if tops and v in tops["variants"] and full.any() and CANOPY.get(kind_v):
            tv = tops["variants"][v]
            fi = np.flatnonzero(full)
            lt, stt = road.locate(xy[fi])
            jt = np.where(stt >= 0, stt, near_i[sel][fi])
            gap_edge = np.where(stt >= 0, np.abs(lt), dist[sel][fi]) - road.hw[jt]
            for k_layer, (fh, fs) in enumerate(CANOPY[kind_v]):
                W = tv["top_w_m"] * (hgt[fi] / tv["height_m"]) * fs
                room = 2 * (gap_edge - TREE_CLEAR) / max(reach_top.get(v, 1.0), 1e-3)
                small = room < W
                W = np.minimum(W, room)
                okc = W >= 0.4 * tv["top_w_m"] * (hgt[fi] / tv["height_m"]) * fs
                n_canopy += int(okc.sum())
                n_canopy_small += int((okc & small).sum())
                if not okc.any():
                    continue
                cen = np.c_[xy[fi][okc], z[sel][fi][okc] + fh * hgt[fi][okc]]
                P, N, U, T = hquads(cen, W[okc], yaw[sel][fi][okc] + 0.7 * k_layer, tv["cell"], tops)
                tk = tile[fi][okc]
                ck = cs[fi][okc]
                for key in {(int(q[0]), int(q[1]), bool(c_)) for q, c_ in zip(tk, ck)}:
                    idx = np.flatnonzero(np.all(tk == key[:2], axis=1) & (ck == key[2]))
                    m_ = len(okc.nonzero()[0])
                    idx2 = np.r_[idx, idx + m_]                         # top and bottom faces
                    vi = (idx2[:, None] * 4 + np.arange(4)).ravel()
                    ti = (idx2[:, None] * 2 + np.arange(2)).ravel()
                    remap = np.full(len(P), -1)
                    remap[vi] = np.arange(len(vi))
                    top_groups[key].append((P[vi], N[vi], U[vi], remap[T[ti]]))
        for m, along, nrm, uvv, lodv in cards:
            if not m.any():
                continue
            P, N, U, T = quads(base[m], along[m], w[m], h[m], nrm[m], uvv)
            lv = np.full(m.sum(), lodv) if lodv is not None else lod[m]
            tk = tile[m]
            ck = cs[m]
            for key in {(int(q[0]), int(q[1]), int(l), bool(c_)) for q, l, c_ in zip(tk, lv, ck)}:
                idx = np.flatnonzero(np.all(tk == key[:2], axis=1) & (lv == key[2]) & (ck == key[3]))
                vi = (idx[:, None] * 4 + np.arange(4)).ravel()
                ti = (idx[:, None] * 2 + np.arange(2)).ravel()
                remap = np.full(len(P), -1)
                remap[vi] = np.arange(len(vi))
                groups[(key[:2], key[2], key[3])].append((P[vi], N[vi], U[vi], remap[T[ti]]))
    print(f"trees: clear road: {n_parallel:,} turned parallel to it, {n_turned:,} turned a little more, "
          f"{n_shrunk:,} made smaller, {n_clear_drop:,} left out; {n_sunk:,} sunk further so nothing touching "
          f"the ground floats; {n_contour:,} on very steep ground turned along the contour, {n_dropped:,} dropped")
    meshes = []
    for (key, lodv, fb), parts in sorted(groups.items()):
        off, tris = 0, []
        for p in parts:
            tris.append(p[3] + off)
            off += len(p[0])
        meshes.append(dict(name=f"trees_{'cspfb_' if fb else ''}{lodv}_{key[0]}_{key[1]}", material="tree_cards", surface=None,
                           pos=np.concatenate([p[0] for p in parts]), nrm=np.concatenate([p[1] for p in parts]),
                           uv=np.concatenate([p[2] for p in parts]), tri=np.concatenate(tris),
                           cast_shadows=lodv in (0, 3), **({"lod_out": FULL_LOD} if lodv == 3 else {})))
    for key, parts in sorted(top_groups.items()):
        off, tris = 0, []
        for p in parts:
            tris.append(p[3] + off)
            off += len(p[0])
        meshes.append(dict(name=f"treetops_{'cspfb_' if key[2] else ''}{key[0]}_{key[1]}", material="tree_tops", surface=None,
                           pos=np.concatenate([p[0] for p in parts]), nrm=np.concatenate([p[1] for p in parts]),
                           uv=np.concatenate([p[2] for p in parts]), tri=np.concatenate(tris),
                           cast_shadows=True, lod_out=FULL_LOD))
    print(f"trees: {n_full:,} near the road with four planes, {n_canopy:,} canopy layers "
          f"({n_canopy_small:,} made smaller to keep off the road)")
    # collision posts for trunks near the road (hexagonal prisms, invisible)
    near = ok & kept & (dist < TRUNK_M) & (kind != "shrub")
    cx, cy, cz = x[near], y[near], z[near]
    ang = np.arange(6) * np.pi / 3
    ring = np.stack([np.cos(ang), np.sin(ang)], 1) * 0.3
    P, T = [], []
    for k in range(len(cx)):
        b0 = len(P)
        for q in ring:
            P.append([cx[k] + q[0], cy[k] + q[1], cz[k] - 0.5])
            P.append([cx[k] + q[0], cy[k] + q[1], cz[k] + 3.0])
        for e in range(6):
            i0, i1 = b0 + 2 * e, b0 + 2 * ((e + 1) % 6)
            T += [[i0, i1, i1 + 1], [i0, i1 + 1, i0 + 1]]
    if P:
        P = np.asarray(P, float)
        T = np.asarray(T)
        meshes.append(dict(name="1WALL_trunks", material="tree_cards", surface="WALL", pos=P,
                           nrm=np.tile([0.0, 0.0, 1.0], (len(P), 1)), uv=np.zeros((len(P), 2)), tri=T,
                           renderable=False, cast_shadows=False))
    materials = {"tree_cards": {"shader": "ksPerPixelAT_NM", "alpha_tested": True,
                                "textures": [["txDiffuse", str(CARDS / "tree_cards.png"), "DXT5"],
                                             ["txNormal", str(CARDS / "tree_cards_nm.png"), "DXT1"]],
                                "props": {"ksAmbient": 0.5, "ksDiffuse": 0.5, "ksSpecular": 0.02,
                                          "ksSpecularEXP": 4.0, "ksAlphaRef": ALPHA_REF, "nmObjectSpace": 0.0}}}
    if tops:
        materials["tree_tops"] = {"shader": "ksPerPixelAT_NM", "alpha_tested": True,
                                  "textures": [["txDiffuse", str(CARDS / "tree_tops.png"), "DXT5"],
                                               ["txNormal", str(CARDS / "tree_tops_nm.png"), "DXT1"]],
                                  "props": materials["tree_cards"]["props"]}
    meta_out = save_part("trees", meshes, materials)
    if models:
        sel3 = np.flatnonzero(csp & ok & kept)
        CSP_DIR.mkdir(parents=True, exist_ok=True)
        (CSP_DIR / "instances.json").write_text(json.dumps({"radius_m": csp_r, "trees": [
            {"x": round(float(x[i]), 2), "y": round(float(y[i]), 2), "z": round(float(z[i]), 2), "variant": str(csp_model[i]),
             "height_m": round(float(height[i]), 2), "angle_deg": round(float(np.degrees(yaw[i]) % 360), 1),
             "width": round(float(csp_width[i]), 2)} for i in sel3]}))
        near3 = ok & kept & (dist < csp_r)
        print(f"trees: {len(sel3):,} of the {near3.sum():,} within {csp_r:.0f} m of the road are 3D in CSP "
              f"({(csp_width[sel3] < 1).sum():,} with a narrowed crown); the others keep their cards "
              f"(no model for the variant: {(near3 & ~np.isin(csp_model, list(models))).sum():,}; too close to the asphalt: "
              f"{(near3 & np.isin(csp_model, list(models)) & ~csp).sum():,})")
    nv = sum(m["verts"] for m in meta_out)
    print(f"ac_trees: {(ok & kept).sum():,} trees ({(ok & kept & (dist < NEAR_M)).sum():,} near the road), "
          f"{near.sum():,} collision trunks; {len(meta_out)} meshes, {nv:,} vertices")


if __name__ == "__main__":
    main()
