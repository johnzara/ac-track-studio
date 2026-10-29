#!/usr/bin/env python3
"""Crop of the 25 cm AGEA orthophoto tiles (roof_analysis.py cache) around a local point.

    ortho_crop.py X Y [--r 40] [--out file.jpg]
"""
import argparse
import math

import numpy as np
from PIL import Image, ImageDraw

from common import OUTPUT, Config
from roof_analysis import RES, TILE_M, fetch_tile, tile_path


def crop(x, y, r=40.0):
    cfg = Config()
    x0, y0, x1, y1 = x - r, y - r, x + r, y + r
    n = int(2 * r / RES)
    out = Image.new("RGB", (n, n))
    for ix in range(math.floor(x0 / TILE_M), math.floor(x1 / TILE_M) + 1):
        for iy in range(math.floor(y0 / TILE_M), math.floor(y1 / TILE_M) + 1):
            fetch_tile(cfg, ix, iy)
            im = Image.open(tile_path(ix, iy))
            # tile top-left in local metres: (ix*TILE_M, (iy+1)*TILE_M)
            px = int(round((ix * TILE_M - x0) / RES))
            py = int(round((y1 - (iy + 1) * TILE_M) / RES))
            out.paste(im, (px, py))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("x", type=float)
    ap.add_argument("y", type=float)
    ap.add_argument("--r", type=float, default=40.0)
    ap.add_argument("--out", default=str(OUTPUT / "ortho_crop.jpg"))
    a = ap.parse_args()
    im = crop(a.x, a.y, a.r)
    d = ImageDraw.Draw(im)
    for k in range(0, int(2 * a.r) + 1, 10):            # 10 m grid ticks
        d.line([(k / RES, 0), (k / RES, 6)], fill=(255, 255, 0))
        d.line([(0, k / RES), (6, k / RES)], fill=(255, 255, 0))
    im.save(a.out, quality=88)
    print(a.out)
