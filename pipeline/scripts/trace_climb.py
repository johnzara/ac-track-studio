#!/usr/bin/env python3
"""Refine the hill-climb road (climb.py) against the 15 cm AGEA 2024 orthophoto corridor.

    trace_climb.py [--debug]

The whole climb is traced as one line (bridges included, so the pieces still meet). Every
STEP metres a cross profile of the photo is classified (trace_roads.classify: light grey,
unsaturated = asphalt) and grown outwards from the asphalt nearest the OSM line, giving the
left/right edges. Much of the climb runs under trees, so only stations where both edges are
seen and the width is plausible count; their centre offsets and widths are cleaned (running
median), then
  * offset: fitted to the GeoDBT carriageway (geodbt_line.py: dynamic programming over lateral
    offsets, the photo's centre where it was seen), in two passes: along the OSM line, then
    along the fitted line (the photo sees more of the road there). Without GeoDBT traffic
    areas: interpolated between seen stretches, relaxing back to the OSM line
  * width: interpolated between seen stretches; where nothing was seen for long, the GeoDBT
    carriageway measured across the road (measured_widths.py), else the width of the Street
    View segments (streetview_segments.json, when present), else the climb median
The climb features get the new centreline and per-node "widths"; side roads ending on the
old line are moved onto the new one. Writes output/climb_trace.json (per-station results).
Run after climb.py, before road_profiles.py.
"""

import argparse
import json

import numpy as np
from scipy.ndimage import gaussian_filter1d, median_filter
from shapely.geometry import LineString, Point

from common import OUTPUT, ROOT, Config, Heightfield
from fetch_corridor import CorridorOrtho
from bends import R_MIN, open_bends
from geodbt_line import SignedField, fit_offsets
from trace_roads import classify, edges_of, normals, resample
import trace_roads

STEP = 2.0
HALF_PROFILE = 9.0
RES = 0.1
ALONG = 1.6
SEED_M = 4.0           # the asphalt nearest the OSM line within this offset seeds the profile
MIN_W, MAX_W = 2.8, 8.5
RELAX_M = 40.0         # an offset fades back to 0 over this length beyond the last seen station
REATTACH_M = 1.5
LEG_GAP_M = 1.6        # between two legs of the climb at different heights (retaining wall)
LEG_MIN_W = 3.6
_ROADS = Config().raw.get("roads", {})
SV_MEASURED = bool(_ROADS.get("climb_sv_measured", False))   # survey widths measured on rectified road patches
SV_BIAS_M = 0.0 if SV_MEASURED else 0.3   # estimated survey widths read a little narrower than the orthophoto
GEODBT_TOL = 0.8      # the traced width stays this close to the GeoDBT carriageway
SV_TOL = float(_ROADS.get("climb_sv_tol", 1.5))   # and at most this much wider than the Street View width
SV_TOL_UP = float(_ROADS.get("climb_sv_tol_up", SV_TOL))   # (measured widths: tighter above than below)
MIN_W_CLIMB = 3.8      # drivable minimum (the survey's narrowest 3.0 m reads ~0.9 m low)
BEND_W = 5.6           # carriageway at the apex of a hairpin (the survey: 4.5-6 m)
FILL_CUT_M = 24.0      # gullies (culverts, embankments) and crests (cuttings) narrower than this
BRIDGE_MARGIN_M = 8.0  # bridge decks run straight from this far before / after the span
PROFILE_SIGMA_M = 6.0
MAX_GRADE = Config().raw.get("roads", {}).get("climb_max_grade", 0.17)   # steepest climb gradient over 10 m


def climb_profile(hf, centre, t, bridge_spans):
    """Road heights along the new centreline: the DEM every STEP m, straight across bridges,
    narrow dips filled and narrow bumps cut (grey closing + opening), lightly smoothed."""
    from scipy.ndimage import grey_closing, grey_opening
    z = hf.sample(centre[:, 0], centre[:, 1]).astype(float)
    for a, b in bridge_spans:
        m = (t >= a - BRIDGE_MARGIN_M) & (t <= b + BRIDGE_MARGIN_M)
        if m.any():
            i0, i1 = np.where(m)[0][[0, -1]]
            i0, i1 = max(i0 - 1, 0), min(i1 + 1, len(z) - 1)
            z[i0:i1 + 1] = np.linspace(z[i0], z[i1], i1 - i0 + 1)
    k = int(FILL_CUT_M / STEP) | 1
    z = grey_opening(grey_closing(z, size=k, mode="nearest"), size=k, mode="nearest")
    z = gaussian_filter1d(z, PROFILE_SIGMA_M / STEP, mode="nearest")
    return limit_grade(z)


def limit_grade(z, gmax=MAX_GRADE, base=10.0):
    """Where the profile is steeper than gmax over `base` metres (contour-derived DTM5 across
    gullies, OSM lines off the road on steep slopes), spread the climb along the road: repeated
    local smoothing around the violations, which keeps the heights either side."""
    from scipy.ndimage import binary_dilation
    k = int(base / STEP)
    n_fixed = 0
    for _ in range(400):
        g = (z[k:] - z[:-k]) / base
        bad = np.abs(g) > gmax
        if not bad.any():
            break
        m = np.zeros(len(z), bool)
        m[:-k] |= bad
        m[k:] |= bad
        m = binary_dilation(m, iterations=int(20 / STEP))
        w = gaussian_filter1d(m.astype(float), 3.0)
        z = z * (1 - w) + gaussian_filter1d(z, 4.0, mode="nearest") * w
        n_fixed += 1
    return z


def _arc(xy):
    return np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(xy, axis=0), axis=1))])


def climb_line(roads):
    """Climb features in route order and the concatenated polyline (shared nodes once)."""
    feats = sorted((f for f in roads if f["properties"].get("climb")), key=lambda f: f["properties"]["route_m0"])
    xyz, bounds = [], [0]
    for f in feats:
        c = np.asarray(f["geometry"]["coordinates"], float)
        if xyz:
            c = c[1:]
        xyz.append(c)
        bounds.append(bounds[-1] + len(c))
    return feats, np.vstack(xyz), bounds


def measure(ortho, pts):
    nrm = normals(pts)
    tan = np.column_stack([nrm[:, 1], -nrm[:, 0]])
    offs = np.arange(-HALF_PROFILE, HALF_PROFILE + 1e-6, RES)
    along = np.linspace(-ALONG / 2, ALONG / 2, 5)
    L = np.full(len(pts), np.nan)
    R = np.full(len(pts), np.nan)
    ok = np.zeros(len(pts), bool)
    trace_roads.RES = RES
    for k, (p, n, tg) in enumerate(zip(pts, nrm, tan)):
        gx = p[0] + n[0] * offs[None, :] + tg[0] * along[:, None]
        gy = p[1] + n[1] * offs[None, :] + tg[1] * along[:, None]
        rgb = ortho.sample(gx.ravel(), gy.ravel())
        if np.isnan(rgb).any():
            continue
        prof = classify(rgb.reshape(len(along), len(offs), 3)).mean(0)
        # seed: like trace_roads.edges_of, but the OSM line may be off by up to SEED_M
        cand = np.where((np.abs(offs) < SEED_M) & (prof >= 0.75))[0]
        if len(cand) == 0:
            continue
        centred = np.roll(prof, len(offs) // 2 - cand[np.argmin(np.abs(offs[cand]))])
        a, b, fa, fb = edges_of(centred, offs)
        if not (fa and fb) or np.isnan(a):
            continue
        shift = offs[cand[np.argmin(np.abs(offs[cand]))]]
        L[k], R[k] = a + shift, b + shift
        ok[k] = MIN_W <= (b - a) <= MAX_W
    return nrm, L, R, ok


def clean(v, ok, win, tol):
    """Valid samples that agree with their running median."""
    idx = np.arange(len(v))
    if ok.sum() < 5:
        return ok
    filled = np.interp(idx, idx[ok], v[ok])
    med = median_filter(filled, size=win, mode="nearest")
    return ok & (np.abs(v - med) < tol)


def fill(t, v, good, relax=None, default=0.0):
    """Interpolate v (seen where `good`) along arc t; with `relax`, values fade to `default`
    with distance from the nearest seen station."""
    if good.sum() == 0:
        return np.full(len(t), default)
    out = np.interp(t, t[good], v[good])
    if relax:
        tg = t[good]
        j = np.searchsorted(tg, t)
        d = np.minimum(np.abs(t - tg[np.clip(j - 1, 0, len(tg) - 1)]), np.abs(tg[np.clip(j, 0, len(tg) - 1)] - t))
        u = np.clip(1 - d / relax, 0, 1)
        out = default + (out - default) * u * u * (3 - 2 * u)
    return out


def separate_legs(centre, width, z, t, iterations=6):
    """Where the climb passes close to itself (hairpin legs side by side), keep a gap between
    the two carriageways (LEG_GAP_M, more with a height difference: room for the retaining
    wall), first by narrowing both legs down to LEG_MIN_W, then by moving them apart."""
    from scipy.spatial import cKDTree
    centre, width = centre.copy(), width.copy()
    touched = np.zeros(len(t), bool)
    for _ in range(iterations):
        tree = cKDTree(centre)
        pairs = tree.query_pairs(width.max() + LEG_GAP_M + 1.5, output_type="ndarray")
        pairs = pairs[np.abs(t[pairs[:, 0]] - t[pairs[:, 1]]) > 25.0]
        if not len(pairs):
            break
        i, j = pairs[:, 0], pairs[:, 1]
        d = np.linalg.norm(centre[i] - centre[j], axis=1)
        gap = np.where(np.abs(z[i] - z[j]) > 0.6, LEG_GAP_M, 0.4)
        need = (width[i] + width[j]) / 2 + gap - d
        bad = need > 0.02
        if not bad.any():
            break
        i, j, d, need = i[bad], j[bad], d[bad], need[bad]
        cut = np.zeros(len(t))
        push = np.zeros((len(t), 2))
        cnt = np.zeros(len(t))
        for a_, b_, dd, nd in zip(i, j, d, need):
            narrow = min(nd, (width[a_] - LEG_MIN_W) + (width[b_] - LEG_MIN_W))
            cut[a_] = max(cut[a_], narrow / 2)
            cut[b_] = max(cut[b_], narrow / 2)
            rest = nd - narrow
            if rest > 0:
                u = (centre[a_] - centre[b_]) / max(dd, 1e-6)
                push[a_] += u * rest / 2
                push[b_] -= u * rest / 2
                cnt[a_] += 1
                cnt[b_] += 1
        touched |= (cut > 0) | (cnt > 0)
        # smooth the changes along the road (no sudden steps in width or line)
        cut = np.maximum(cut, gaussian_filter1d(cut, 3.0) * 2.0)
        width = np.maximum(width - gaussian_filter1d(cut, 2.0, mode="nearest") * 1.2, np.minimum(width, LEG_MIN_W))
        push /= np.maximum(cnt, 1)[:, None]
        centre = centre + gaussian_filter1d(push, 4.0, axis=0, mode="nearest") * 1.5
    n = int(touched.sum())
    if n:
        print(f"  hairpin legs side by side: {n} stations narrowed / moved apart")
    return centre, width, n


def curvature_radius(xy):
    d1 = np.gradient(xy, axis=0)
    d2 = np.gradient(d1, axis=0)
    k = (d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]) / np.maximum(np.linalg.norm(d1, axis=1) ** 3, 1e-9)
    return k


def bend_widths(centre, width):
    """Hairpin apexes widen to BEND_W (tighter bends need more room for the swept path); the
    whole climb is at least MIN_W_CLIMB wide."""
    R = 1 / np.maximum(np.abs(curvature_radius(gaussian_filter1d(centre, 1.0, axis=0, mode="nearest"))), 1e-9)
    need = np.interp(R, [R_MIN, 9.0, 16.0], [BEND_W, BEND_W - 0.4, 0.0])
    need = np.maximum(need, gaussian_filter1d(need, 4.0, mode="nearest"))   # widen before / after the apex
    return np.maximum(np.maximum(width, need), MIN_W_CLIMB)


def sv_widths(route_m):
    path = ROOT / "streetview_segments.json"
    if not path.exists():
        return None
    segs = json.loads(path.read_text())
    if SV_MEASURED:                                    # measured at each stop: linear between the stops
        mid = [(s["from_m"] + s["to_m"]) / 2 for s in segs if s.get("width_m")]
        return np.interp(route_m, mid, [float(s["width_m"]) for s in segs if s.get("width_m")])
    w = np.full(len(route_m), np.nan)
    for s in segs:
        if s.get("width_m"):
            w[(route_m >= s["from_m"]) & (route_m <= s["to_m"])] = float(s["width_m"])
    return w


def geodbt_widths(pts):
    """(width, weight 0..1) of the GeoDBT carriageway at every station, or None."""
    from measured_widths import carriageway_data, measured_widths
    cw = carriageway_data(Config())
    return measured_widths(pts, cw, "road", False) if cw is not None else None


def main(debug=False):
    path = OUTPUT / "roads_local.geojson"
    pristine = OUTPUT / "roads_local_climb.geojson"
    gj = json.loads(path.read_text())
    if any(f["properties"].get("traced") for f in gj["features"]):
        gj = json.loads(pristine.read_text())          # re-run: start again from climb.py's output
    else:
        pristine.write_text(json.dumps(gj))
    roads = gj["features"]
    feats, xyz, bounds = climb_line(roads)
    ortho = CorridorOrtho("climb")

    # two passes: the OSM line, then the line fitted to the GeoDBT carriageway (geodbt_line.py),
    # along which the photo sees more of the road
    field = SignedField(Config(), resample(xyz[:, :2], STEP)[0])
    base = xyz[:, :2]
    for pas in (0, 1):
        pts, t = resample(base, STEP)
        nrm, L, R, ok = measure(ortho, pts)
        mid, wid = (L + R) / 2, R - L
        win = 13
        good = clean(mid, ok, win, 0.8) & clean(wid, ok, win, 0.8)
        # isolated hits are unreliable: keep runs of >= 4 stations (8 m)
        runs, start = [], None
        for i, g in enumerate(list(good) + [False]):
            if g and start is None:
                start = i
            elif not g and start is not None:
                if i - start < 4:
                    good[start:i] = False
                start = None
        if field.ok:
            off, on_cw = fit_offsets(field, pts, nrm, mid, good)
            print(f"  pass {pas + 1}: photo sees both edges at {good.mean():.0%}; centre on the GeoDBT carriageway "
                  f"at {on_cw.mean():.0%}; offset median {np.median(np.abs(off)):.2f} m, max {np.abs(off).max():.1f} m")
        else:
            off = gaussian_filter1d(fill(t, mid, good, relax=RELAX_M), 2.0, mode="nearest")
        ramp = np.clip(np.minimum(t, t[-1] - t) / 10.0, 0, 1)
        if pas == 0:
            base = gaussian_filter1d(pts + nrm * (off * ramp)[:, None], 1.0, axis=0, mode="nearest")
    seen_w = fill(t, wid, good)
    # far from any seen station: Street View width, else the climb median
    tg = t[good]
    j = np.searchsorted(tg, t)
    dist = np.minimum(np.abs(t - tg[np.clip(j - 1, 0, len(tg) - 1)]), np.abs(tg[np.clip(j, 0, len(tg) - 1)] - t)) if len(tg) else np.full(len(t), 1e9)
    sv = sv_widths(t)
    fallback = np.where(np.isnan(sv), np.median(wid[good]), sv + SV_BIAS_M) if sv is not None else np.full(len(t), np.median(wid[good]))
    gm = geodbt_widths(pts)
    if gm is not None:
        fallback = gm[0] * gm[1] + fallback * (1 - gm[1])
        print(f"  GeoDBT carriageway measured along {np.mean(gm[1] > 0.5):.0%} of the climb "
              f"(median {np.median(gm[0][gm[1] > 0.5]):.2f} m)")
    u = np.clip((dist - 30.0) / 60.0, 0, 1)
    width = seen_w * (1 - u) + fallback * u
    # the photo's grey can take in a yard or a drive: stay within GEODBT_TOL of the surveyed
    # carriageway, and within SV_TOL above the Street View estimate
    if gm is not None:
        width = width * (1 - gm[1]) + np.clip(width, gm[0] - GEODBT_TOL, gm[0] + GEODBT_TOL) * gm[1]
    if sv is not None:
        width = np.minimum(width, np.where(np.isnan(sv), np.inf, sv + SV_TOL_UP))
        if SV_MEASURED:      # the topo carriageway takes in shoulders and lay-bys; a tree shadow narrows the photo
            width = np.maximum(width, sv - SV_TOL)
    width = gaussian_filter1d(width, 3.0, mode="nearest")

    # the start and finish keep their OSM position
    centre = pts + nrm * (off * ramp)[:, None]
    s_old = _arc(xyz[:, :2])
    # split points between the climb features (shared end nodes), as arc positions (scaled
    # onto the refitted line)
    cut = [s_old[min(b, len(s_old) - 1)] if b < len(s_old) else s_old[-1] for b in bounds]
    cut = [c * t[-1] / s_old[-1] for c in cut]
    cut[0], cut[-1] = 0.0, s_old[-1]
    spans = [(cut[k], cut[k + 1]) for k, f in enumerate(feats) if f["properties"]["bridge"]]
    # drivable bends (bends.py): tight corners filleted, hairpins rebuilt; bridges untouched
    t_old = t
    protect = [(int(np.searchsorted(t, a)), int(np.searchsorted(t, b))) for a, b in spans]
    centre, t_src, bstats = open_bends(centre, protect)
    width = np.interp(t_src, t_old, width)
    t = _arc(centre)

    def to_new(x):
        return float(t[int(np.argmin(np.abs(t_src - x)))])
    cut = [to_new(x) for x in cut]
    cut[0], cut[-1] = 0.0, float(t[-1])
    spans = [(to_new(a), to_new(b)) for a, b in spans]
    print(f"  bends rebuilt for R >= {R_MIN} m: {bstats['fillet']} corners filleted, {bstats['hairpin']} hairpins, "
          f"{bstats['skipped']} skipped; length {t_old[-1]:.0f} -> {t[-1]:.0f} m")
    width = bend_widths(centre, width)
    hf = Heightfield(Config(), OUTPUT / "dem_natural.tif")
    z = climb_profile(hf, centre, t, spans)
    centre, width, n_sep = separate_legs(centre, width, z, t)
    if n_sep:
        z = climb_profile(hf, centre, t, spans)
    new = np.column_stack([centre, z])
    old_line = LineString(xyz[:, :2])
    for k, f in enumerate(feats):
        a, b = cut[k], cut[k + 1]
        ia, ib = int(np.argmin(np.abs(t - a))), int(np.argmin(np.abs(t - b)))
        seg, segw = new[ia:ib + 1], width[ia:ib + 1]
        if len(seg) < 2:
            seg, segw = new[[ia, min(ia + 1, len(new) - 1)]], width[[ia, min(ia + 1, len(new) - 1)]]
        f["geometry"]["coordinates"] = [[round(float(v), 3) for v in p] for p in seg]
        f["properties"]["widths"] = [round(float(v), 2) for v in segw]
        f["properties"]["width"] = round(float(np.median(segw)), 2)
        f["properties"]["length"] = round(float(_arc(seg[:, :2])[-1]), 1)
        f["properties"]["route_m0"] = round(float(t[ia]), 1)
        f["properties"]["traced"] = "climb"
    new_line = LineString(centre)
    climb_ids = {f["properties"]["id"] for f in feats}
    moved = 0
    for f in roads:
        if f["properties"]["id"] in climb_ids:
            continue
        c = np.asarray(f["geometry"]["coordinates"], float)
        for end in (0, -1):
            p = Point(c[end, :2])
            if old_line.distance(p) < REATTACH_M:
                q = new_line.interpolate(new_line.project(p))
                d = np.array([q.x, q.y]) - c[end, :2]
                s = _arc(c[:, :2])
                s = s if end == 0 else s[-1] - s
                c[:, :2] += np.clip(1 - s / 12.0, 0, 1)[:, None] * d
                moved += 1
        f["geometry"]["coordinates"] = [[round(float(v), 3) for v in p] for p in c]
    path.write_text(json.dumps(gj))
    (OUTPUT / "climb_trace.json").write_text(json.dumps({
        "t_old": t_old.round(1).tolist(), "seen": good.tolist(), "offset": off.round(2).tolist(),
        "measured_width": np.where(good, wid, np.nan).round(2).tolist(),
        "t": t.round(1).tolist(), "width": width.round(2).tolist()}))
    print(f"Climb trace: {len(t)} stations, both edges seen at {good.mean():.0%}; width median "
          f"{np.median(width):.2f} m (p10 {np.percentile(width, 10):.2f}, p90 {np.percentile(width, 90):.2f}); "
          f"centreline moved median {np.median(np.abs(off)):.2f} m / max {np.abs(off).max():.2f} m; "
          f"{moved} side-road ends re-attached")
    if debug:
        debug_sheet(ortho, pts, nrm, L, R, good, centre, width, t)


def debug_sheet(ortho, pts, nrm, L, R, good, centre, width, t, every=600.0, half=45.0, res=0.2):
    from PIL import Image, ImageDraw
    tiles = []
    for m in np.arange(150, t[-1], every):
        k = int(np.argmin(np.abs(t - m)))
        cx, cy = centre[k]
        n = int(2 * half / res)
        X, Y = np.meshgrid(cx - half + np.arange(n) * res, cy + half - np.arange(n) * res)
        img = np.nan_to_num(ortho.sample(X, Y), nan=0).astype(np.uint8)
        im = Image.fromarray(img)
        d = ImageDraw.Draw(im)

        def px(p):
            return ((p[0] - cx + half) / res, (cy + half - p[1]) / res)
        near = np.where(np.hypot(*(centre - centre[k]).T) < half * 1.5)[0]
        for i in (near if len(pts) == len(centre) else []):
            if not np.isnan(L[i]):
                col = (255, 255, 0) if good[i] else (255, 120, 0)
                d.point(px(pts[i] + nrm[i] * L[i]), fill=col)
                d.point(px(pts[i] + nrm[i] * R[i]), fill=col)
        n2 = normals(centre[near])
        for side in (-1, 1):
            d.line([px(p) for p in centre[near] + n2 * (side * width[near] / 2)[:, None]], fill=(255, 0, 255))
        d.line([px(p) for p in pts[near]], fill=(255, 0, 0))
        d.text((4, 4), f"{m:.0f} m  w {width[k]:.1f}", fill=(255, 255, 255))
        tiles.append(im)
    cols = 5
    W = Image.new("RGB", (cols * 450, int(np.ceil(len(tiles) / cols)) * 450))
    for i, im in enumerate(tiles):
        W.paste(im, ((i % cols) * 450, (i // cols) * 450))
    W.save(OUTPUT / "trace_climb.jpg", quality=85)
    print("  debug:", OUTPUT / "trace_climb.jpg")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true")
    main(ap.parse_args().debug)
