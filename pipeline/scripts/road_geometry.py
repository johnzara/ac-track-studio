"""Small road geometry helpers shared by several stages.

  DROP_M, _resample, _urban_mask   boundaries.py (walls, fences and hedges along traced roads)
  EXTRA_*_M                        build_terrain.py (paved / dirt ground beyond a road's edge)
  simplify_nodes                   road_profiles.py (the nodes kept along every road)
"""

import json

import numpy as np
from shapely.geometry import shape
from shapely.ops import unary_union

from common import OUTPUT

DROP_M = 2.5          # ground this far below the road edge, 6 m out: a drop (guardrail side)
URBAN = {"urban", "industrial"}

EXTRA_ASPHALT_M = 0.9      # terrain asphalt beyond the carriageway edge (~0.9 m at 1 m cells)
EXTRA_DIRT_M = 0.9         # terrain dirt beyond a track's edge
EXTRA_TRAIL_M = 0.6

# simplify_nodes: deviation allowed (lateral, height, width, m) and the longest step between nodes
SIMPLIFY_TOL = (0.12, 0.10, 0.25)
SIMPLIFY_MAX_SEG = 25.0


def _resample(pts, step):
    s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(pts[:, :2], axis=0), axis=1))])
    if s[-1] < 2 * step:
        return None, None
    t = np.arange(step / 2, s[-1] - step / 2 + 1e-6, step)
    out = np.column_stack([np.interp(t, s, pts[:, k]) for k in range(pts.shape[1])])
    return out, t



def _urban_mask(cfg):
    lc = json.loads((OUTPUT / "landcover_local.geojson").read_text())["features"]
    return unary_union([shape(f["geometry"]) for f in lc if f["properties"]["class"] in URBAN])



def simplify_nodes(P, tol=SIMPLIFY_TOL, max_seg=SIMPLIFY_MAX_SEG, keep=()):
    """Indices of the nodes to keep (Douglas-Peucker on xy, z and width at once)."""
    n = len(P)
    if n <= 2:
        return np.arange(n)
    arc = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(P[:, :2], axis=0), axis=1))])
    mask = np.zeros(n, bool)
    mask[[0, -1]] = True
    for k in keep:
        mask[int(k)] = True
    ks = np.where(mask)[0]
    stack = list(zip(ks[:-1], ks[1:]))
    tx, tz, tw = tol
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        A, B = P[a], P[b]
        seg = B[:2] - A[:2]
        L2 = float(seg @ seg)
        Q = P[a + 1:b]
        t = np.clip(((Q[:, :2] - A[:2]) @ seg) / L2, 0, 1) if L2 > 1e-12 else np.zeros(len(Q))
        dxy = np.linalg.norm(Q[:, :2] - (A[:2] + t[:, None] * seg), axis=1)
        err = np.maximum(dxy / tx, np.abs(Q[:, 2] - (A[2] + t * (B[2] - A[2]))) / tz)
        if P.shape[1] > 3:
            err = np.maximum(err, np.abs(Q[:, 3] - (A[3] + t * (B[3] - A[3]))) / tw)
        if err.max() > 1.0:
            m = a + 1 + int(np.argmax(err))
        elif arc[b] - arc[a] > max_seg:
            m = a + 1 + int(np.argmin(np.abs(arc[a + 1:b] - (arc[a] + arc[b]) / 2)))
        else:
            continue
        mask[m] = True
        stack += [(a, m), (m, b)]
    return np.where(mask)[0]
