#!/usr/bin/env python3
"""Vineyard rows for the Assetto Corsa track, in 3D (as bush cards alone the vineyards looked like
flat fields).

Rows: vineyards.py's parcels and rows (OSM / GeoDBT vineyards in ~70 m parcels, rows
ROW_SPACING apart along the slope contours, kept off roads and buildings), within NEAR_M of the
climb, off the AC road (wider than the measured road) by ROAD_CLEAR.
Each row follows the AC terrain mesh (a station every STEP m) and is
  * a leafy wall: two cards LEAF_HALF either side of the row from LEAF_Z0 to LEAF_Z1 above the
    ground and a cap on top (alpha-tested leaves, ac_textures.make_vine_texture); within INNER_M of
    the climb also the cards' inner faces (seen through the gaps), drawn to INNER_LOD m; normals
    lean upward so the wall lights like a canopy;
  * within POST_M of the climb, the wooden posts every POST_EVERY m (2.1 m, timber).
Meshes per TILE_M tile; leaves stop drawing past LEAF_LOD m, posts past POST_LOD m.
Visual only (no physics). ac_trees.py leaves out the bush cards inside these vineyards.
Output: output/ac/vines.npz + vines.json.
"""

import json

import numpy as np
from collections import defaultdict

import ac_textures as tx
import vineyards as V
from ac_track import AC_DIR, CROWN, TEX_DIR, Road, _flat, _mesh, box, save_part
from ac_trees import terrain_height
from common import OUTPUT, Config, Heightfield

NEAR_M = 450.0
INNER_M = 150.0               # rows this close to the climb also get the inner faces (seen through the gaps)
POST_M = 120.0
ROAD_CLEAR = 1.8              # m between a row and the AC asphalt edge
STEP = 3.0
LEAF_HALF, LEAF_Z0, LEAF_Z1 = 0.28, 0.5, 1.85
POST_EVERY = 6.0
TILE_M = 128.0
LEAF_LOD, INNER_LOD, POST_LOD = 1100.0, 250.0, 220.0
LEAF_U_M = 2.0                # metres of row per texture repeat


def vineyard_rows(cfg, hf):
    grad = np.gradient(hf.z, cfg.mpp)
    grad = (grad[1], -grad[0])
    rows = []
    for geom, direction, phase in V.parcels(cfg, hf, grad):
        rows += V.rows_in(geom, direction, phase)
    return rows


def row_stations(line, road):
    """Stations every STEP m along a straight row (n, 2), cut where the AC road is too close."""
    a, b = line[0], line[-1]
    L = float(np.hypot(*(b - a)))
    k = max(int(L / STEP), 1)
    t = np.linspace(0, 1, k + 1)
    P = a + (b - a) * t[:, None]
    lat, st = road.locate(P)
    j = np.where(st >= 0, st, 0)
    near = (st >= 0) & (np.abs(lat) < road.hw[j] + road.verge[1][j] + ROAD_CLEAR)
    d, _ = road.tree.query(P)
    near |= d < road.hw.max() + ROAD_CLEAR - 1.0
    runs, cur = [], []
    for p, bad in zip(P, near):
        if bad:
            if len(cur) >= 2:
                runs.append(np.asarray(cur))
            cur = []
        else:
            cur.append(p)
    if len(cur) >= 2:
        runs.append(np.asarray(cur))
    return runs


def leaf_wall(P, z, faces):
    """One row's leafy wall: faces "outer" (the two side cards seen from outside, and the cap) or
    "inner" (the side cards' inner faces, seen through the gaps): (pos, nrm, uv, tri)."""
    d = np.gradient(P, axis=0)
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    n = np.stack([-d[:, 1], d[:, 0]], 1)
    s = np.r_[0, np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))] / LEAF_U_M
    m = len(P)
    pos, nrm, uv, tri = [], [], [], []
    off = 0

    def strip(A, B, NA, ua, va, vb, flip):
        nonlocal off
        pos.append(np.concatenate([A, B]))
        nrm.append(np.concatenate([NA, NA]))
        uv.append(np.concatenate([np.c_[ua, np.full(m, va)], np.c_[ua, np.full(m, vb)]]))
        k = np.arange(m - 1)
        t = np.concatenate([np.stack([k, k + 1, m + k + 1], 1), np.stack([k, m + k + 1, m + k], 1)])
        tri.append((t[:, [0, 2, 1]] if flip else t) + off)
        off += 2 * m

    up = np.array([0.0, 0.0, 1.0])
    for side in (1, -1):
        e = P + n * side * LEAF_HALF
        lo = np.c_[e, z + LEAF_Z0]
        hi = np.c_[e, z + LEAF_Z1]
        out = np.c_[n * side, np.zeros(m)]
        lean = out * 0.45 + up
        lean /= np.linalg.norm(lean, axis=1, keepdims=True)
        inward = np.c_[-n * side, np.zeros(m)] * 0.45 + up
        inward /= np.linalg.norm(inward, axis=1, keepdims=True)
        u = s if side > 0 else -s                                  # the image's right to each viewer's right
        # AC's v grows down the image: the image's top row at the top of the wall
        if faces == "outer":
            strip(lo, hi, lean, u, 0.8, 0.0, side < 0)
        else:
            strip(lo, hi, inward, -u, 0.8, 0.0, side > 0)
    if faces == "outer":
        cap_l = np.c_[P + n * LEAF_HALF, z + LEAF_Z1]
        cap_r = np.c_[P - n * LEAF_HALF, z + LEAF_Z1]
        strip(cap_r, cap_l, np.tile(up, (m, 1)), s, 0.82, 1.0, False)  # the image's bottom fifth: seen from above
    pos, nrm, uv, tri = (np.concatenate(a) for a in (pos, nrm, uv, tri))
    # counter-clockwise as seen from the side the normal points to
    fn = np.cross(pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]])
    bad = np.einsum("ij,ij->i", fn, nrm[tri[:, 0]]) < 0
    tri[bad] = tri[bad][:, [0, 2, 1]]
    return pos, nrm, uv, tri


def main():
    cfg = Config()
    hf = Heightfield(cfg, OUTPUT / "dem_final.tif")
    road = Road()
    rows = vineyard_rows(cfg, hf)
    leaves = defaultdict(list)
    inner = defaultdict(list)
    posts = defaultdict(list)
    n_rows, length, n_posts = 0, 0.0, 0
    for line in rows:
        mid = (line[0] + line[-1]) / 2
        d, _ = road.tree.query(np.r_[line[0:1], line[-1:], mid[None]])
        if d.min() > NEAR_M:
            continue
        for P in row_stations(line, road):
            z = terrain_height(P[:, 0], P[:, 1])
            tile = tuple(np.floor(P.mean(0) / TILE_M).astype(int))
            leaves[tile].append(leaf_wall(P, z, "outer"))
            dmin = float(road.tree.query(P)[0].min())
            if dmin < INNER_M:
                inner[tile].append(leaf_wall(P, z, "inner"))
            n_rows += 1
            L = float(np.sum(np.linalg.norm(np.diff(P, axis=0), axis=1)))
            length += L
            if dmin < POST_M:
                dirn = (P[-1] - P[0]) / max(np.linalg.norm(P[-1] - P[0]), 1e-6)
                for u in np.arange(0.5, L, POST_EVERY):
                    q = P[0] + dirn * u
                    zq = float(terrain_height(q[0], q[1]))
                    B, T = box(q, dirn, 0.04, 0.04, zq - 0.3, zq + 2.1)
                    m = _flat(_mesh("post", "vine_post", None, B, T, np.zeros((len(B), 2))))
                    m["uv"] = np.c_[(m["pos"][:, 0] + m["pos"][:, 1]) * 2.0, m["pos"][:, 2]]
                    posts[tile].append(m)
                    n_posts += 1
    meshes = []
    for prefix, groups, lod in (("vines", leaves, LEAF_LOD), ("vinesin", inner, INNER_LOD)):
        for (i, j), parts in sorted(groups.items()):
            off, P, N, U, T = 0, [], [], [], []
            for pos, nrm, uv, tri in parts:
                P.append(pos); N.append(nrm); U.append(uv); T.append(tri + off)
                off += len(pos)
            meshes.append(dict(name=f"{prefix}_{i}_{j}", material="vine_leaves", surface=None, pos=np.concatenate(P),
                               nrm=np.concatenate(N), uv=np.concatenate(U), tri=np.concatenate(T), lod_out=lod,
                               cast_shadows=prefix == "vines"))
    for (i, j), ms in sorted(posts.items()):
        off, P, N, U, T = 0, [], [], [], []
        for m in ms:
            P.append(m["pos"]); N.append(m["nrm"]); U.append(m["uv"]); T.append(m["tri"] + off)
            off += len(m["pos"])
        meshes.append(dict(name=f"vineposts_{i}_{j}", material="vine_post", surface=None, pos=np.concatenate(P),
                           nrm=np.concatenate(N), uv=np.concatenate(U), tri=np.concatenate(T), lod_out=POST_LOD,
                           cast_shadows=True))
    if not (TEX_DIR / "vine_leaves.png").exists() or (TEX_DIR / "vine_leaves.png").stat().st_mtime < __import__("os").path.getmtime(tx.__file__):
        tx.make_vine_texture()
    if not (TEX_DIR / "timber.png").exists():
        tx.make_timber_texture()
    materials = {
        "vine_leaves": {"shader": "ksPerPixelAT", "alpha_tested": True,
                        "props": {"ksAmbient": 0.5, "ksDiffuse": 0.55, "ksSpecular": 0.03, "ksSpecularEXP": 6.0,
                                  "ksAlphaRef": 0.45},
                        "textures": [["txDiffuse", "vine_leaves.png", "DXT5"]]},
        "vine_post": {"shader": "ksPerPixel", "props": {"ksAmbient": 0.5, "ksDiffuse": 0.75, "ksSpecular": 0.03, "ksSpecularEXP": 6.0},
                      "textures": [["txDiffuse", "timber.png", "DXT1"]]},
    }
    meta = save_part("vines", meshes, materials)
    print(f"ac_vineyards: {n_rows:,} rows within {NEAR_M:.0f} m of the climb, {length / 1000:.1f} km of vines, "
          f"{n_posts:,} posts; {len(meta)} meshes, {sum(m['tris'] for m in meta):,} triangles")
    # for ac_trees: the vineyards that now have rows (their bush cards are left out)
    centres = np.array([((r[0] + r[-1]) / 2) for r in rows]) if rows else np.zeros((0, 2))
    np.save(AC_DIR / "vine_row_centres.npy", centres)


if __name__ == "__main__":
    main()
