#!/usr/bin/env python3
"""Drivable widths and height profiles for every road in output/roads_local.geojson.

1. Widths: game widths per class (two-lane roads 6-7.5 m; OSM `width` when larger; traced
   roads their measured width + 0.6 m), narrowed only where a building is in the way.
2. Profiles: every road is resampled every 2 m; its heights are median-filtered (LiDAR spikes)
   and smoothed along the road with a class-dependent length (main roads flow, trails keep
   their bumps). Bridges keep their straight decks.
3. Precedence: roads are processed from the most important (rank, width, length). Wherever a
   lesser road's carriageway touches a more important road's carriageway (junctions, and
   roads that run alongside each other), the lesser road is pulled flush to the other road's
   surface there and blended back to its own profile over a length that keeps the gradient
   change small. A carriageway therefore never contains a step: it is either its own smooth
   profile or flush with the road it meets.
0. Corners: OSM draws a bend as one sharp node. On a slope that leaves a crease in the
   terrain on the inside of the bend (the nearest centreline point jumps from one leg to the
   other) and a flat fan on the outside, i.e. a bump under each wheel. Every sharp node
   that is not a junction becomes a circular arc (radius by class, at most half of each
   neighbouring segment, so a hairpin keeps its shape).
Output: nodes every 4 m with per-node widths. Run after trace_roads.py and before
build_terrain.py, which flattens the terrain to these profiles. Re-runnable: the input is
kept as output/roads_local_traced.geojson.
"""

import json

import numpy as np
import shapely
from scipy.ndimage import gaussian_filter1d, median_filter
from shapely import STRtree
from shapely.geometry import LineString, Point, shape

from common import OUTPUT, Config, road_widths
from road_geometry import simplify_nodes

GAME_WIDTH = {"motorway": 11.0, "trunk": 10.0, "primary": 8.5, "secondary": 7.5, "tertiary": 7.0,
              "motorway_link": 6.5, "trunk_link": 6.5, "primary_link": 6.5, "secondary_link": 6.5,
              "tertiary_link": 6.0, "unclassified": 6.0, "residential": 6.0, "living_street": 5.0,
              "service": 4.0, "pedestrian": 5.0, "track": 3.5}
ONEWAY_WIDTH = 4.5
_RCFG = Config()["roads"]
TRACED_EXTRA = float(_RCFG.get("traced_extra_m", 0.6))
GAME_WIDTH.update({k: float(v) for k, v in _RCFG.get("game_widths", {}).items() if not k.startswith("_")})
CLIMB_RANK = 5.0            # the hill-climb road wins every junction
BUILDING_CLEAR = 0.5
SIGMA_M = {"major": 24.0, "minor": 16.0, "track": 8.0, "trail": 3.0}
BLEND_M = {"major": 30.0, "minor": 24.0, "track": 14.0, "trail": 8.0}
RANK = {"major": 3, "minor": 2, "track": 1, "trail": 0}
TOUCH_M = 1.2
STEP = 2.0
OUT_STEP = 4.0
MAX_GRADE_CHANGE = 0.08     # blend length >= |dz| / this
LOCK_MARGIN = 0.5           # a centreline within the other carriageway + this is pulled flush
CORNER_R = {"major": 30.0, "minor": 12.0, "track": 8.0}      # fillet radius (trails keep corners)
CORNER_MIN_DEG = 8.0


def _arc(xy):
    return np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(xy, axis=0), axis=1))])


def resample(c, w, step):
    s = _arc(c[:, :2])
    if s[-1] < step:
        return c.copy(), np.asarray(w, float).copy()
    t = np.linspace(0, s[-1], int(np.ceil(s[-1] / step)) + 1)
    return np.column_stack([np.interp(t, s, c[:, k]) for k in range(3)]), np.interp(t, s, w)


def round_corners(c, radius, blocked):
    """Replace each sharp interior node by a circular arc tangent to both legs.
    c: (n, 3) polyline; blocked(k, a, b) -> max tangent length allowed at node k (0 = keep the
    node, e.g. a junction). z follows the original arc position."""
    n = len(c)
    if n < 3 or radius <= 0:
        return c
    xy = c[:, :2]
    s = _arc(xy)
    seg = np.diff(xy, axis=0)
    L = np.linalg.norm(seg, axis=1)
    U = seg / np.maximum(L, 1e-9)[:, None]
    theta = np.zeros(n)                                 # deflection at each node
    theta[1:-1] = np.arccos(np.clip(np.sum(U[:-1] * U[1:], axis=1), -1, 1))
    cap = np.full(n, np.inf)                            # tangent-length limit from junctions
    for k in range(1, n - 1):
        cap[k] = blocked(k, U[k - 1], U[k])
    ok = (np.degrees(theta) >= CORNER_MIN_DEG) & (np.degrees(theta) <= 150) & (cap >= 0.3)
    ok[[0, -1]] = False
    th = np.where(ok, theta, 0.0)
    out = [c[0]]
    for k in range(1, n - 1):
        if not ok[k]:
            out.append(c[k])
            continue
        u1, u2 = U[k - 1], U[k]
        # each segment is shared with the corner at its other end in proportion to the
        # deflections; 1 m is kept clear of a node that stays sharp (junction, road end)
        lim = []
        for seg_i, other in ((k - 1, k - 1), (k, k + 1)):
            if th[other] > 0:
                lim.append(L[seg_i] * th[k] / (th[k] + th[other]))
            else:
                lim.append(L[seg_i] - 1.0)
        T = min(radius * np.tan(theta[k] / 2), *lim, cap[k])
        if T < 0.3:
            out.append(c[k])
            continue
        theta_k = theta[k]
        R = T / np.tan(theta_k / 2)
        a, b = xy[k] - u1 * T, xy[k] + u2 * T          # tangent points
        cross = u1[0] * u2[1] - u1[1] * u2[0]
        nrm = np.array([-u1[1], u1[0]]) * np.sign(cross)  # towards the inside of the bend
        centre = a + nrm * R
        a0, a1 = np.arctan2(*(a - centre)[::-1]), np.arctan2(*(b - centre)[::-1])
        da = (a1 - a0 + np.pi) % (2 * np.pi) - np.pi
        m = max(2, int(np.ceil(abs(da) * R / 1.0)))       # ~1 m steps
        ang = a0 + da * np.linspace(0, 1, m + 1)
        arc = centre + R * np.column_stack([np.cos(ang), np.sin(ang)])
        sa = s[k] - T + np.linspace(0, 1, m + 1) * 2 * T    # original arc positions, for z
        z = np.interp(sa, s, c[:, 2])
        out.extend(np.column_stack([arc, z]))
    out.append(c[-1])
    return np.asarray(out)


def corner_limits(i, c, ends, end_tree, lines, line_tree):
    """blocked(k, u1, u2) for road i: no arc at a node another road touches; an arc stops 1 m
    short of another road's end lying on either leg (T-junction without a shared node)."""
    def blocked(k, u1, u2):
        P = c[k, :2]
        pt = Point(P)
        for j in line_tree.query(pt.buffer(TOUCH_M)):
            if j != i and lines[j].distance(pt) < TOUCH_M:
                return 0.0
        lim = np.inf
        for e in end_tree.query(pt.buffer(40.0)):
            j, q = ends[e]
            if j == i:
                continue
            for u, sign in ((u1, -1.0), (u2, 1.0)):
                along = float((q - P) @ u) * sign
                lat = abs(float((q - P) @ np.array([-u[1], u[0]])))
                if along > 0 and lat < TOUCH_M + 0.5:
                    lim = min(lim, along - 1.0)
        return max(lim, 0.0)
    return blocked


def game_width(p):
    base = GAME_WIDTH.get(p["highway"])
    if p.get("lanes") == 1 and f"{p['highway']}_1lane" in GAME_WIDTH:
        base = GAME_WIDTH[f"{p['highway']}_1lane"]
    if base is None:
        return p["width"]
    if p["oneway"] and not p.get("roundabout") and p["highway"] not in {"tertiary", "secondary", "primary", "trunk", "motorway"}:
        base = ONEWAY_WIDTH
    return max(base, p["width"])


def widths_for(f, c_orig, xy, blds, btree):
    p = f["properties"]
    s0 = _arc(c_orig[:, :2])
    s = shapely.line_locate_point(LineString(c_orig[:, :2]), shapely.points(xy))
    old = np.interp(s, s0, road_widths(f))
    if p["group"] == "trail":
        return old
    w = old + TRACED_EXTRA if p.get("traced") else np.maximum(np.full(len(xy), game_width(p)), old)
    # building clearance: keep BUILDING_CLEAR from walls, but never narrower than before
    pts = shapely.points(xy)
    hits = btree.query(pts, predicate="dwithin", distance=float(w.max()) / 2 + BUILDING_CLEAR)
    if hits.size:
        d = np.full(len(xy), np.inf)
        dist = shapely.distance(pts[hits[0]], np.asarray(blds, dtype=object)[hits[1]])
        np.minimum.at(d, hits[0], dist)
        w = np.maximum(np.minimum(w, 2 * (d - BUILDING_CLEAR)), old)
    return gaussian_filter1d(w, 3.0, mode="nearest")


def smooth_profile(z, group, bridge):
    """Median + Gaussian along the road; both ends stay at their original height, so chains
    that continue each other (same OSM node) keep meeting at the same height."""
    if bridge or len(z) < 5:
        return z
    sig = SIGMA_M.get(group, 10.0) / STEP
    zs = gaussian_filter1d(median_filter(z, size=5, mode="nearest"), sig, mode="nearest")
    i = np.arange(len(z))
    R = max(2.0 * sig, 3.0)
    ramp0 = np.clip(1 - i / R, 0, 1)
    ramp1 = np.clip(1 - (len(z) - 1 - i) / R, 0, 1)
    return zs + (z[0] - zs[0]) * ramp0 + (z[-1] - zs[-1]) * ramp1


def priority(p):
    rank = CLIMB_RANK if p.get("climb") else RANK.get(p["group"], 1) + (0.5 if p.get("roundabout") else 0.0)
    return (rank, p["width"], p["length"])


def junctions(roads, lines):
    """[(participants [(road index, arc position)], point)] for every place roads meet."""
    tree = STRtree(lines)
    found = {}
    for i, (f, line) in enumerate(zip(roads, lines)):
        if f["properties"]["tunnel"]:
            continue
        coords = np.asarray(line.coords)
        for end in (coords[0], coords[-1]):
            pt = Point(end)
            parts = [(i, line.project(pt))]
            for j in tree.query(pt.buffer(TOUCH_M)):
                if j == i or roads[j]["properties"]["tunnel"]:
                    continue
                if lines[j].distance(pt) < TOUCH_M:
                    parts.append((int(j), lines[j].project(pt)))
            if len(parts) > 1:
                key = (round(end[0] / 2), round(end[1] / 2))
                found.setdefault(key, (end, {}))[1].update({k: v for k, v in parts})
    # crossings in the middle of both roads (same level only)
    for i, line in enumerate(lines):
        pi = roads[i]["properties"]
        if pi["tunnel"] or pi["bridge"]:
            continue
        for j in tree.query(line):
            if j <= i:
                continue
            pj = roads[j]["properties"]
            if pj["tunnel"] or pj["bridge"] or pj.get("layer", "0") != pi.get("layer", "0"):
                continue
            inter = line.intersection(lines[j])
            for g in getattr(inter, "geoms", [inter]):
                if g.geom_type != "Point":
                    continue
                key = (round(g.x / 2), round(g.y / 2))
                found.setdefault(key, (np.array([g.x, g.y]), {}))[1].update({i: line.project(g), int(j): lines[j].project(g)})
    return [(list(parts.items()), pt) for pt, parts in found.values()]



def main():
    path = OUTPUT / "roads_local.geojson"
    pristine = OUTPUT / "roads_local_traced.geojson"
    gj = json.loads(path.read_text())
    if any(f["properties"].get("profiled") for f in gj["features"]):
        gj = json.loads(pristine.read_text())        # re-run: start from trace_roads' output
    else:
        pristine.write_text(json.dumps(gj))
    roads = gj["features"]
    blds = [shape(f["geometry"]) for f in json.loads((OUTPUT / "buildings_osm_local.geojson").read_text())["features"]]
    btree = STRtree(blds)

    lines0 = [LineString(np.asarray(f["geometry"]["coordinates"], float)[:, :2]) for f in roads]
    line_tree = STRtree(lines0)
    ends = []
    for j, f in enumerate(roads):
        cc = np.asarray(f["geometry"]["coordinates"], float)
        ends += [(j, cc[0, :2]), (j, cc[-1, :2])]
    end_tree = STRtree([Point(q) for _, q in ends])
    n_arcs = 0

    data = []
    for i, f in enumerate(roads):
        p = f["properties"]
        c0 = np.asarray(f["geometry"]["coordinates"], float)
        rad = CORNER_R.get(p["group"], 0.0)
        if rad > 0 and not p["bridge"] and not p["tunnel"] and len(c0) >= 3:
            c1 = round_corners(c0, rad, corner_limits(i, c0, ends, end_tree, lines0, line_tree))
            n_arcs += int(len(c1) > len(c0))
        else:
            c1 = c0
        c, _ = resample(c1, np.zeros(len(c1)), STEP)
        path_s = _arc(c1[:, :2])
        t = path_s if path_s[-1] < STEP else np.linspace(0, path_s[-1], len(c))
        w = widths_for(f, c0, c[:, :2], blds, btree)
        c[:, 2] = smooth_profile(c[:, 2], p["group"], p["bridge"])
        data.append({"c": c, "w": w, "s": _arc(c[:, :2]), "path": c1[:, :2], "t": t})

    order = sorted(range(len(roads)), key=lambda i: priority(roads[i]["properties"]), reverse=True)
    done_lines, done_idx = [], []
    tree, tree_n = None, -1
    n_locked, max_dz = 0, 0.0
    for i in order:
        p = roads[i]["properties"]
        d = data[i]
        c, w, s = d["c"], d["w"], d["s"]
        if done_lines and not p["tunnel"] and len(c) >= 2:
            if len(done_lines) - tree_n > 200 or tree is None:
                tree, tree_n = STRtree(done_lines), len(done_lines)
            pts = shapely.points(c[:, :2])
            target = np.full(len(c), np.nan)
            tprio = np.full(len(c), -1e9)
            cand = list(tree.query(LineString(c[:, :2]).buffer(float(w.max()) / 2 + 12.0)))
            cand += list(range(tree_n, len(done_lines)))          # not yet in the tree
            for jl in cand:
                j = done_idx[jl]
                q = roads[j]["properties"]
                if q["tunnel"] or q["bridge"] != p["bridge"] or q.get("layer", "0") != p.get("layer", "0"):
                    continue
                dj, lj = data[j], done_lines[jl]
                dist = shapely.distance(lj, pts)
                sj = shapely.line_locate_point(lj, pts)
                hwj = np.interp(sj, dj["s"], dj["w"]) / 2
                inside = dist <= hwj + LOCK_MARGIN                 # our centreline on its surface
                if not inside.any():
                    continue
                pr = priority(q)[0] * 1000 + q["width"]
                better = inside & (pr > tprio)
                target[better] = np.interp(sj, dj["s"], dj["c"][:, 2])[better]
                tprio[better] = pr
            locked = ~np.isnan(target)
            if locked.any():
                n_locked += 1
                z = c[:, 2].copy()
                li = np.where(locked)[0]
                # locked nodes: exactly flush. Elsewhere the road keeps its own profile plus a
                # correction that fades out from each locked stretch over L (smoothstep);
                # overlapping corrections (short gaps) are averaged
                d_lock = target - z
                corr_sum, w_sum = np.zeros(len(c)), np.zeros(len(c))
                runs = np.split(li, np.where(np.diff(li) > 1)[0] + 1)
                for run in runs:
                    for end_idx, side in ((run[0], -1), (run[-1], 1)):
                        m = (s - s[end_idx]) * side > 0
                        L = max(BLEND_M.get(p["group"], 15.0), abs(d_lock[end_idx]) / MAX_GRADE_CHANGE)
                        u = np.clip(1 - np.abs(s - s[end_idx]) / L, 0, 1)
                        wv = np.where(m, u * u * (3 - 2 * u), 0.0)
                        corr_sum += wv * d_lock[end_idx]
                        w_sum += wv
                corr = np.where(w_sum > 0, corr_sum / np.maximum(w_sum, 1.0), 0.0)
                tz = z + corr
                tz[locked] = target[locked]
                wgt = np.ones(len(c))
                max_dz = max(max_dz, float(np.abs(tz - z)[locked].max()))
                c[:, 2] = z * (1 - wgt) + tz * wgt
        done_lines.append(LineString(c[:, :2]) if len(c) >= 2 else LineString([c[0, :2], c[0, :2] + 0.01]))
        done_idx.append(i)

    for f, d in zip(roads, data):
        # output along the exact (rounded) path: its vertices (1 m on arcs) plus the 2 m
        # profile samples, then simplified (2 cm in xy and z), nodes at most OUT_STEP apart
        pth, t = d["path"], d["t"]
        sp = _arc(pth)
        ss = np.unique(np.concatenate([sp, t]))
        P = np.column_stack([np.interp(ss, sp, pth[:, 0]), np.interp(ss, sp, pth[:, 1]),
                             np.interp(ss, t, d["c"][:, 2]), np.interp(ss, t, d["w"])])
        P = P[simplify_nodes(P, tol=(0.02, 0.02, 0.05), max_seg=OUT_STEP)]
        c, w = P[:, :3], P[:, 3]
        f["geometry"]["coordinates"] = [[round(float(v), 3) for v in q] for q in c]
        f["properties"]["widths"] = [round(float(v), 2) for v in w]
        f["properties"]["width"] = round(float(np.median(w)), 2)
        f["properties"]["profiled"] = True
    path.write_text(json.dumps(gj))
    ws = np.concatenate([d["w"] for d, f in zip(data, roads) if f["properties"]["group"] in {"major", "minor"}])
    print(f"Road profiles: {len(roads)} roads ({n_arcs} with rounded corners), paved width median {np.median(ws):.1f} m (p10 {np.percentile(ws, 10):.1f}); "
          f"{n_locked} roads pulled flush where they meet a more important road (largest correction {max_dz:.2f} m)")


if __name__ == "__main__":
    main()
