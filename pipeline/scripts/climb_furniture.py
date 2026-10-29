#!/usr/bin/env python3
"""Roadside of the hill-climb road, from the Street View survey (streetview_segments.json).

Each segment says, for the left and the right side (driving uphill), what lines the road:
  guardrail_steel     W-beam guardrail on the edge (pieces in climb_furniture.json; ac_track.py
                      builds the 3D profile on posts)
  guardrail_wood      timber rail on posts (generated mesh)
  drystone_wall, stone_wall, retaining_stone
                      grey limestone wall: a retaining wall up to the natural bank where the
                      ground rises (the terrain behind it keeps its natural height), else a
                      free-standing wall of the surveyed height
  retaining_concrete  board-formed concrete retaining wall (same rule)
  concrete_kerb       low concrete kerb / gutter edge (0.25 m)
  rock_face, rock_netting
                      bare rock terrain on the bank behind the edge (climb_rock_zones.geojson;
                      height from the survey)
  fence               peeled-log / timber rail fence
  hedge               bushes in a row
  concrete_parapet    bridge parapets (ac_track.py builds them with the deck), building, none: nothing
Survey distances are along the route of route.py; they are scaled onto the traced climb.
Gaps: side roads (their mouth + JUNCTION_PAD), bridges, building walls.

    climb_furniture.py      -> output/boundaries.json (walls/kerbs/fences, read by
                               build_terrain.py and ac_track.py) and
                               output/climb_furniture.json (placed items: guardrail pieces,
                               rocks, hedge bushes; the bushes go into scenery_inputs.py's
                               vegetation list). The item type names are the planting
                               catalogue's (italy_guardrails_basic, …): labels only.
Run after road_profiles.py and build_buildings.py; then build_terrain.py.
"""

import json
import math

import numpy as np
import shapely
from scipy.ndimage import gaussian_filter1d, median_filter
from shapely import STRtree
from shapely.geometry import LineString, Point

from boundaries import _side_road_ends
from climb_report import climb_polyline
from common import OFFROAD, OUTPUT, ROOT, Config, Heightfield, road_normals

STEP = 1.0
OFF = 0.4                 # wall / kerb line beyond the asphalt edge
GUARD_OFF = 0.5
GUARD_STEP = 2.9          # Italy guardrail pieces are 3 m and overlap a little
RETAIN_MIN = 0.5          # natural bank this much above the road -> retaining wall
RETAIN_MAX = 3.0
JUNCTION_PAD = 2.5
MIN_RUN = 4               # stations
GUARD_DROP_M = 4.0        # ground this far below the road 4-8 m out -> guardrail (if nothing else is there)
ROCK_TYPES = [("italy_rockface_tall_narrow", 5.7, 11.07), ("italy_rockface_short_wide", 10.0, 1.37),
              ("italy_rockface_tall_wide", 12.8, 11.0)]     # (type, length, top above the model origin)
STONE = {"drystone_wall", "stone_wall", "retaining_stone"}


def survey():
    segs = json.loads((ROOT / "streetview_segments.json").read_text())
    route_len = json.loads((OUTPUT / "route_climb.geojson").read_text())["features"][0]["properties"]["length_m"]
    return segs, route_len


def codes_along(t, segs, scale, side):
    key, hkey = ("left", "left_height_m") if side > 0 else ("right", "right_height_m")
    code = np.full(len(t), "none", dtype=object)
    height = np.full(len(t), np.nan)
    for s in segs:
        m = (t >= s["from_m"] * scale) & (t < s["to_m"] * scale)
        code[m] = s[key]
        height[m] = s[hkey] if s[hkey] is not None else np.nan
    return code, height


def plan():
    cfg = Config()
    nat = Heightfield(cfg, OUTPUT / "dem_natural.tif")
    segs, route_len = survey()
    c, w, s = climb_polyline()
    scale = s[-1] / route_len
    t = np.arange(0.5, s[-1] - 0.5, STEP)
    P = np.column_stack([np.interp(t, s, c[:, k]) for k in range(2)])
    Z = np.interp(t, s, c[:, 2])
    W = np.interp(t, s, w)
    N = road_normals(P)
    roads = json.loads((OUTPUT / "roads_local.geojson").read_text())["features"]
    climb = [f for f in roads if f["properties"].get("climb")]
    others = [f for f in roads if not f["properties"].get("climb") and f["properties"]["group"] != "trail"
              and not f["properties"]["tunnel"]]
    line = LineString(c[:, :2])
    ends = _side_road_ends(line, others, tol=1.5)
    bridges = [(f["properties"]["route_m0"], f["properties"]["route_m0"] + f["properties"]["length"])
               for f in climb if f["properties"]["bridge"]]
    blds = [shapely.geometry.shape(f["geometry"]) for f in
            json.loads((OUTPUT / "buildings_local.geojson").read_text())["features"]]
    btree = STRtree(blds)

    runs, items, rock_zones = [], [], []
    stats = {}
    # drops beside the road (natural ground 4-8 m beyond the edge, below the road)
    drop = {}
    for side in (1, -1):
        drop[side] = Z - np.minimum(nat.sample(*(P + N * (side * (W / 2 + 4.0))[:, None]).T),
                                    nat.sample(*(P + N * (side * (W / 2 + 8.0))[:, None]).T))
        drop[side] = gaussian_filter1d(drop[side], 3.0, mode="nearest")
    codes = {side: codes_along(t, segs, scale, side) for side in (1, -1)}
    # a guardrail the survey put on the uphill side of a hairpin belongs on the drop side
    swapped = 0
    for side in (1, -1):
        cs, co = codes[side][0], codes[-side][0]
        wrong = (cs == "guardrail_steel") & (drop[side] < -1.5) & (drop[-side] > 2.0)
        for a, b in _runs(np.where(wrong, "x", ""), ):
            if co[a] in ("none", "hedge", "concrete_kerb"):
                co[a:b + 1] = "guardrail_steel"
                cs[a:b + 1] = "none"
                swapped += b - a + 1
    # steel guardrail wherever the ground falls away and the survey saw nothing else there
    # (not when the studio brief asks for barriers exactly as surveyed)
    added = 0
    survey_only = cfg.raw.get("studio", {}).get("detail", {}).get("barriers") == "survey"
    for side in (() if survey_only else (1, -1)):
        cs = codes[side][0]
        need = (drop[side] > GUARD_DROP_M) & np.isin(cs, ["none", "hedge", "concrete_kerb"])
        need = np.convolve(need.astype(float), np.ones(13), "same") > 0          # +-6 m run-in / run-out
        need &= np.isin(cs, ["none", "hedge", "concrete_kerb", "guardrail_steel"])
        new = need & (cs != "guardrail_steel")
        cs[new] = "guardrail_steel"
        added += int(new.sum())
    stats["guardrail_added_on_drops"] = added * STEP
    stats["guardrail_moved_to_drop_side"] = swapped * STEP
    for side in (1, -1):
        nout = N * side
        code, height = codes[side]
        gap = np.zeros(len(t), bool)
        for s_j, side_j, hw in ends:
            if side_j == side:
                gap |= np.abs(t - s_j) < hw + JUNCTION_PAD
        for a, b in bridges:
            gap |= (t > a - 2) & (t < b + 2)
        E = P + nout * (W / 2 + OFF)[:, None]
        near_b = btree.query(shapely.points(E), predicate="dwithin", distance=0.8)
        gap[np.unique(near_b[0])] = True
        up = np.maximum(nat.sample(*(P + nout * (W / 2 + 1.5)[:, None]).T),
                        nat.sample(*(P + nout * (W / 2 + 2.5)[:, None]).T)) - Z
        up = gaussian_filter1d(median_filter(up, 5, mode="nearest"), 2, mode="nearest")
        kind = np.full(len(t), "", dtype=object)
        for i in range(len(t)):
            if gap[i]:
                continue
            k = code[i]
            if k in STONE or k == "retaining_concrete":
                mat = "stone" if k in STONE else "concrete"
                kind[i] = f"retain_{mat}" if up[i] > RETAIN_MIN else f"wall_{mat}"
            elif k in {"concrete_kerb", "fence", "guardrail_steel", "guardrail_wood", "rock_face", "rock_netting", "hedge"}:
                kind[i] = k
        for a, b in _runs(kind):
            if b - a < MIN_RUN:
                continue
            k = kind[a]
            sl = slice(a, b + 1)
            stats[k] = stats.get(k, 0) + (b - a + 1) * STEP
            h_sv = np.nan_to_num(height[sl], nan=1.0)
            if k.startswith("retain_") or k.startswith("wall_") or k in {"concrete_kerb", "fence", "guardrail_wood"}:
                if k.startswith("retain_"):
                    # the surveyed height (the bank continues above the wall), never higher
                    # than the bank itself
                    top = Z[sl] + np.clip(np.minimum(h_sv, up[sl] + 0.3), 0.5, RETAIN_MAX)
                elif k.startswith("wall_"):
                    top = Z[sl] + np.clip(h_sv, 0.5, 2.0)
                elif k == "concrete_kerb":
                    top = Z[sl] + 0.25
                else:
                    top = Z[sl] + 1.0
                off = 0.15 if k == "concrete_kerb" else OFF
                Q = P[sl] + nout[sl] * (W[sl] / 2 + off)[:, None]
                runs.append({"kind": k, "road": "climb", "route_m": [round(float(t[a]), 1), round(float(t[b]), 1)],
                             "pts": [[round(float(x), 3), round(float(y), 3), round(float(z), 3), round(float(tt), 3)]
                                     for (x, y), z, tt in zip(Q, Z[sl], top)],
                             "out": [[round(float(x), 4), round(float(y), 4)] for x, y in nout[sl]]})
            elif k == "guardrail_steel":
                items += _guardrail(t[sl], P[sl], Z[sl], W[sl], nout[sl], side)
            elif k in {"rock_face", "rock_netting"}:
                items += _rocks(t[sl], P[sl], Z[sl], W[sl], nout[sl], h_sv, k)
                Q0 = P[sl] + nout[sl] * (W[sl] / 2 + 1.0)[:, None]
                Q1 = P[sl] + nout[sl] * (W[sl] / 2 + 7.0)[:, None]
                rock_zones.append(shapely.Polygon(np.vstack([Q0, Q1[::-1]])).buffer(0))
            elif k == "hedge":
                for i in range(a, b + 1, 2):
                    q = P[i] + nout[i] * (W[i] / 2 + 1.2)
                    items.append(("generibush" if i % 4 else "fluffy_bush", float(q[0]), float(q[1]), float(Z[i]) - 0.1,
                                  float(np.random.default_rng(i).uniform(0, 6.28)), 0.0, 1.0))
    (OUTPUT / "boundaries.json").write_text(json.dumps(runs))
    (OUTPUT / "climb_furniture.json").write_text(json.dumps({"items": items}))
    zones = shapely.union_all(rock_zones) if rock_zones else shapely.Polygon()
    (OUTPUT / "climb_rock_zones.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": shapely.geometry.mapping(zones), "properties": {}}]}))
    print("Climb roadside (Street View survey): " + ", ".join(f"{k} {v:.0f} m" for k, v in sorted(stats.items()))
          + f"; {len(items)} forest items")


def _runs(kind):
    out, start = [], None
    for i in range(len(kind) + 1):
        k = kind[i] if i < len(kind) else ""
        if start is not None and k != kind[start]:
            out.append((start, i - 1))
            start = None
        if start is None and k:
            start = i
    return out


def _guardrail(t, P, Z, W, nout, side):
    """Italy W-beam pieces: local +X along the road, local +Y away from it, at road height."""
    out = []
    n = max(1, int(round((t[-1] - t[0]) / GUARD_STEP)))
    for k in range(n):
        u = t[0] + (k + 0.5) * (t[-1] - t[0]) / n
        i = int(np.clip(np.searchsorted(t, u), 1, len(t) - 2))
        q = P[i] + nout[i] * (W[i] / 2 + GUARD_OFF)
        tan = P[i + 1] - P[i - 1]
        yaw = math.atan2(tan[1], tan[0]) + (math.pi if side < 0 else 0.0)
        pitch = math.atan2((Z[i + 1] - Z[i - 1]) * side, np.linalg.norm(tan) or 1)
        out.append(("italy_guardrails_basic", float(q[0]), float(q[1]), float(Z[i]), yaw, pitch, 1.0))
    return out


def _rocks(t, P, Z, W, nout, h, kind):
    """Rock-face models set into the bank so that ~the surveyed height shows above the road."""
    out = []
    rng = np.random.default_rng(int(t[0]))
    hmax = float(np.nanmax(h)) if len(h) else 2.0
    if hmax >= 8:
        typ, length, top = ROCK_TYPES[2]
    elif hmax >= 3:
        typ, length, top = ROCK_TYPES[0]
    else:
        typ, length, top = ROCK_TYPES[1]
    spacing = length * 0.7
    for u in np.arange(t[0] + spacing / 2, t[-1], spacing):
        i = int(np.clip(np.searchsorted(t, u), 1, len(t) - 2))
        depth = 2.2 if top > 5 else 1.6
        q = P[i] + nout[i] * (W[i] / 2 + depth)
        tan = P[i + 1] - P[i - 1]
        yaw = math.atan2(tan[1], tan[0]) + (0.0 if (nout[i] @ np.array([-tan[1], tan[0]])) > 0 else math.pi)
        yaw += rng.uniform(-0.15, 0.15)
        show = float(np.clip(h[i], 1.0, 15.0))
        sc = float(np.clip(show / top, 0.7, 1.5)) if top < 5 else float(np.clip(show / top + 0.15, 0.5, 1.4))
        z = float(Z[i]) + show - top * sc
        out.append((typ, float(q[0]), float(q[1]), z, yaw, 0.0, round(sc, 2)))
    return out


def items():
    path = OUTPUT / "climb_furniture.json"
    return [tuple(x) for x in json.loads(path.read_text())["items"]] if path.exists() else []


if __name__ == "__main__":
    plan()
