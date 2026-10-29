#!/usr/bin/env python3
"""Asphalt width at every Street View stop, measured on the top-down road patches
(../streetview/ground, from the studio's tools/streetview_views.py): 2 cm per pixel, driving
direction up, the camera in the middle.

Two measures on the rows of the patch 2.5-9 m ahead of and behind the camera:
  lines    the white edge lines: the nearest thin bright stripe on each side of the car; the
           asphalt is the distance between their outer edges + LINE_MARGIN_M each side. Reliable
           wherever the road has both edge lines (checked by eye on 18 stops of the first studio build).
  asphalt  the run of asphalt-coloured pixels around the car's lane. Fooled by shadows, grey
           gravel and concrete walls smeared across the patch (they are grey too): only a rough
           figure for roads without lines.
The width at the stop is the median over the rows.

    sv_widths.py [--debug]   -> output/sv_widths.json  [{at_m, method, width_m, rows, spread_m}]
"""

import argparse
import json

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import binary_closing, median_filter

from common import OUTPUT, ROOT

K = 4                      # asphalt measure: 8 cm per pixel
KL = 2                     # line measure: 4 cm per pixel
LINE_MARGIN_M = 0.15       # asphalt beyond the outer edge of an edge line
NEAR_M, FAR_M = 3.0, 8.0
MIN_W, MAX_W = 2.4, 9.5


def asphalt(rgb):
    a = rgb.astype(np.float32) / 255
    mx, mn = a.max(2), a.min(2)
    sat = (mx - mn) / np.maximum(mx, 1e-3)
    grey = (sat < 0.20) & (mx > 0.16) & (mx < 0.93)
    blueish_shadow = (a[..., 2] >= a[..., 0]) & (sat < 0.32) & (mx > 0.10) & (mx < 0.5)     # asphalt in shade
    return grey | blueish_shadow


def measure_lines(path, debug=None):
    im = Image.open(path)
    small = im.resize((im.width // KL, im.height // KL), Image.BOX)
    arr = np.asarray(small).astype(np.float32) / 255
    alpha = arr[..., 3] > 0.5
    mx, mn = arr[..., :3].max(2), arr[..., :3].min(2)
    sat = (mx - mn) / np.maximum(mx, 1e-3)
    m_px = 0.02 * KL
    bg = median_filter(mx, size=(1, int(1.4 / m_px) | 1))
    white = (mx - bg > 0.09) & (sat < 0.24) & alpha
    cx, cy = white.shape[1] // 2, white.shape[0] // 2
    lo_px, hi_px = 1, int(0.34 / m_px)                      # a line is 4-34 cm wide
    skip = int(0.7 / m_px)                                  # the car is at least this far from a line
    widths, marks = [], []
    for sign in (-1, 1):
        for d in np.arange(2.5, 9.0, m_px):
            r = int(round(cy + sign * d / m_px))
            if not 0 <= r < white.shape[0]:
                break
            row = white[r]
            edges = []
            for step in (-1, 1):
                c, found = cx + step * skip, None
                while 2 < c < len(row) - 3:
                    if row[c]:
                        e = c
                        while 2 < e + step < len(row) - 3 and row[e + step]:
                            e += step
                        if lo_px <= abs(e - c) + 1 <= hi_px:
                            found = e                       # outer edge of the stripe
                            break
                        c = e
                    c += step
                edges.append(found)
            if edges[0] is not None and edges[1] is not None:
                wdt = (edges[1] - edges[0] + 1) * m_px + 2 * LINE_MARGIN_M
                if MIN_W <= wdt <= MAX_W:
                    widths.append(wdt)
                    marks.append((r, edges[0], edges[1]))
    if debug is not None and marks:
        dbg = small.convert("RGB")
        dr = ImageDraw.Draw(dbg)
        for r, a, b in marks:
            dr.point([(a, r), (b, r)], fill=(255, 0, 255))
        dbg.save(debug)
    if len(widths) < 40:
        return None
    w = np.asarray(widths)
    return float(np.median(w)), len(w), float(np.percentile(w, 75) - np.percentile(w, 25))


def measure(path, debug=None):
    im = Image.open(path)
    w, h = im.size
    small = im.resize((w // K, h // K), Image.BOX)
    arr = np.asarray(small)
    rgb, alpha = arr[..., :3], arr[..., 3] > 128
    m_px = 0.02 * K
    road = asphalt(rgb) & alpha
    road = binary_closing(road, np.ones((1, 5)))          # paint, small stains
    cx, cy = road.shape[1] // 2, road.shape[0] // 2
    out, marks = [], []
    for sign in (-1, 1):                                    # ahead (up) and behind
        centre = cx
        for d in np.arange(NEAR_M, FAR_M, m_px * 2):
            r = int(round(cy + sign * d / m_px))
            if not 0 <= r < road.shape[0]:
                break
            row = road[r]
            if not alpha[r, max(0, centre - 3):centre + 4].all():
                continue
            if not row[centre]:
                near = np.flatnonzero(row[max(0, centre - 12):centre + 13])
                if not len(near):
                    continue
                centre = max(0, centre - 12) + int(near[np.argmin(np.abs(near - 12))])
            lo = centre
            while lo > 0 and row[lo - 1]:
                lo -= 1
            hi = centre
            while hi < len(row) - 1 and row[hi + 1]:
                hi += 1
            width = (hi - lo + 1) * m_px
            if lo <= 1 or hi >= len(row) - 2 or not MIN_W <= width <= MAX_W:
                continue
            centre = (lo + hi) // 2
            out.append(width)
            marks.append((r, lo, hi))
    if debug is not None:
        dbg = small.convert("RGB")
        dr = ImageDraw.Draw(dbg)
        for r, lo, hi in marks:
            dr.point([(lo, r), (hi, r)], fill=(255, 0, 255))
        dbg.save(debug)
    if len(out) < 12:
        return None
    out = np.asarray(out)
    return float(np.median(out)), len(out), float(np.percentile(out, 75) - np.percentile(out, 25))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true")
    a = ap.parse_args()
    root = ROOT / "streetview" / "ground"
    patches = json.loads((root / "index.json").read_text())["patches"]
    (root / "debug").mkdir(exist_ok=True)
    res = []
    for p in patches:
        dbg = root / "debug" / p["file"].replace(".png", ".jpg") if a.debug else None
        m, method = measure_lines(root / p["file"], dbg), "lines"
        if not m or m[2] > 0.6:
            m, method = measure(root / p["file"], dbg), "asphalt"
        if m:
            res.append({"at_m": p["at_m"], "method": method, "width_m": round(m[0], 2), "rows": m[1], "spread_m": round(m[2], 2)})
    (OUTPUT / "sv_widths.json").write_text(json.dumps(res, indent=1))
    for method in ("lines", "asphalt"):
        w = np.array([r["width_m"] for r in res if r["method"] == method])
        if len(w):
            print(f"sv_widths: {method}: {len(w)} of {len(patches)} stops; median {np.median(w):.2f} m, p10 {np.percentile(w, 10):.2f}, p90 {np.percentile(w, 90):.2f}")


if __name__ == "__main__":
    main()
