#!/usr/bin/env python3
"""AI line and TV cameras for the Assetto Corsa track: what replays (and Time Attack, AI cars and
the apps that show where a car is on the road) need besides the model.

ai/fast_lane.ai (version 7, as Content Manager's AcTools reads it; little-endian)
  int32 7, int32 n, int32 0 (lap time), int32 0 (sample count)
  n x {float x, y, z (AC space), float distance from the first point, int32 index}
  int32 n, n x 18 floats: speed (m/s), gas, brake, obsolete lateral g, radius (m), side left,
  side right (m from the line to the asphalt edges), camber, direction (+1 left / -1 right),
  normal (3), length (to the next point), forward (3), tag, grade
  int32 0 (no grid: AC builds it)
  The line: the climb's centreline pulled towards the inside of the bends (a mild racing line,
  smoothed, kept LINE_MARGIN inside the asphalt), every AI_STEP m, AI_LIFT above the asphalt. Speeds: the cornering limit
  sqrt(MU g R), then accelerating (ACCEL, less uphill) and braking (BRAKE) limits, VMAX at most.
  The spline runs from the bottom of the road to the gate at the top; car positions along it
  (0 .. 1) switch the TV cameras.
data/cameras.ini, cameras_1.ini, cameras_2.ini: three sets (F3 in a replay cycles them)
  TV 1      trackside, a marshal's height, near the road (both sides; outside of bends favoured)
  TV 2      high: poles / hillside spots 10-25 m up, further back; in the woods spots over the
            verge 7-12 m up, looking along the gap in the trees
  Helicam   60-90 m up, well back from the road, long stretches
  Every set covers the whole spline: walking up the road, each next camera is the candidate
  that sees the car (1 m above the road) the furthest without a break from where the previous
  one handed over. Sight lines are tested against the terrain (dem_final), the tree crowns (the
  vegetation's positions and heights, crowns as a canopy height map) and the buildings.
  Cameras follow the car (IS_FIXED=0); their field of view zooms between MAX_FOV (car close)
  and MIN_FOV (car at the far end of the camera's stretch), the car about TARGET_M wide.
Output: output/ac/replay/ (export_ac.py copies it into the track folder).
"""

import json
import math
import struct

import numpy as np
from scipy.ndimage import gaussian_filter1d, maximum_filter, minimum_filter
from shapely.geometry import shape

from ac_track import AC_DIR, CROWN, Road, timed_section
from ac_trees import SPECIES, terrain_height
from common import OUTPUT, Config, Heightfield

OUT = AC_DIR / "replay"
AI_STEP = 1.0                 # m between AI line points
AI_LIFT = 0.4                 # m above the asphalt, like a recorded line (the car's position). AC casts a ray
                              # down from every point to find the road (AISpline::calculateNormals); a point
                              # on the surface is below it half the time in float32 and the ray then finds
                              # nothing (the terrain is cut away under the road): acs.exe crashed on that
AI_END_GAP = 1.5              # m kept from either end of the road mesh
LINE_MARGIN = 1.1             # m kept between the AI line and the asphalt edge
MU, G = 1.05, 9.81
VMAX, ACCEL, BRAKE = 36.0, 4.5, 8.0
CAR_H = 1.0                   # sight lines aim this high above the road
GRID = 2.0                    # canopy / building height map cell (m)
TARGET_M = 9.0                # the car fills about this many metres of the view at the zoomed-in end


def to_ac(p):
    p = np.asarray(p, float)
    return np.stack([p[..., 0], p[..., 2], -p[..., 1]], -1)


# ----------------------------------------------------------------------------- AI line
def racing_line(road, iters=400):
    """Lateral offset (m, + left) at every road station: the centreline relaxed towards a
    smoother path inside the asphalt (iterative smoothing of the line, clamped to the road)."""
    lim = np.maximum(road.hw - LINE_MARGIN, 0.0)
    off = np.zeros(len(road.s))
    for _ in range(iters):
        p = road.xy + road.n * off[:, None]
        sm = gaussian_filter1d(p, 6.0 / 0.5, axis=0, mode="nearest")
        d = np.einsum("ij,ij->i", sm - road.xy, road.n)
        off = np.clip(0.5 * off + 0.5 * d, -lim, lim)
    off[:40] *= np.linspace(0, 1, 40)            # start and end on the centreline
    off[-40:] *= np.linspace(1, 0, 40)
    return gaussian_filter1d(off, 2.0, mode="nearest")


def ai_line(road):
    off = racing_line(road)
    xy = road.xy + road.n * off[:, None]
    z = road.z - CROWN * np.abs(off) + AI_LIFT
    gap = int(round(AI_END_GAP / 0.5))
    st = np.arange(gap, len(road.s) - gap, int(round(AI_STEP / 0.5)))
    if st[-1] != len(road.s) - 1 - gap:
        st = np.r_[st, len(road.s) - 1 - gap]
    P = np.c_[xy[st], z[st]]
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
    dist = np.r_[0, np.cumsum(seg)]
    t = np.gradient(P, axis=0)
    t /= np.linalg.norm(t, axis=1, keepdims=True)
    t2 = t[:, :2] / np.linalg.norm(t[:, :2], axis=1, keepdims=True)
    dt = np.gradient(t2, axis=0) / np.maximum(np.gradient(dist), 1e-6)[:, None]
    kappa = gaussian_filter1d(t2[:, 0] * dt[:, 1] - t2[:, 1] * dt[:, 0], 2.0, mode="nearest")
    radius = 1 / np.maximum(np.abs(kappa), 1e-4)
    grade = np.gradient(P[:, 2]) / np.maximum(np.gradient(dist), 1e-6)
    # speed profile
    v = np.minimum(np.sqrt(MU * G * radius), VMAX)
    for i in range(1, len(v)):                   # accelerating (less uphill)
        a = max(ACCEL - G * grade[i], 0.8)
        v[i] = min(v[i], math.sqrt(v[i - 1] ** 2 + 2 * a * seg[i - 1]))
    for i in range(len(v) - 2, -1, -1):          # braking (easier uphill)
        b = BRAKE + G * grade[i]
        v[i] = min(v[i], math.sqrt(v[i + 1] ** 2 + 2 * max(b, 3.0) * seg[i]))
    dv = np.gradient(v)
    gas = np.clip(np.where(dv >= -0.01, 0.4 + 6 * dv, 0.0), 0, 1)
    brake = np.clip(np.where(dv < -0.01, -4 * dv, 0.0), 0, 1)
    side_l = road.hw[st] - off[st]
    side_r = road.hw[st] + off[st]
    nrm = np.c_[-t[:, 0] * t[:, 2], -t[:, 1] * t[:, 2], t[:, 0] ** 2 + t[:, 1] ** 2]     # up, normal to the slope
    nrm /= np.linalg.norm(nrm, axis=1, keepdims=True)
    length = np.r_[seg, seg[-1]]
    return dict(P=P, dist=dist, st=st, off=off, speed=v, gas=gas, brake=brake, radius=np.minimum(radius, 1000.0),
                side_l=side_l, side_r=side_r, direction=np.where(kappa >= 0, 1.0, -1.0), normal=nrm,
                length=length, forward=t, grade=grade)


def write_ai(path, a):
    n = len(a["P"])
    pos = to_ac(a["P"])
    b = bytearray(struct.pack("<4i", 7, n, 0, 0))
    for i in range(n):
        b += struct.pack("<4fi", *pos[i], a["dist"][i], i)
    b += struct.pack("<i", n)
    nrm, fwd = to_ac(a["normal"]), to_ac(a["forward"])
    for i in range(n):
        b += struct.pack("<18f", a["speed"][i], a["gas"][i], a["brake"][i], 0.0, a["radius"][i],
                         a["side_l"][i], a["side_r"][i], 0.0, a["direction"][i], *nrm[i], a["length"][i],
                         *fwd[i], 0.0, a["grade"][i])
    b += struct.pack("<i", 0)
    path.write_bytes(bytes(b))
    return len(b)


def read_ai(path):
    """Parse a v7 fast_lane.ai back (checks the layout, every byte used)."""
    data = path.read_bytes()
    ver, n, _, _ = struct.unpack_from("<4i", data, 0)
    off = 16
    pts = np.frombuffer(data, "<f4", count=n * 5, offset=off).reshape(n, 5)
    off += 20 * n
    (m,) = struct.unpack_from("<i", data, off)
    off += 4
    ext = np.frombuffer(data, "<f4", count=m * 18, offset=off).reshape(m, 18)
    off += 72 * m
    (grid,) = struct.unpack_from("<i", data, off)
    off += 4
    if ver != 7 or m != n or grid != 0 or off != len(data):
        raise ValueError(f"bad fast_lane.ai: version {ver}, {n}/{m} points, grid {grid}, {len(data) - off} trailing bytes")
    return pts[:, :3], pts[:, 3], ext


# ----------------------------------------------------------------------------- sight lines
class Occluders:
    """Canopy and building tops / bottoms on a GRID m raster, plus the AC terrain."""

    def __init__(self, cfg):
        self.half = cfg.half
        n = int(2 * cfg.half / GRID)
        self.n = n
        top = np.full((n, n), -1e9, np.float32)
        bot = np.full((n, n), 1e9, np.float32)
        d = np.load(OUTPUT / "forest_instances.npz")
        types, data = d["types"], d["data"]
        for typ in np.unique(types):
            kind, h = SPECIES.get(str(typ), ("shrub", 2.0))
            if kind == "shrub":
                continue                              # bushes hide nothing above them
            sel = types == typ
            x, y, z, sc = data[sel, 0], data[sel, 1], data[sel, 2], data[sel, 5]
            H = h * sc
            r = H * (0.22 if kind == "conifer" else 0.3) * 0.7      # cards have gaps: a little narrower
            for k in range(-2, 3):
                for m in range(-2, 3):
                    rr = np.hypot(k, m) * GRID
                    ok = rr <= np.maximum(r, GRID * 0.6)
                    c = np.clip(((x + self.half) / GRID).astype(int) + k, 0, n - 1)[ok]
                    rw = np.clip(((self.half - y) / GRID).astype(int) + m, 0, n - 1)[ok]
                    np.maximum.at(top, (rw, c), (z + H)[ok])
                    np.minimum.at(bot, (rw, c), (z + H * (0.3 if kind == "conifer" else 0.35))[ok])
        from rasterio.features import rasterize
        from rasterio.transform import from_origin
        feats = json.loads((OUTPUT / "buildings_local.geojson").read_text())["features"]
        shapes_ = [(shape(f["geometry"]), float(f["properties"]["eave_z"]) + 1.5) for f in feats]
        bt = rasterize(shapes_, out_shape=(n, n), transform=from_origin(-self.half, self.half, GRID, GRID),
                       fill=-1e9, dtype="float32", merge_alg=__import__("rasterio").enums.MergeAlg.replace)
        has_b = bt > -1e8
        top = np.where(has_b, np.maximum(top, bt), top)
        bot = np.where(has_b, -1e9, bot)          # a building blocks from the ground up
        self.top, self.bot = top, bot
        self.ground = Heightfield(cfg, OUTPUT / "dem_final.tif")      # the AC terrain follows it closely

    def cell(self, x, y):
        c = np.clip(((x + self.half) / GRID).astype(int), 0, self.n - 1)
        r = np.clip(((self.half - y) / GRID).astype(int), 0, self.n - 1)
        return r, c

    def visible(self, cam, tgt, step=1.5, skip_end=4.0):
        """cam (3,), tgt (m, 3) -> bool (m,): nothing between them."""
        d = tgt - cam
        L = np.linalg.norm(d, axis=1)
        ns = np.maximum((L / step).astype(int), 2)
        nmax = int(ns.max())
        f = (np.arange(1, nmax)[None, :] / ns[:, None])                    # (m, k)
        valid = (f < 1.0) & (f * L[:, None] < L[:, None] - skip_end) & (f * L[:, None] > 1.0)
        X = cam[0] + d[:, 0:1] * f
        Y = cam[1] + d[:, 1:2] * f
        Z = cam[2] + d[:, 2:3] * f
        r, c = self.cell(X, Y)
        blocked = (Z < self.top[r, c]) & (Z > self.bot[r, c])
        zt = self.ground.sample(X[valid], Y[valid])
        tb = np.zeros_like(blocked)
        tb[valid] = Z[valid] < zt + 0.15
        return ~((blocked | tb) & valid).any(1)


# ----------------------------------------------------------------------------- cameras
SETS = [
    # name, [(lateral min, max), (height min, max)] spot kinds, max distance, min stretch, candidate spacing
    ("TV 1", [((3.0, 12.0), (1.8, 3.0))], 230.0, 25.0, 8.0),
    # high spots back from the road, and (in the woods, where those look down through the canopy)
    # spots over the verge 7-12 m up that look along the gap in the trees
    ("TV 2", [((12.0, 40.0), (10.0, 25.0)), ((0.5, 2.5), (7.0, 12.0))], 420.0, 60.0, 12.0),
    ("Helicam", [((50.0, 120.0), (60.0, 90.0))], 800.0, 200.0, 40.0),
]


def candidates(road, occ, lat_rng, h_rng, spacing, rng):
    """Camera spots along both sides of the road, not inside a tree crown or a building."""
    out = []
    for i in range(0, len(road.s), int(spacing / 0.5)):
        for side in (1, -1):
            lat = road.hw[i] + rng.uniform(*lat_rng)
            # outside of a bend: a bit further out, it sees along the bend
            if road.kappa[i] * side < -0.01:
                lat += 2.0
            p = road.xy[i] + road.n[i] * side * lat
            if abs(p[0]) > occ.half - 50 or abs(p[1]) > occ.half - 50:
                continue
            j = int(road.tree.query(p)[1])
            if np.linalg.norm(road.xy[j] - p) < road.hw[j] + min(2.0, lat_rng[0]):
                continue                                       # over (another leg of) the road
            zg = float(terrain_height(p[0], p[1]))
            z = zg + rng.uniform(*h_rng)
            r, c = occ.cell(np.array([p[0]]), np.array([p[1]]))
            if occ.bot[r, c][0] < z < occ.top[r, c][0]:
                if h_rng[1] < 5:
                    continue                                   # in a crown: try the next spot
                z = max(z, float(occ.top[r, c][0]) + 3.0)      # high cameras: above the canopy
            out.append((i, np.array([p[0], p[1], z])))
    return out


def plan_set(road, occ, spec, ai, rng):
    name, kinds, dmax, min_run, spacing = spec
    cands = [c for lat_rng, h_rng in kinds for c in candidates(road, occ, lat_rng, h_rng, spacing, rng)]
    lat_rng, h_rng = kinds[0]
    tgt_all = np.c_[road.xy, road.z + CAR_H]
    step = 4                                                   # stations (2 m) tested per sight line
    vis = []
    for i, cam in cands:
        lo = max(0, i - int(dmax / 0.5))
        hi = min(len(road.s), i + int(dmax / 0.5))
        idx = np.arange(lo, hi, step)
        near = np.linalg.norm(tgt_all[idx] - cam, axis=1) <= dmax
        idx = idx[near]
        ok = occ.visible(cam, tgt_all[idx]) if len(idx) else np.zeros(0, bool)
        vis.append((idx, ok))
    cams = []
    s_cur, n = 0, len(road.s)
    while s_cur < n - 1:
        best = None
        for (i, cam), (idx, ok) in zip(cands, vis):
            if not len(idx) or idx[0] > s_cur or idx[-1] <= s_cur:
                continue
            k0 = int(np.searchsorted(idx, s_cur))
            if k0 >= len(idx) or idx[k0] - s_cur > step or not ok[k0]:
                continue
            k = k0
            while k + 1 < len(ok) and ok[k + 1] and idx[k + 1] - idx[k] <= step:
                k += 1
            end = int(idx[k])
            # prefer cameras that are not right on top of the car at the hand-over
            dmin = float(np.linalg.norm(tgt_all[idx[k0:k + 1]] - cam, axis=1).min())
            score = (end - s_cur) * 0.5 - (6.0 - dmin) * 3 * (dmin < 6)
            if best is None or score > best[0]:
                best = (score, cam, end, idx[k0:k + 1])
        if best is None or (best[2] - s_cur) * 0.5 < min(min_run, (n - 1 - s_cur) * 0.5):
            # nothing sees far from here: the nearest spot ahead that sees the car right now,
            # or a spot straight above the road
            j = min(s_cur + int(min_run / 0.5), n - 1)
            if best is None:
                p = road.xy[s_cur] + road.n[s_cur] * (road.hw[s_cur] + lat_rng[0])
                cam = np.array([p[0], p[1], float(terrain_height(p[0], p[1])) + max(h_rng[1], 4.0)])
                best = (0, cam, j, np.arange(s_cur, j + 1, step))
            else:
                best = (best[0], best[1], max(best[2], j), np.arange(s_cur, max(best[2], j) + 1, step))
        _, cam, end, seen = best
        end = max(end, s_cur + step)
        if n - 1 - end < int(min_run / 0.5 / 3):
            end = n - 1                                         # no stub at the very end
        d = np.linalg.norm(tgt_all[seen] - cam, axis=1)
        fov_far = math.degrees(2 * math.atan(TARGET_M / 2 / max(d.max(), 1.0)))
        fov_near = math.degrees(2 * math.atan(TARGET_M * 2.5 / 2 / max(d.min(), 1.0)))
        mid = tgt_all[seen[len(seen) // 2]]
        cams.append(dict(pos=cam, look=mid, s0=s_cur, s1=min(end, n - 1),
                         min_fov=round(float(np.clip(fov_far, 3.0, 40.0)), 2),
                         max_fov=round(float(np.clip(max(fov_near, fov_far + 2), 8.0, 70.0)), 2)))
        s_cur = min(end, n - 1)
    return cams


def spline_fraction(ai, stations):
    """0..1 position along the AI spline of road stations (the AI line is sampled on them)."""
    return np.interp(stations, ai["st"], ai["dist"]) / ai["dist"][-1]


def write_cameras(path, set_name, cams, ai):
    lines = ["[HEADER]", "VERSION=3", f"CAMERA_COUNT={len(cams)}", f"SET_NAME={set_name}", ""]
    for k, c in enumerate(cams):
        pos = to_ac(c["pos"])
        fwd = to_ac(c["look"] - c["pos"])
        fwd /= np.linalg.norm(fwd)
        up = np.array([0.0, 1.0, 0.0]) - fwd * fwd[1]
        up /= np.linalg.norm(up)
        a, b = spline_fraction(ai, np.array([c["s0"], c["s1"]]))
        lines += [f"[CAMERA_{k}]", f"NAME={set_name} {k + 1}",
                  "POSITION=" + ", ".join(f"{v:.3f}" for v in pos),
                  "FORWARD=" + ", ".join(f"{v:.5f}" for v in fwd),
                  "UP=" + ", ".join(f"{v:.5f}" for v in up),
                  f"MIN_FOV={c['min_fov']}", f"MAX_FOV={c['max_fov']}",
                  f"IN_POINT={a:.6f}", f"OUT_POINT={b:.6f}",
                  "SHADOW_SPLIT0=50", "SHADOW_SPLIT1=200", "SHADOW_SPLIT2=600",
                  "NEAR_PLANE=0.3", "FAR_PLANE=12000",
                  "MIN_EXPOSURE=0", "MAX_EXPOSURE=10000",
                  "DOF_FACTOR=10", "DOF_RANGE=10000", "DOF_FOCUS=0", "DOF_MANUAL=0",
                  "SPLINE=", "SPLINE_ROTATION=0", "FOV_GAMMA=0", "SPLINE_ANIMATION_LENGTH=0", "IS_FIXED=0", ""]
    path.write_text("\r\n".join(lines))


def main():
    cfg = Config()
    OUT.mkdir(parents=True, exist_ok=True)
    road = Road()
    ai = ai_line(road)
    size = write_ai(OUT / "fast_lane.ai", ai)
    P, dist, ext = read_ai(OUT / "fast_lane.ai")
    s0, s1 = timed_section()
    i0, i1 = (int(np.argmin(np.abs(road.route_m - m))) for m in (s0, s1))
    print(f"fast_lane.ai: {len(P)} points, {dist[-1]:.0f} m, {size / 1024:.0f} kB; speeds {ext[:, 0].min() * 3.6:.0f}-"
          f"{ext[:, 0].max() * 3.6:.0f} km/h (median {np.median(ext[:, 0]) * 3.6:.0f}); line within "
          f"{np.min(np.minimum(ext[:, 5], ext[:, 6])):.2f} m of an edge at the closest; timed run = spline "
          f"{spline_fraction(ai, np.array([i0]))[0]:.4f} .. {spline_fraction(ai, np.array([i1]))[0]:.4f}")
    occ = Occluders(cfg)
    rng = np.random.default_rng(5)
    summary = {}
    for k, spec in enumerate(SETS):
        cams = plan_set(road, occ, spec, ai, rng)
        fname = "cameras.ini" if k == 0 else f"cameras_{k}.ini"
        write_cameras(OUT / fname, spec[0], cams, ai)
        runs = np.array([(c["s1"] - c["s0"]) * 0.5 for c in cams])
        summary[spec[0]] = {"cameras": len(cams), "stretch_m": [round(float(runs.min())), round(float(np.median(runs))),
                                                                  round(float(runs.max()))]}
        print(f"{fname} ({spec[0]}): {len(cams)} cameras, stretch per camera min / median / max "
              f"{runs.min():.0f} / {np.median(runs):.0f} / {runs.max():.0f} m", flush=True)
    (OUT / "replay.json").write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
