#!/usr/bin/env python3
"""Audit of the Assetto Corsa track parts (output/ac/*.npz/json) along the climb.

1. Obstacles: any triangle of a physics mesh (terrain GRASS, WALL: walls, rails, parapets,
   invisible collision for buildings and trunks) reaching into the space a car drives through:
   over the carriageway (|lateral| < half-width - 0.1 m) between 4 cm and 2.5 m above the asphalt.
   Terrain poking through the asphalt counts from 2 cm.
2. Clutter: visible geometry (buildings, landmarks, signs) in the same space (tree cards are
   checked by ac_trees, which knows where their images are transparent).
Points are sampled on every triangle that can reach within REACH_M of the centreline (vertices, edge midpoints,
centroid, six interior points). Prints findings grouped by route metre.
"""

import json
from collections import defaultdict

import numpy as np

from ac_track import AC_DIR, CROWN, Road

REACH_M = 12.0
PARTS = ("geometry", "scenery", "trees", "vines")
BARY = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [.5, .5, 0], [0, .5, .5], [.5, 0, .5], [1 / 3] * 3,
                 [2 / 3, 1 / 6, 1 / 6], [1 / 6, 2 / 3, 1 / 6], [1 / 6, 1 / 6, 2 / 3],
                 [.8, .1, .1], [.1, .8, .1], [.1, .1, .8]])


def kind_of(name):
    if name.startswith("road_lines"):
        return "paint"                                  # the lane markings, 1.2 cm over the asphalt
    if "WALL" in name:
        return "wall" if not any(k in name for k in ("bldcol", "trunks")) else ("building collision" if "bldcol" in name else "trunk collision")
    if name[:1].isdigit() and "GRASS" in name:
        return "terrain"
    if name[:1].isdigit() and "ROAD" in name:
        return "road"
    return "visual"


def samples(pos, tri):
    P = pos[tri]                                        # (m, 3, 3)
    return np.einsum("sk,mkd->msd", BARY, P)            # (m, s, 3)


def past_ends(road, pts):
    """Points beyond the first or last station (where the asphalt ends; the street may go on)."""
    a = ((pts - road.xy[0]) @ road.t[0] < -0.05) & (np.linalg.norm(pts - road.xy[0], axis=1) < 12)
    b = ((pts - road.xy[-1]) @ road.t[-1] > 0.05) & (np.linalg.norm(pts - road.xy[-1], axis=1) < 12)
    return a | b


def audit(road, parts=PARTS, verbose=True):
    hits = defaultdict(list)                            # (kind, route bucket) -> (h, mesh)
    for part in parts:
        meta = json.loads((AC_DIR / f"{part}.json").read_text())
        G = np.load(AC_DIR / f"{part}.npz")
        for k, m in enumerate(meta["meshes"]):
            kind = kind_of(m["name"])
            if kind in ("road", "paint") or (kind == "visual" and m.get("renderable") is False):
                continue
            if kind == "visual" and m["name"].startswith(("trees_", "treetops_")):
                continue                                    # canopy over the road: checked by ac_trees
            pos, tri = G[f"m{k}_pos"], G[f"m{k}_tri"]
            Pt = pos[tri]
            c = Pt.mean(1)
            rad = np.linalg.norm(Pt - c[:, None], axis=2).max(1)
            d, _ = road.tree.query(c[:, :2])
            near = d < REACH_M + rad                        # long triangles too
            if not near.any():
                continue
            S = samples(pos, tri[near]).reshape(-1, 3)
            lat, st = road.locate(S[:, :2])
            j = np.where(st >= 0, st, 0)
            inside = (st >= 0) & (np.abs(lat) < road.hw[j] - 0.1) & ~past_ends(road, S[:, :2])
            h = S[:, 2] - (road.z[j] - CROWN * np.abs(lat))
            lo = 0.02 if kind == "terrain" else 0.04
            bad = inside & (h > lo) & (h < 2.5)
            for i in np.flatnonzero(bad):
                hits[(kind, int(road.route_m[j[i]] // 10 * 10))].append((float(h[i]), m["name"]))
    if verbose:
        if not hits:
            print("audit: nothing in the driving space over the carriageway")
        for (kind, rm), v in sorted(hits.items(), key=lambda kv: kv[0][1]):
            hmax, name = max(v)
            print(f"  {rm:6d} m  {kind:18s} {len(v):4d} samples, up to {hmax:.2f} m above the asphalt ({name})")
    return hits


def audit_tree_cards(road, clear=0.3, reach=20.0, grid=24):
    """Opaque pixels of tree cards (sampled on a grid x grid lattice over every card near the road,
    looked up in the card sheet's alpha) within `clear` m of the asphalt edge, at any height."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    meta = json.loads((AC_DIR / "trees.json").read_text())
    G = np.load(AC_DIR / "trees.npz")
    sheets = {"trees_": np.asarray(Image.open(AC_DIR / "trees" / "tree_cards.png"))[..., 3]}
    if (AC_DIR / "trees" / "tree_tops.png").exists():                  # canopy layers of the near trees
        sheets["treetops_"] = np.asarray(Image.open(AC_DIR / "trees" / "tree_tops.png"))[..., 3]
    ref = meta["materials"]["tree_cards"]["props"]["ksAlphaRef"] * 255
    s, t = np.meshgrid((np.arange(grid) + 0.5) / grid, (np.arange(grid) + 0.5) / grid)
    s, t = s.ravel(), t.ravel()
    w = np.stack([(1 - s) * (1 - t), s * (1 - t), s * t, (1 - s) * t], 1)        # corners 0, 1, 2, 3
    bad, cards = 0, 0
    where = defaultdict(int)
    for k, m in enumerate(meta["meshes"]):
        prefix = next((p_ for p_ in sheets if m["name"].startswith(p_)), None)
        if prefix is None:
            continue
        alpha = sheets[prefix]
        S = alpha.shape[0]
        pos, uv, tri = G[f"m{k}_pos"], G[f"m{k}_uv"], G[f"m{k}_tri"]
        t0, t1 = tri[:-1], tri[1:]                          # a card is two triangles (0 1 2) (0 2 3)
        pair = (t0[:, 0] == t1[:, 0]) & (t0[:, 2] == t1[:, 1])
        pair[1:] &= ~pair[:-1]
        cq = np.c_[t0[pair], t1[pair][:, 2]]
        q, quv = pos[cq], uv[cq]
        d, _ = road.tree.query(q[:, :, :2].mean(1))
        near = d < reach
        if not near.any():
            continue
        q, quv = q[near], quv[near]
        cards += len(q)
        P = np.einsum("sc,nck->nsk", w, q)                 # (n, grid^2, 3)
        U = np.einsum("sc,nck->nsk", w, quv)
        px = np.clip((U[..., 0] * S).astype(int), 0, S - 1)
        py = np.clip((U[..., 1] * S).astype(int), 0, S - 1)
        opaque = alpha[py, px] > ref
        lat, st = road.locate(P[..., :2].reshape(-1, 2))
        j = np.where(st >= 0, st, 0)
        inside = ((st >= 0) & (np.abs(lat) < road.hw[j] + clear)).reshape(opaque.shape) & opaque
        hit = inside.any(1)
        bad += int(hit.sum())
        for jj in j.reshape(opaque.shape)[hit][:, 0]:
            where[int(road.route_m[jj] // 100 * 100)] += 1
    print(f"tree cards: {cards:,} cards within {reach:.0f} m of the road checked, {bad} with leaves within "
          f"{clear} m of the asphalt" + (f" at {dict(sorted(where.items()))}" if bad else ""))
    return bad


def audit_shoulder(road, every=10):
    """Fraction of the climb's edge (0.1 and 0.2 m outside the asphalt) where the terrain mask is the
    gravel shoulder (B >= 0.5: no CSP grass)."""
    from PIL import Image
    from ac_track import ATLAS, CORR_MARGIN, CORR_PX, CORR_TILE
    from common import Config
    cfg = Config()
    meta = json.loads((AC_DIR / "geometry.json").read_text())
    slots = {}
    G = np.load(AC_DIR / "geometry.npz")
    for k, m in enumerate(meta["meshes"]):           # tile -> (atlas, sx, sy) from the corridor meshes' UVs
        if not m["material"].startswith("terrain_c"):
            continue
        pos, uv = G[f"m{k}_pos"], G[f"m{k}_uv"]
        c = pos[:, :2].mean(0)
        i, jt = int((c[0] + cfg.half) // CORR_TILE), int((cfg.half - c[1]) // CORR_TILE)
        sx, sy = int(np.median(uv[:, 0]) * ATLAS), int(np.median(uv[:, 1]) * ATLAS)
        slots[(i, jt)] = (int(m["material"][9:]), sx, sy)
    masks = {}
    ok = tot = 0
    st = np.arange(0, len(road.s), every)
    st = st[~road.bridge[st]]
    for side in (1, -1):
        for off in (0.1, 0.2):
            p = road.xy[st] + side * road.n[st] * (road.hw[st] + off)[:, None]
            for (x, y) in p:
                i, jt = int((x + cfg.half) // CORR_TILE), int((cfg.half - y) // CORR_TILE)
                if (i, jt) not in slots:
                    continue
                a, sx, sy = slots[(i, jt)]
                if a not in masks:
                    masks[a] = np.asarray(Image.open(AC_DIR / "textures" / f"terrain_cmask_{a:02d}.png"))
                span = CORR_TILE + 2 * CORR_MARGIN
                u = (x + cfg.half - (i * CORR_TILE - CORR_MARGIN)) / span
                v = (cfg.half - y - (jt * CORR_TILE - CORR_MARGIN)) / span
                px, py = int((sx + u) * CORR_PX), int((sy + v) * CORR_PX)
                tot += 1
                ok += masks[a][py, px, 2] >= 128
    print(f"shoulder: gravel (no grass) at {ok / max(tot, 1):.1%} of {tot:,} points 0.1-0.2 m outside the asphalt")
    return ok / max(tot, 1)


def main():
    road = Road()
    hits = audit(road)
    by_kind = defaultdict(int)
    for (kind, _), v in hits.items():
        by_kind[kind] += 1
    print(f"audit_ac: {sum(by_kind.values())} places ({dict(by_kind)})")
    audit_tree_cards(road)
    audit_shoulder(road)


if __name__ == "__main__":
    main()
