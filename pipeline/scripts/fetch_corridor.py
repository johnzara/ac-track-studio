#!/usr/bin/env python3
"""Download the AGEA 2024 orthophoto at high resolution in a corridor along a route.

    fetch_corridor.py ROUTE [--res 0.15] [--buffer 30]

Tiles of TILE_PX x TILE_PX on a grid aligned with the map origin; only the tiles that the
route (output/route_ROUTE.geojson, from route.py) buffered by `buffer` metres touches are
fetched. Each tile is a GeoTIFF in data/ortho/corridor_ROUTE/ (UTM 32N); `CorridorOrtho`
samples them as one image. Licence: IODL 2.0, Regione del Veneto.
"""

import argparse
import json

import numpy as np
import rasterio
from rasterio.transform import from_origin
from scipy.ndimage import map_coordinates
from shapely.geometry import LineString, box

from common import DATA, OUTPUT, Config
from fetch_ortho import TILE_PX, get_tile


def tile_dir(route):
    return DATA / "ortho" / f"corridor_{route}"


def fetch(route, res=0.15, buffer=30.0):
    cfg = Config()
    xy = np.array(json.loads((OUTPUT / f"route_{route}.geojson").read_text())["features"][0]["properties"]["local_xy"])
    area = LineString(xy).buffer(buffer)
    step = TILE_PX * res
    out = tile_dir(route)
    out.mkdir(parents=True, exist_ok=True)
    x0, y0, x1, y1 = area.bounds
    n_new = n_all = 0
    for i in range(int(np.floor((x0 + cfg.half) / step)), int(np.ceil((x1 + cfg.half) / step))):
        for j in range(int(np.floor((y0 + cfg.half) / step)), int(np.ceil((y1 + cfg.half) / step))):
            bx0, by0 = -cfg.half + i * step, -cfg.half + j * step
            if not area.intersects(box(bx0, by0, bx0 + step, by0 + step)):
                continue
            n_all += 1
            path = out / f"{i}_{j}.tif"
            if path.exists():
                continue
            X0, Y0 = bx0 + cfg.center_e, by0 + cfg.center_n
            img = get_tile(X0, Y0, X0 + step, Y0 + step, TILE_PX, TILE_PX)
            with rasterio.open(path, "w", driver="GTiff", width=TILE_PX, height=TILE_PX, count=3, dtype="uint8",
                               crs="EPSG:32632", transform=from_origin(X0, Y0 + step, res, res),
                               compress="jpeg", photometric="ycbcr", tiled=True) as dst:
                dst.write(img.transpose(2, 0, 1))
            n_new += 1
    print(f"Corridor ortho '{route}': {n_all} tiles of {step:.0f} m at {res} m/px ({n_new} downloaded)")


class CorridorOrtho:
    """Bilinear RGB sampling (local metres) over the corridor tiles; (nan) outside them."""

    def __init__(self, route):
        cfg = Config()
        self.ox, self.oy = cfg.center_e, cfg.center_n
        self.tiles = {}
        for p in sorted(tile_dir(route).glob("*.tif")):
            with rasterio.open(p) as src:
                self.tiles[tuple(map(int, p.stem.split("_")))] = (src.read().astype(np.float32), src.transform)
        t = next(iter(self.tiles.values()))[1]
        self.res = t.a
        self.step = TILE_PX * self.res
        self.half = cfg.half

    def sample(self, x, y):
        x, y = np.asarray(x, float), np.asarray(y, float)
        out = np.full(x.shape + (3,), np.nan, np.float32)
        ti = np.floor((x + self.half) / self.step).astype(int)
        tj = np.floor((y + self.half) / self.step).astype(int)
        for key in set(zip(ti.ravel().tolist(), tj.ravel().tolist())):
            if key not in self.tiles:
                continue
            img, tr = self.tiles[key]
            m = (ti == key[0]) & (tj == key[1])
            c, r = ~tr * (x[m] + self.ox, y[m] + self.oy)
            coords = np.array([np.asarray(r) - 0.5, np.asarray(c) - 0.5])
            out[m] = np.stack([map_coordinates(img[k], coords, order=1, mode="nearest") for k in range(3)], -1)
        return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("route")
    ap.add_argument("--res", type=float, default=0.15)
    ap.add_argument("--buffer", type=float, default=30.0)
    a = ap.parse_args()
    fetch(a.route, a.res, a.buffer)
