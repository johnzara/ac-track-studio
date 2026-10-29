#!/usr/bin/env python3
"""Audit the hill-climb road as the game will see it.

  1. bumps: second differences of the final ground heights (dem_final.tif) along the centre and
     two wheel paths, 1 m steps (ac_track.py smooths the road on top of these)
  2. width and hairpins: narrowest stretches; radius of every tight bend vs. what a car needs
     (outer front wheel turning circle ~5.6 m, ~2.6 m swept path)
  3. surface: share of the carriageway on the asphalt land-cover layers (the terrain texture
     around the AC road's edges)
  4. guardrails: stretches with a real drop beside the road and no barrier; guardrails on the
     uphill side (suspected side swap)
Writes output/audit_climb.json.
"""

import json

import numpy as np
from scipy.ndimage import gaussian_filter1d

from build_terrain import LAYERS
from climb_report import climb_polyline
from common import OUTPUT, Config, Heightfield, road_normals

CAR_R = 5.6
SWEPT = 2.6


def main():
    cfg = Config()
    hf = Heightfield(cfg, OUTPUT / "dem_final.tif")
    nat = Heightfield(cfg, OUTPUT / "dem_natural.tif")
    c, w, s = climb_polyline()
    roads = json.loads((OUTPUT / "roads_local.geojson").read_text())["features"]
    bridges = [(f["properties"]["route_m0"] - 3, f["properties"]["route_m0"] + f["properties"]["length"] + 3)
               for f in roads if f["properties"].get("climb") and f["properties"]["bridge"]]
    t = np.arange(1.0, s[-1] - 1.0, 1.0)
    P = np.column_stack([np.interp(t, s, c[:, k]) for k in range(3)])
    W = np.interp(t, s, w)
    N = road_normals(P[:, :2])
    on_bridge = np.zeros(len(t), bool)
    for a, b in bridges:
        on_bridge |= (t > a) & (t < b)
    report = {}

    # 1. bumps
    K = []
    for off in (-1.1, 0.0, 1.1):
        q = P[:, :2] + N * off
        z = hf.sample(q[:, 0], q[:, 1])
        k = np.concatenate([[0], np.abs(np.diff(z, 2)), [0]])
        K.append(np.where(on_bridge, 0, k))
    K = np.max(K, axis=0)
    worst = [(round(float(t[i])), round(float(K[i]), 3)) for i in np.argsort(-K)[:12] if K[i] > 0.04]
    report["bumps"] = {"gt_0.02": int((K > 0.02).sum()), "gt_0.04": int((K > 0.04).sum()),
                       "gt_0.10": int((K > 0.10).sum()), "worst": worst}

    # 2. width and bends
    xy = gaussian_filter1d(P[:, :2], 1.5, axis=0, mode="nearest")
    d1 = np.gradient(xy, axis=0)
    d2 = np.gradient(d1, axis=0)
    curv = np.abs(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]) / np.maximum(np.linalg.norm(d1, axis=1) ** 3, 1e-9)
    R = 1 / np.maximum(curv, 1e-6)
    bends = []
    i = 0
    while i < len(t):
        if R[i] < 15:
            j = i
            while j + 1 < len(t) and R[j + 1] < 15:
                j += 1
            k = i + int(np.argmin(R[i:j + 1]))
            turn = np.degrees(abs(np.arctan2(*d1[min(j + 3, len(t) - 1)][::-1]) - np.arctan2(*d1[max(i - 3, 0)][::-1])))
            turn = min(turn, 360 - turn)
            r_out = R[k] + W[k] / 2 - 0.3
            ok = r_out >= CAR_R and (W[k] - 0.6) >= SWEPT * (1 + max(0, 8 - R[k]) / 8)
            bends.append({"route_m": round(float(t[k])), "radius": round(float(R[k]), 1), "width": round(float(W[k]), 2),
                          "turn_deg": round(float(turn)), "drivable": bool(ok)})
            i = j + 1
        else:
            i += 1
    report["width"] = {"p1": round(float(np.percentile(W, 1)), 2), "p10": round(float(np.percentile(W, 10)), 2),
                       "median": round(float(np.median(W)), 2),
                       "narrowest": [(round(float(t[i])), round(float(W[i]), 2)) for i in np.argsort(W)[:8]]}
    report["bends"] = bends

    # 3. surface
    idx = np.load(OUTPUT / "terrain_layer_index.npy")
    asphalt = {LAYERS.index("asphalt"), LAYERS.index("asphalt_main")}
    tot = paved = 0
    gaps = []
    for frac in np.linspace(-0.45, 0.45, 7):
        q = P[:, :2] + N * (frac * W)[:, None]
        col = np.clip(np.round(q[:, 0] + cfg.half).astype(int), 0, cfg.size_px - 1)
        row = np.clip(np.round(cfg.size_px - 1 - (q[:, 1] + cfg.half)).astype(int), 0, cfg.size_px - 1)
        ok = np.isin(idx[row, col], list(asphalt)) | on_bridge
        tot += len(ok)
        paved += int(ok.sum())
        gaps += [round(float(x)) for x in t[~ok][::5]]
    report["surface"] = {"asphalt_share": round(paved / tot, 4), "gap_samples": sorted(set(gaps))[:40]}

    # 4. guardrails
    items = json.loads((OUTPUT / "climb_furniture.json").read_text())["items"]
    rails = np.array([[x, y] for typ, x, y, *_ in items if typ.startswith("italy_guardrails")]) if items else np.zeros((0, 2))
    walls = json.loads((OUTPUT / "boundaries.json").read_text())
    wpts = np.array([p[:2] for r in walls for p in r["pts"]]) if walls else np.zeros((0, 2))
    from scipy.spatial import cKDTree
    rt = cKDTree(rails) if len(rails) else None
    wt = cKDTree(wpts) if len(wpts) else None
    missing, uphill_rails = [], []
    for side, name in ((1, "left"), (-1, "right")):
        edge = P[:, :2] + N * (side * (W / 2 + 0.5))[:, None]
        drop = P[:, 2] - np.minimum(nat.sample(*(P[:, :2] + N * (side * (W / 2 + 4))[:, None]).T),
                                    nat.sample(*(P[:, :2] + N * (side * (W / 2 + 8))[:, None]).T))
        has_rail = rt.query(edge)[0] < 1.8 if rt is not None else np.zeros(len(t), bool)
        has_wall = wt.query(edge)[0] < 1.2 if wt is not None else np.zeros(len(t), bool)
        need = (drop > 4.0) & ~on_bridge
        bad = need & ~has_rail & ~has_wall
        for a, b in _runs(bad, 15):
            missing.append({"side": name, "from_m": round(float(t[a])), "to_m": round(float(t[b])),
                            "max_drop_m": round(float(drop[a:b + 1].max()), 1)})
        up = has_rail & (drop < -1.5)
        for a, b in _runs(up, 10):
            uphill_rails.append({"side": name, "from_m": round(float(t[a])), "to_m": round(float(t[b]))})
    report["guardrails"] = {"pieces": int(len(rails)),
                            "drop_over_4m_without_barrier": missing,
                            "missing_m": int(sum(m["to_m"] - m["from_m"] for m in missing)),
                            "rails_on_uphill_side": uphill_rails}
    (OUTPUT / "audit_climb.json").write_text(json.dumps(report, indent=1))
    b = report["bumps"]
    print(f"Bumps (ground heights): >2 cm {b['gt_0.02']}, >4 cm {b['gt_0.04']}, >10 cm {b['gt_0.10']}; worst {b['worst'][:6]}")
    print(f"Width: p1 {report['width']['p1']} m, p10 {report['width']['p10']}, median {report['width']['median']}; narrowest {report['width']['narrowest'][:4]}")
    tight = [x for x in bends if not x["drivable"]]
    print(f"Bends with radius < 15 m: {len(bends)}, too tight for a car: {len(tight)} {tight[:8]}")
    print(f"Surface: asphalt layer under {report['surface']['asphalt_share']:.2%} of the carriageway")
    g = report["guardrails"]
    print(f"Guardrails: {g['pieces']} pieces; drop > 4 m without barrier: {g['missing_m']} m in {len(g['drop_over_4m_without_barrier'])} stretches; "
          f"rails on the uphill side: {g['rails_on_uphill_side']}")


def _runs(mask, min_len):
    out, start = [], None
    for i, m in enumerate(list(mask) + [False]):
        if m and start is None:
            start = i
        elif not m and start is not None:
            if i - start >= min_len:
                out.append((start, i - 1))
            start = None
    return out


if __name__ == "__main__":
    main()
