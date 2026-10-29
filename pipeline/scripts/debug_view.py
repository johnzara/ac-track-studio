#!/usr/bin/env python3
"""Debug view of the climb around a route distance: corridor orthophoto, 1 m contours of the
final terrain (cyan), road edges (magenta), OSM ways (orange), labels route_m:z.

    debug_view.py ROUTE_M [--r 60] [--out file.jpg]
"""
import argparse
import json

import numpy as np
from PIL import Image, ImageDraw

from climb_report import climb_polyline
from common import DATA, OUTPUT, Config, Heightfield, road_normals, road_widths
from fetch_corridor import CorridorOrtho


def view(m, R=60.0, res=0.2, dem="dem_final.tif", step=1.0):
    cfg = Config()
    hf = Heightfield(cfg, OUTPUT / dem)
    o = CorridorOrtho("climb")
    c, w, s = climb_polyline()
    cx, cy = np.interp(m, s, c[:, 0]), np.interp(m, s, c[:, 1])
    n = int(2 * R / res)
    X, Y = np.meshgrid(cx - R + np.arange(n) * res, cy + R - np.arange(n) * res)
    img = np.nan_to_num(o.sample(X, Y), nan=60)
    Z = hf.sample(X.ravel(), Y.ravel()).reshape(X.shape)
    band = np.floor(Z / step) % 2
    edge = (np.abs(np.diff(band, axis=0, prepend=band[:1])) + np.abs(np.diff(band, axis=1, prepend=band[:, :1]))) > 0
    img[edge] = [0, 255, 255]
    im = Image.fromarray(img.astype(np.uint8))
    d = ImageDraw.Draw(im)

    def px(x, y):
        return ((x - cx + R) / res, (cy + R - y) / res)
    roads = json.loads((OUTPUT / "roads_local.geojson").read_text())["features"]
    for f in roads:
        p = np.asarray(f["geometry"]["coordinates"], float)
        if not np.any((np.abs(p[:, 0] - cx) < 1.5 * R) & (np.abs(p[:, 1] - cy) < 1.5 * R)):
            continue
        nn = road_normals(p[:, :2])
        ww = road_widths(f)
        for side in (-1, 1):
            e = p[:, :2] + nn * (side * ww / 2)[:, None]
            d.line([px(*q) for q in e], fill=(255, 0, 255) if f["properties"].get("climb") else (255, 160, 0), width=1)
    for k in np.arange(max(0, m - 3 * R), min(s[-1], m + 3 * R), 10):
        x, y = np.interp(k, s, c[:, 0]), np.interp(k, s, c[:, 1])
        if abs(x - cx) < R and abs(y - cy) < R:
            d.text(px(x, y), f"{k:.0f}:{np.interp(k, s, c[:, 2]):.1f}", fill=(255, 255, 0))
    return im


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("m", type=float)
    ap.add_argument("--r", type=float, default=60.0)
    ap.add_argument("--out", default=str(OUTPUT / "debug_view.jpg"))
    a = ap.parse_args()
    view(a.m, a.r).save(a.out, quality=88)
    print(a.out)
