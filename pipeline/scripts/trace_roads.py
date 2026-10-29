#!/usr/bin/env python3
"""Refine selected roads against the 10 cm orthophoto: true centreline and per-node width.

    trace_roads.py [--debug]

For each road in config.json "traced_roads" (by OSM way ids), stations every STEP metres along the OSM centreline
sample a cross profile of the orthophoto (data/ortho/<ortho>.tif, from fetch_ortho.py). Asphalt
is light grey and unsaturated; vegetation, roofs and soil are not; shade is "unknown". Each
profile is grown outwards from the centre until it hits non-asphalt, giving left/right edges.
Edges are then cleaned with a running median (driveways, parking and shade make outliers) and
smoothed, and the road is rebuilt from the edge midpoints with per-node widths.

Writes output/road_overrides.json and patches output/roads_local.geojson in place:
  * the traced chains get the new geometry (z re-sampled from their old profile) and a
    per-node "widths" list; "width" becomes the median
  * side roads whose ends sat on a traced road are re-attached to its new centreline
Run after build_osm.py and before build_terrain.py (run_all.sh does).
"""

import argparse
import json

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from scipy.ndimage import gaussian_filter1d, map_coordinates, median_filter
from shapely.geometry import LineString, Point

from common import DATA, OUTPUT, Config

STEP = 2.0           # m between stations
HALF_PROFILE = 9.0   # m sampled either side
RES = 0.1            # m per profile sample
ALONG = 1.2          # m averaged along the road per profile
MIN_W, MAX_W = 3.2, 9.5
WIDEN_M = 1.6         # max local widening over the typical width
REATTACH_M = 1.5     # side-road ends this close to the old centreline are moved onto the new one


def classify(rgb):
    """1 asphalt, 0 not asphalt, 0.5 unknown (shade / markings)."""
    mx, mn = rgb.max(-1), rgb.min(-1)
    sat = (mx - mn) / np.maximum(mx, 1)
    val = rgb.mean(-1)
    g_dom = (rgb[..., 1] > rgb[..., 0] + 4) & (rgb[..., 1] > rgb[..., 2] + 12)  # green
    red = rgb[..., 0] > rgb[..., 2] + 45                                          # roofs, soil
    out = np.full(sat.shape, 0.5)
    out[(sat < 0.19) & (val > 120) & ~g_dom] = 1.0
    out[(sat > 0.24) | g_dom | red] = 0.0
    out[(val < 80) & (sat < 0.35)] = 0.5      # deep shade: could be anything
    return out


class Ortho:
    def __init__(self, cfg, name):
        src = rasterio.open(DATA / "ortho" / f"{name}.tif")
        self.img = src.read().astype(np.float32)          # (3, H, W)
        self.inv = ~src.transform
        self.ox, self.oy = cfg.center_e, cfg.center_n

    def sample(self, x, y):
        c, r = self.inv * (np.asarray(x) + self.ox, np.asarray(y) + self.oy)
        coords = np.array([r - 0.5, c - 0.5])
        return np.stack([map_coordinates(self.img[k], coords, order=1, mode="nearest") for k in range(3)], -1)

    def covers(self, x, y):
        c, r = self.inv * (x + self.ox, y + self.oy)
        return 0 <= r < self.img.shape[1] and 0 <= c < self.img.shape[2]


def resample(xy, step):
    s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(xy, axis=0), axis=1))])
    t = np.linspace(0, s[-1], max(int(round(s[-1] / step)), 1) + 1)
    return np.column_stack([np.interp(t, s, xy[:, 0]), np.interp(t, s, xy[:, 1])]), t


def normals(xy):
    d = np.gradient(gaussian_filter1d(xy, 1.5, axis=0, mode="nearest"), axis=0)
    d /= np.linalg.norm(d, axis=1, keepdims=True) + 1e-9
    return np.column_stack([-d[:, 1], d[:, 0]])


def edges_of(profile, offs):
    """Left (negative) and right (positive) asphalt edge offsets, and whether each was found."""
    n = len(offs)
    mid = n // 2
    seed_zone = np.where((np.abs(offs) < 2.5) & (profile >= 0.75))[0]
    if len(seed_zone) == 0:
        return np.nan, np.nan, False, False
    seed = seed_zone[np.argmin(np.abs(seed_zone - mid))]
    stop = int(round(0.4 / RES))
    out, found = [], []
    for direction in (-1, 1):
        i, bad, last_good = seed, 0, seed
        while 0 <= i + direction < n:
            i += direction
            if profile[i] >= 0.75:
                bad, last_good = 0, i
            elif profile[i] <= 0.25:
                bad += 1
                if bad >= stop:
                    break
            # unknown: neither extends nor stops the run
        hit = 0 <= i + direction < n
        out.append(offs[last_good] + direction * RES / 2)
        found.append(hit)
    return out[0], out[1], found[0], found[1]


def robust_series(v, ok, window, tol):
    """Median-filter cleaned, gap-filled series."""
    v = np.where(ok, v, np.nan)
    idx = np.arange(len(v))
    good = ~np.isnan(v)
    if good.sum() < 3:
        return None
    filled = np.interp(idx, idx[good], v[good])
    med = median_filter(filled, size=window, mode="nearest")
    keep = good & (np.abs(filled - med) < tol)
    if keep.sum() < 3:
        return med
    return np.interp(idx, idx[keep], filled[keep])


def trace(ortho, xy):
    pts, t = resample(xy, STEP)
    nrm = normals(pts)
    tan = np.column_stack([nrm[:, 1], -nrm[:, 0]])
    offs = np.arange(-HALF_PROFILE, HALF_PROFILE + 1e-6, RES)
    along = np.linspace(-ALONG / 2, ALONG / 2, 5)
    L, R, fl, fr, cov = [], [], [], [], []
    for p, n, tg in zip(pts, nrm, tan):
        if not ortho.covers(*p):
            L.append(np.nan), R.append(np.nan), fl.append(False), fr.append(False), cov.append(False)
            continue
        gx = p[0] + n[0] * offs[None, :] + tg[0] * along[:, None]
        gy = p[1] + n[1] * offs[None, :] + tg[1] * along[:, None]
        cls = classify(ortho.sample(gx.ravel(), gy.ravel()).reshape(len(along), len(offs), 3))
        prof = cls.mean(0)
        a, b, ha, hb = edges_of(prof, offs)
        L.append(a), R.append(b), fl.append(ha), fr.append(hb), cov.append(True)
    L, R = np.array(L), np.array(R)
    fl, fr, cov = np.array(fl), np.array(fr), np.array(cov)
    w = R - L
    plausible = cov & (w > MIN_W) & (w < MAX_W + 2)
    win = max(int(24 / STEP) | 1, 5)
    Ls = robust_series(L, plausible & fl, win, 0.7)
    Rs = robust_series(R, plausible & fr, win, 0.7)
    if Ls is None or Rs is None:
        return None
    # a side that runs into a wide grey area (junction, driveway) is replaced by the other
    # side minus the typical width
    W = Rs - Ls
    w_typ = median_filter(W, size=win * 3, mode="nearest")
    too_wide = W > np.minimum(w_typ + 1.2, MAX_W)
    left_suspect = (Ls - median_filter(Ls, size=win * 3, mode="nearest")) < -0.6
    Ls = np.where(too_wide & left_suspect, Rs - w_typ, Ls)
    Rs = np.where(too_wide & ~left_suspect, Ls + w_typ, Rs)
    sig = 2.0 / STEP * 2
    Ls, Rs = gaussian_filter1d(Ls, sig, mode="nearest"), gaussian_filter1d(Rs, sig, mode="nearest")
    # junction mouths, forecourts and parking bays read as asphalt: cap the width at the road's
    # typical width + WIDEN_M, moving the side that strayed further from the OSM line
    cap = np.median(Rs - Ls) + WIDEN_M
    over = (Rs - Ls) > cap
    left_far = np.abs(Ls) > np.abs(Rs)
    Ls = np.where(over & left_far, Rs - cap, Ls)
    Rs = np.where(over & ~left_far, Ls + cap, Rs)
    Ls, Rs = gaussian_filter1d(Ls, sig / 2, mode="nearest"), gaussian_filter1d(Rs, sig / 2, mode="nearest")
    width = np.clip(Rs - Ls, MIN_W, MAX_W)
    shift = (Ls + Rs) / 2
    shift = np.where(cov, shift, 0.0)
    # keep the chain's two ends where they are (junctions with untraced roads)
    ramp = np.clip(np.minimum(t, t[-1] - t) / 8.0, 0, 1)
    centre = pts + nrm * (shift * ramp)[:, None]
    quality = float(np.mean(fl & fr & plausible))
    return {"centre": centre, "width": width, "t": t, "quality": quality,
            "raw": (pts, nrm, L, R, fl, fr)}


def chain_for(roads, way_ids):
    """Concatenate the road chains carrying these OSM ways, in way order."""
    chains = [f for f in roads if set(f["properties"]["osm_ids"]) & set(way_ids)]
    chains.sort(key=lambda f: min(way_ids.index(i) for i in f["properties"]["osm_ids"] if i in way_ids))
    xyz, parts = [], []
    for f in chains:
        c = np.asarray(f["geometry"]["coordinates"], float)
        if xyz:
            end = xyz[-1][-1]
            if np.linalg.norm(c[-1, :2] - end[:2]) < np.linalg.norm(c[0, :2] - end[:2]):
                c = c[::-1]
            c = c[1:] if np.linalg.norm(c[0, :2] - end[:2]) < 0.5 else c
        parts.append((f, len(c)))
        xyz.append(c)
    return chains, np.vstack(xyz), parts


def main(debug=False):
    cfg = Config()
    path = OUTPUT / "roads_local.geojson"
    pristine = OUTPUT / "roads_local_untraced.geojson"
    gj = json.loads(path.read_text())
    if any("traced" in f["properties"] for f in gj["features"]):
        gj = json.loads(pristine.read_text())        # re-run: start again from build_osm's output
    else:
        pristine.write_text(json.dumps(gj))
    roads = gj["features"]
    overrides = {}
    # roads to trace: name -> [ortho name, OSM way ids in order along the road, OSM ways that have
    # a dashed centre line in Street View; the rest only has solid edge lines]
    for name, (ortho_name, way_ids, centre_ways) in cfg.raw.get("traced_roads", {}).items():
        if not (DATA / "ortho" / f"{ortho_name}.tif").exists():
            print(f"{name}: no data/ortho/{ortho_name}.tif (run fetch_ortho.py), skipped")
            continue
        ortho = Ortho(cfg, ortho_name)
        chains, xyz, parts = chain_for(roads, way_ids)
        if not chains:
            continue
        old = LineString(xyz[:, :2])
        res = trace(ortho, xyz[:, :2])
        if res is None:
            print(f"{name}: tracing failed")
            continue
        centre, width = res["centre"], res["width"]
        # z: the old profile at the same arc position
        s_old = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(xyz[:, :2], axis=0), axis=1))])
        z = np.interp([old.project(Point(p)) for p in centre], s_old, xyz[:, 2])
        new = np.column_stack([centre, z])
        new_line = LineString(centre)
        # split back into the original chains at their old boundaries
        bounds = np.cumsum([0] + [n for _, n in parts])
        cut_s = [s_old[min(b, len(s_old) - 1)] for b in bounds]
        s_new = np.array([old.project(Point(p)) for p in centre])
        for k, (f, _) in enumerate(parts):
            a, b = cut_s[k], cut_s[k + 1]
            m = (s_new >= a - 1e-6) & (s_new <= b + 1e-6)
            idx = np.where(m)[0]
            if len(idx) < 2:
                continue
            seg, segw = new[idx], width[idx]
            # make the shared node identical between consecutive chains
            if k > 0 and idx[0] > 0:
                seg, segw = np.vstack([new[idx[0] - 1], seg]), np.concatenate([[width[idx[0] - 1]], segw])
            orig = np.asarray(f["geometry"]["coordinates"])
            if np.linalg.norm(orig[0, :2] - seg[-1, :2]) < np.linalg.norm(orig[0, :2] - seg[0, :2]):
                seg, segw = seg[::-1], segw[::-1]
            f["geometry"]["coordinates"] = [[round(float(v), 3) for v in p] for p in seg]
            f["properties"]["widths"] = [round(float(v), 2) for v in segw]
            f["properties"]["width"] = round(float(np.median(segw)), 2)
            f["properties"]["length"] = round(float(LineString(seg[:, :2]).length), 1)
            f["properties"]["traced"] = name
            f["properties"]["centre_line"] = bool(set(f["properties"]["osm_ids"]) & set(centre_ways))
        # re-attach side roads
        traced_ids = {f["properties"]["id"] for f in chains}
        moved = 0
        for f in roads:
            if f["properties"]["id"] in traced_ids:
                continue
            c = np.asarray(f["geometry"]["coordinates"], float)
            for end in (0, -1):
                p = Point(c[end, :2])
                if old.distance(p) < REATTACH_M:
                    q = new_line.interpolate(new_line.project(p))
                    d = np.array([q.x, q.y]) - c[end, :2]
                    s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(c[:, :2], axis=0), axis=1))])
                    s = s if end == 0 else s[-1] - s
                    c[:, :2] += np.clip(1 - s / 12.0, 0, 1)[:, None] * d
                    moved += 1
            f["geometry"]["coordinates"] = [[round(float(v), 3) for v in p] for p in c]
        shift = np.array([old.distance(Point(p)) for p in centre])
        overrides[name] = {"ways": way_ids, "coords": [[round(float(x), 3), round(float(y), 3)] for x, y in centre],
                           "widths": [round(float(v), 2) for v in width], "quality": round(res["quality"], 2)}
        print(f"{name}: {len(centre)} nodes, width {np.median(width):.2f} m (p10 {np.percentile(width, 10):.2f}, "
              f"p90 {np.percentile(width, 90):.2f}), centreline moved median {np.median(shift):.2f} m / max "
              f"{shift.max():.2f} m, both edges seen at {res['quality']:.0%} of stations, {moved} side-road ends re-attached")
        if debug:
            debug_image(ortho, res, name)
    (OUTPUT / "road_overrides.json").write_text(json.dumps(overrides))
    path.write_text(json.dumps(gj))


def debug_image(ortho, res, name, scale=0.25):
    pts, nrm, L, R, fl, fr = res["raw"]
    img = ortho.img.transpose(1, 2, 0).astype(np.uint8)
    im = Image.fromarray(img)
    im = im.resize((int(im.width * scale), int(im.height * scale)))
    d = ImageDraw.Draw(im)

    def px(p):
        c, r = ortho.inv * (p[0] + ortho.ox, p[1] + ortho.oy)
        return c * scale, r * scale
    for p, n, a, b, ha, hb in zip(pts, nrm, L, R, fl, fr):
        if np.isnan(a):
            continue
        d.point(px(p + n * a), fill=(255, 255, 0) if ha else (255, 128, 0))
        d.point(px(p + n * b), fill=(255, 255, 0) if hb else (255, 128, 0))
    c, w = res["centre"], res["width"]
    n2 = normals(c)
    for side in (-1, 1):
        d.line([px(p) for p in c + n2 * (side * w / 2)[:, None]], fill=(255, 0, 255), width=1)
    d.line([px(p) for p in c], fill=(0, 255, 255), width=1)
    out = OUTPUT / f"trace_{name}.jpg"
    im.save(out, quality=88)
    print("  debug:", out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true")
    main(ap.parse_args().debug)
