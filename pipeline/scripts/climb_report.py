#!/usr/bin/env python3
"""Print the hill-climb road's length, height profile, gradients and widths (roads_local.geojson)."""

import json

import numpy as np

from common import OUTPUT


def climb_polyline():
    d = json.loads((OUTPUT / "roads_local.geojson").read_text())["features"]
    cl = sorted([f for f in d if f["properties"].get("climb")], key=lambda f: f["properties"]["route_m0"])
    c = np.vstack([np.asarray(f["geometry"]["coordinates"])[(1 if i else 0):] for i, f in enumerate(cl)])
    w = np.concatenate([np.asarray(f["properties"]["widths"])[(1 if i else 0):] for i, f in enumerate(cl)])
    s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(c[:, :2], axis=0).T))])
    return c, w, s


def main():
    c, w, s = climb_polyline()
    t = np.arange(0, s[-1], 10)
    z = np.interp(t, s, c[:, 2])
    print(f"length {s[-1]:.0f} m, z {z[0]:.1f} -> {z[-1]:.1f}, lowest {z.min():.1f} at {t[np.argmin(z)]:.0f} m")
    for L in (10, 50, 100, 200):
        k = L // 10
        gg = (z[k:] - z[:-k]) / L
        print(f"  grade over {L:3d} m: max {gg.max() * 100:5.1f}% at {t[np.argmax(gg)]:5.0f} m, min {gg.min() * 100:5.1f}% at {t[np.argmin(gg)]:5.0f} m")
    print("  widths p10/50/90:", np.round(np.percentile(w, [10, 50, 90]), 2))
    print("  per km: " + ", ".join(f"{a // 1000}:{100 * (np.interp(a + 1000, t, z) - np.interp(a, t, z)) / 1000:.1f}%"
                                 for a in range(0, int(s[-1]) - 999, 1000)))


if __name__ == "__main__":
    main()


def surface(top=12):
    """Bumps under the wheels along the climb (final terrain, 1 m steps, wheel paths 1.1 m
    either side of the centre): |second difference| > 0.04 m noticeable, > 0.10 a jolt."""
    from common import Config, Heightfield, road_normals
    cfg = Config()
    hf = Heightfield(cfg, OUTPUT / "dem_final.tif")
    c, w, s = climb_polyline()
    t = np.arange(0, s[-1], 1.0)
    P = np.column_stack([np.interp(t, s, c[:, k]) for k in range(3)])
    n = road_normals(P[:, :2])
    out = []
    for side in (-1, 1):
        q = P[:, :2] + n * (side * 1.1)
        z = hf.sample(q[:, 0], q[:, 1])
        k = np.abs(np.diff(z, 2))
        out.append(np.concatenate([[0], k, [0]]))
    K = np.maximum(*out)
    dep = np.abs(hf.sample(P[:, 0], P[:, 1]) - P[:, 2])
    print(f"climb surface: kinks > 0.04: {(K > 0.04).sum()}, > 0.10: {(K > 0.10).sum()}, > 0.25: {(K > 0.25).sum()}; "
          f"centre off profile > 0.15 m: {(dep > 0.15).sum()}")
    order = np.argsort(-K)
    shown = []
    for i in order:
        if K[i] < 0.10 or len(shown) >= top:
            break
        if all(abs(t[i] - u) > 40 for u in shown):
            shown.append(t[i])
            print(f"  {K[i]:.2f} at route {t[i]:.0f} m ({P[i, 0]:.0f}, {P[i, 1]:.0f})")
    return t, K


if __name__ == "__main__":
    surface()
