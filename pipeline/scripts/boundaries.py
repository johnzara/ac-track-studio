#!/usr/bin/env python3
"""Walls, fences and hedges along the roads traced from the orthophoto (trace_roads.py).

What Street View shows along a typical village lane:
  * houses: every plot is closed off at the road edge: a low concrete wall with a wrought-iron
    or brown wooden picket fence, a white plastered wall, or green wire mesh with a cypress
    hedge behind; gaps for driveways and side roads
  * where the ground rises above the road: a stone retaining wall up to the natural ground,
    with a railing on top in front of houses (dry-stone walls in the wooded hairpins)
  * where the ground falls away: guardrails (climb_furniture.py, on the climb)

    boundaries.py           plan the runs -> output/boundaries.json (read by build_terrain.py,
                            which keeps the natural ground behind retaining walls, and by
                            ac_track.py, which builds the walls and fences; hedges go into the
                            vegetation list of scenery_inputs.py)

Run after build_buildings.py (it needs the house footprints), then run build_terrain.py again.
"""

import json
import math

import numpy as np
import shapely
from scipy.ndimage import gaussian_filter1d, median_filter
from shapely import STRtree
from shapely.geometry import LineString, Point, Polygon

from buildings_mesh import TexMesh
from common import OFFROAD, OUTPUT, Config, Heightfield, landmark_registry, road_normals, road_widths
from road_geometry import DROP_M, _resample, _urban_mask

STEP = 1.0              # m between stations
OFF = 0.35              # wall centre line beyond the asphalt edge
RETAIN_MIN = 0.7        # natural ground this much above the road (1.5-2.5 m out) -> retaining wall
RETAIN_MAX = 4.0
OWNER_M = 28.0          # a house within this distance (same side) -> its plot reaches the road
IN_BUILDING_M = 0.6     # a building wall this close to the line is the boundary itself
JUNCTION_PAD = 3.0      # gap either side of a side road's carriageway
DRIVEWAY_W = 3.4
MIN_RUN = 3             # stations
TOWN_HOUSES = 4

# style -> wall (material, height, thickness), fence (texture, height), pillars, hedge
STYLES = {
    "iron":    dict(wall=("concrete", 0.5, 0.22), fence=("fence_iron_alpha", 1.0), pillar="concrete"),
    "picket":  dict(wall=("concrete", 0.4, 0.22), fence=("fence_picket_alpha", 1.0), pillar="pillar_redbrown"),
    "plaster": dict(wall=("wall_plaster", 1.25, 0.3), fence=None, pillar=None),
    "mesh":    dict(wall=("concrete", 0.3, 0.2), fence=("fence_mesh_alpha", 1.2), pillar=None, hedge=True),
    "stone":   dict(wall=("bld_stone_rubble", 0.9, 0.45), fence=None, pillar=None),
}
TOWN_MIX = {"iron": 0.40, "picket": 0.15, "plaster": 0.25, "mesh": 0.20}
COUNTRY_MIX = {"stone": 0.45, "mesh": 0.35, "picket": 0.20}
TILE = {"concrete": 2.0, "wall_plaster": 2.0, "bld_stone_rubble": 3.0, "pillar_redbrown": 0.5}


def _style_for(owner, town):
    rng = np.random.default_rng(1000 + owner)
    mix = TOWN_MIX if town else COUNTRY_MIX
    return str(rng.choice(list(mix), p=list(mix.values())))


def _side_road_ends(line, others, tol=1.0):
    """(arc position, side +1/-1, half width) of every road that ends on this line."""
    out = []
    for f in others:
        c = np.asarray(f["geometry"]["coordinates"], float)
        if len(c) < 2:
            continue
        for end, inner in ((0, 1), (-1, -2)):
            p = Point(c[end, :2])
            if line.distance(p) > tol:
                continue
            s = line.project(p)
            a, b = line.interpolate(max(s - 1, 0)), line.interpolate(min(s + 1, line.length))
            t = np.array([b.x - a.x, b.y - a.y])
            # a point some way into the side road says which side it leaves from
            q = LineString(c[:, :2] if end == 0 else c[::-1, :2]).interpolate(min(8.0, 0.5 * LineString(c[:, :2]).length))
            v = np.array([q.x - p.x, q.y - p.y])
            side = 1 if t[0] * v[1] - t[1] * v[0] > 0 else -1
            out.append((s, side, road_widths(f)[end] / 2))
    return out


def plan(cfg=None):
    cfg = cfg or Config()
    nat = Heightfield(cfg, OUTPUT / "dem_natural.tif")
    roads = json.loads((OUTPUT / "roads_local.geojson").read_text())["features"]
    traced = [f for f in roads if f["properties"].get("traced")]
    others = [f for f in roads if not f["properties"].get("traced") and f["properties"]["group"] not in OFFROAD
              and not f["properties"]["tunnel"]]
    feats = json.loads((OUTPUT / "buildings_local.geojson").read_text())["features"]
    polys = [shapely.geometry.shape(f["geometry"]) for f in feats]
    cents = np.array([p.centroid.coords[0] for p in polys])
    tree = STRtree(polys)
    from common import landmark_footprint_blocks
    own_boundary = landmark_footprint_blocks(feats)      # hand-built landmarks bring their own fences
    urban = _urban_mask(cfg)
    skip = [Polygon(s["vegetation_clear"]).buffer(1.0) for s in landmark_registry().values() if s.get("vegetation_clear")]
    skip = shapely.union_all(skip) if skip else Polygon()

    runs = []
    for f in traced:
        coords = np.asarray(f["geometry"]["coordinates"], float)[:, :3]
        st, s_arc = _resample(np.column_stack([coords, road_widths(f)]), STEP)
        if st is None:
            continue
        P, Z, W = st[:, :2], st[:, 2], st[:, 3]
        N = road_normals(P)
        line = LineString(coords[:, :2])
        s_on = np.array([line.project(Point(p)) for p in P])
        ends = _side_road_ends(line, others)
        for side in (1, -1):
            nout = N * side
            E = P + nout * (W / 2 + OFF)[:, None]
            up = np.maximum(nat.sample(*(P + nout * (W / 2 + 1.5)[:, None]).T),
                            nat.sample(*(P + nout * (W / 2 + 2.5)[:, None]).T)) - Z
            drop = Z - nat.sample(*(P + nout * (W / 2 + 6.0)[:, None]).T)
            gap = np.zeros(len(P), bool)
            for s_j, side_j, hw in ends:
                if side_j == side:
                    gap |= np.abs(s_on - s_j) < hw + JUNCTION_PAD
            gap |= shapely.contains_xy(skip, E[:, 0], E[:, 1])
            # "town" = in the urban land cover, or at least TOWN_HOUSES houses within 45 m
            near = np.array([len(tree.query(Point(e).buffer(45.0))) for e in E])
            town = shapely.contains_xy(urban, E[:, 0], E[:, 1]) | (near >= TOWN_HOUSES)
            owner = np.full(len(P), -1)
            inside = np.zeros(len(P), bool)
            for i, e in enumerate(E):
                pt = Point(e)
                cand = tree.query(pt.buffer(OWNER_M))
                best, bd = -1, OWNER_M
                for j in cand:
                    d = polys[j].distance(pt)
                    if d < IN_BUILDING_M:
                        inside[i] = True
                    if (cents[j] - P[i]) @ nout[i] > 0 and d < bd:
                        best, bd = j, d
                owner[i] = best
            # one driveway per house, opposite its centroid, unless a side road is close by
            drive = np.zeros(len(P), bool)
            for j in set(owner[owner >= 0]):
                k = np.where(owner == j)[0]
                s_b = line.project(Point(cents[j]))
                if any(abs(s_b - s_j) < 10 for s_j, sd, _ in ends if sd == side):
                    continue
                m = k[np.abs(s_on[k] - s_b) < DRIVEWAY_W / 2]
                drive[m] = up[m] < 1.5        # no driveway through a high retaining wall
            up_s = gaussian_filter1d(median_filter(up, 5, mode="nearest"), 2, mode="nearest")
            kind = np.full(len(P), "", dtype=object)
            for i in range(len(P)):
                if gap[i] or inside[i]:
                    continue
                if up_s[i] > RETAIN_MIN:
                    kind[i] = "retain_rail" if (town[i] and owner[i] >= 0) else "retain"
                elif drive[i] or drop[i] > DROP_M:
                    continue
                elif owner[i] >= 0 and int(owner[i]) not in own_boundary:
                    kind[i] = "fence:" + _style_for(int(owner[i]), bool(town[i]))
            for a, b in _runs_of(kind):
                if b - a < MIN_RUN:
                    continue
                sl = slice(a, b + 1)
                k = kind[a]
                top = Z[sl] + (np.clip(up_s[sl], 0, RETAIN_MAX) + 0.1 if k.startswith("retain") else 0)
                runs.append({"kind": k, "road": f["properties"]["id"],
                             "pts": [[round(float(x), 3), round(float(y), 3), round(float(z), 3), round(float(t), 3)]
                                     for (x, y), z, t in zip(E[sl], Z[sl], top)],
                             "out": [[round(float(x), 4), round(float(y), 4)] for x, y in nout[sl]]})
    summary = {}
    for r in runs:
        summary[r["kind"]] = summary.get(r["kind"], 0) + len(r["pts"]) * STEP
    print("Boundaries along traced roads: " + ", ".join(f"{k} {v:.0f} m" for k, v in sorted(summary.items())))
    (OUTPUT / "boundaries.json").write_text(json.dumps(runs))
    return runs


def _runs_of(kind):
    out, start = [], None
    for i in range(len(kind) + 1):
        k = kind[i] if i < len(kind) else ""
        if start is not None and k != kind[start]:
            out.append((start, i - 1))
            start = None
        if start is None and k:
            start = i
    return out


# ------------------------------------------------------------------ meshes

def _quad(mesh, mat, v, uv, want):
    """Quad whose front faces `want` (flips the winding when needed)."""
    n = np.cross(np.subtract(v[1], v[0]), np.subtract(v[3], v[0]))
    if np.dot(n, want) < 0:
        v, uv = v[::-1], uv[::-1]
    mesh.quad(mat, v, uv)


def _wall(mesh, P, out, zb, zt, thick, mat, top_mat=None):
    s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))])
    tu = TILE.get(mat, 2.0)
    for sgn in (-1, 1):                                   # road face, back face
        Q = P + out * (sgn * thick / 2)
        for i in range(len(P) - 1):
            want = [out[i, 0] * sgn, out[i, 1] * sgn, 0]
            v = [[*Q[i], zb[i]], [*Q[i + 1], zb[i + 1]], [*Q[i + 1], zt[i + 1]], [*Q[i], zt[i]]]
            uv = [(s[i] / tu, zb[i] / tu), (s[i + 1] / tu, zb[i + 1] / tu), (s[i + 1] / tu, zt[i + 1] / tu), (s[i] / tu, zt[i] / tu)]
            _quad(mesh, mat, v, uv, want)
    F, B = P - out * thick / 2, P + out * thick / 2
    tm = top_mat or mat
    for i in range(len(P) - 1):                           # coping
        v = [[*F[i], zt[i]], [*F[i + 1], zt[i + 1]], [*B[i + 1], zt[i + 1]], [*B[i], zt[i]]]
        uv = [(s[i] / 2, 0), (s[i + 1] / 2, 0), (s[i + 1] / 2, thick / 2), (s[i] / 2, thick / 2)]
        _quad(mesh, tm, v, uv, [0, 0, 1])
    for i, o in ((0, -1), (len(P) - 1, 1)):               # run ends
        t = P[min(i + 1, len(P) - 1)] - P[max(i - 1, 0)]
        t = t / (np.linalg.norm(t) or 1)
        v = [[*F[i], zb[i]], [*B[i], zb[i]], [*B[i], zt[i]], [*F[i], zt[i]]]
        _quad(mesh, mat, v, [(0, zb[i] / tu), (thick / tu, zb[i] / tu), (thick / tu, zt[i] / tu), (0, zt[i] / tu)], [*(t * o), 0])


def _panel(mesh, P, z0, h, tex):
    s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))])
    for i in range(len(P) - 1):
        v = [[*P[i], z0[i]], [*P[i + 1], z0[i + 1]], [*P[i + 1], z0[i + 1] + h], [*P[i], z0[i] + h]]
        mesh.quad(tex, v, [(s[i] / 2, 0), (s[i + 1] / 2, 0), (s[i + 1] / 2, 1), (s[i] / 2, 1)])


def _pillar(mesh, p, z0, h, size, mat):
    hs = size / 2
    c = [(p[0] - hs, p[1] - hs), (p[0] + hs, p[1] - hs), (p[0] + hs, p[1] + hs), (p[0] - hs, p[1] + hs)]
    for k in range(4):
        a, b = c[k], c[(k + 1) % 4]
        want = [b[1] - a[1], -(b[0] - a[0]), 0]
        _quad(mesh, mat, [[*a, z0], [*b, z0], [*b, z0 + h], [*a, z0 + h]],
              [(0, 0), (size / 0.5, 0), (size / 0.5, h / 0.5), (0, h / 0.5)], want)
    _quad(mesh, "concrete", [[*c[0], z0 + h], [*c[1], z0 + h], [*c[2], z0 + h], [*c[3], z0 + h]],
          [(0, 0), (1, 0), (1, 1), (0, 1)], [0, 0, 1])


def build(runs=None):
    """(TexMesh, hedge forest instances) for the planned runs."""
    if runs is None:
        path = OUTPUT / "boundaries.json"
        runs = json.loads(path.read_text()) if path.exists() else []
    mesh, hedges = TexMesh(), []
    for r in runs:
        A = np.asarray(r["pts"], float)
        P, zr, zt = A[:, :2], A[:, 2], A[:, 3]
        out = np.asarray(r["out"], float)
        k = r["kind"]
        if k in CLIMB_KINDS:                      # climb_furniture.py (Street View survey)
            CLIMB_KINDS[k](mesh, P, out, zr, zt)
            continue
        if r["kind"].startswith("retain"):
            _wall(mesh, P, out, zr - 0.4, zt, 0.45, "bld_stone_rubble", "concrete")
            if r["kind"] == "retain_rail":
                _panel(mesh, P + out * 0.1, zt, 1.0, "fence_iron_alpha")
            continue
        st = STYLES[r["kind"].split(":")[1]]
        mat, h, thick = st["wall"]
        _wall(mesh, P, out, zr - 0.5, zr + h, thick, mat, "concrete")
        if st["fence"]:
            tex, fh = st["fence"]
            _panel(mesh, P, zr + h, fh, tex)
        if st["pillar"]:
            s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))])
            marks = sorted(set([0, len(P) - 1] + [int(np.searchsorted(s, d)) for d in np.arange(6.0, s[-1] - 2, 6.0)]))
            for i in marks:
                _pillar(mesh, P[i], zr[i] - 0.3, h + (st["fence"][1] if st["fence"] else 0) + 0.45, 0.38, st["pillar"])
        if st.get("hedge"):
            s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))])
            for d in np.arange(1.0, s[-1] - 0.5, 2.0):
                i = int(np.searchsorted(s, d))
                i = min(max(i, 1), len(P) - 1)
                t = P[i] - P[i - 1]
                yaw = math.atan2(t[1], t[0])
                q = P[i] + out[i] * 0.75
                hedges.append(("cypress_hedge_3m", float(q[0]), float(q[1]), float(zr[i]) - 0.05, yaw, 0.0, 1.0))
    return mesh, hedges


if __name__ == "__main__":
    plan()


# ------------------------------------------------------------------ hill-climb roadside kinds

def _rail_fence(mesh, P, out, zr, zt, post=0.12, spacing=2.0, mat="bench_wood"):
    """Timber rail fence: square posts every `spacing` m, two rails (peeled logs, timber rail)."""
    s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))])
    h = zt - zr
    for d in np.arange(0, s[-1] + 1e-6, spacing):
        i = int(min(np.searchsorted(s, d), len(P) - 1))
        _pillar(mesh, P[i], zr[i] - 0.3, h[i] + 0.3, post, mat)
    for frac, th in ((0.45, 0.1), (0.9, 0.1)):
        zc = zr + h * frac
        _wall(mesh, P + out * 0.08, out, zc - th / 2, zc + th / 2, 0.08, mat)


CLIMB_KINDS = {
    "retain_stone": lambda m, P, o, zr, zt: _wall(m, P, o, zr - 0.4, zt, 0.5, "bld_stone_rubble", "bld_stone_rubble"),
    "wall_stone": lambda m, P, o, zr, zt: _wall(m, P, o, zr - 0.4, zt, 0.5, "bld_stone_rubble", "bld_stone_rubble"),
    "retain_concrete": lambda m, P, o, zr, zt: _wall(m, P, o, zr - 0.4, zt, 0.35, "concrete"),
    "wall_concrete": lambda m, P, o, zr, zt: _wall(m, P, o, zr - 0.4, zt, 0.3, "concrete"),
    "concrete_kerb": lambda m, P, o, zr, zt: _wall(m, P, o, zr - 0.2, zt, 0.2, "concrete"),
    "fence": lambda m, P, o, zr, zt: _rail_fence(m, P, o, zr, zt),
    "guardrail_wood": lambda m, P, o, zr, zt: _rail_fence(m, P, o, zr, zt, post=0.16, spacing=1.8, mat="timber_dark"),
}
