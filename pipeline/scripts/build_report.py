#!/usr/bin/env python3
"""Numbers for the build README: road against the LiDAR terrain, grades, widths built against the
widths measured in the survey. -> output/build_report.json"""

import json

import numpy as np
from scipy.ndimage import gaussian_filter1d

from climb_report import climb_polyline
from common import OUTPUT, ROOT, Config, Heightfield


def main():
    cfg = Config()
    nat = Heightfield(cfg, OUTPUT / "dem_natural.tif")
    c, w, s = climb_polyline()
    t = np.arange(0, s[-1], 1.0)
    P = np.column_stack([np.interp(t, s, c[:, k]) for k in range(2)])
    Z, W = np.interp(t, s, c[:, 2]), np.interp(t, s, w)
    zn = nat.sample(*P.T)
    st, fin = cfg["hillclimb"]["start_m"], cfg["hillclimb"]["finish_m"]
    m = (t >= st) & (t <= fin)
    d = (Z - zn)[m]
    rep = {"length_m": round(float(s[-1]), 1), "timed_m": fin - st, "z_start": round(float(Z[st]), 1), "z_finish": round(float(Z[fin]), 1),
           "dz_mean_abs": round(float(np.abs(d).mean()), 2), "dz_median_abs": round(float(np.median(np.abs(d))), 2),
           "dz_p95": round(float(np.percentile(np.abs(d), 95)), 2), "dz_max": round(float(d.max()), 2), "dz_max_at": int(t[m][np.argmax(d)]),
           "dz_min": round(float(d.min()), 2), "dz_min_at": int(t[m][np.argmin(d)]),
           "within_0.5_pct": round(float((np.abs(d) < 0.5).mean() * 100), 1)}
    zs = gaussian_filter1d(zn, 4)
    for base in (10, 100):
        g, gd = (Z[base:] - Z[:-base]) / base, (zs[base:] - zs[:-base]) / base
        rep[f"grade{base}_road"] = [round(float(g[:fin].max() * 100), 1), int(np.argmax(g[:fin]))]
        rep[f"grade{base}_dtm"] = [round(float(gd[:fin].max() * 100), 1), int(np.argmax(gd[:fin]))]
    segs = json.loads((ROOT / "streetview_segments.json").read_text())
    rows = []
    for a in range(0, int(s[-1]), 100):
        k = (t >= a) & (t < a + 100)
        sv = [x["width_m"] for x in segs if x["from_m"] < a + 100 and x["to_m"] > a and "interpolated" not in x["notes"]]
        rows.append({"from_m": a, "surveyed_m": round(float(np.mean(sv)), 1) if sv else None, "built_m": round(float(W[k].mean()), 1),
                     "min_m": round(float(W[k].min()), 1), "max_m": round(float(W[k].max()), 1)})
    rep["width_per_100m"] = rows
    diff = [r["built_m"] - r["surveyed_m"] for r in rows if r["surveyed_m"]]
    rep["width_built_minus_surveyed"] = {"median": round(float(np.median(diff)), 2), "p90_abs": round(float(np.percentile(np.abs(diff), 90)), 2),
                                         "max": round(float(max(diff)), 2), "min": round(float(min(diff)), 2)}
    rep["width_built"] = {"min": round(float(W[m].min()), 2), "median": round(float(np.median(W[m])), 2), "max": round(float(W[m].max()), 2),
                          "narrowest_at": int(t[m][np.argmin(W[m])]), "widest_at": int(t[m][np.argmax(W[m])])}
    (OUTPUT / "build_report.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps({k: v for k, v in rep.items() if k != "width_per_100m"}, indent=1))
    print("km: surveyed/built  " + "  ".join(f"{r['from_m'] / 1000:.1f}:{r['surveyed_m']}/{r['built_m']}" for r in rows))


if __name__ == "__main__":
    main()
