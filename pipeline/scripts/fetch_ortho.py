#!/usr/bin/env python3
"""Download the Veneto AGEA 2024 orthophoto (10 cm/px) for a local-frame box as a GeoTIFF.

    fetch_ortho.py NAME XMIN YMIN XMAX YMAX [--res 0.1]

The box is in our local frame (metres from the map centre). The image is fetched from the
Regione Veneto WMS in UTM 32N tiles and mosaicked to data/ortho/NAME.tif (UTM 32N, north up).
Licence: IODL 2.0, Regione del Veneto.
"""

import argparse
import io
import time

import numpy as np
import rasterio
import requests
from PIL import Image
from rasterio.transform import from_origin

from common import DATA, Config

WMS = "https://idt2-geoserver.regione.veneto.it/geoserver/ows"
LAYER = "rv:ortofoto_agea_2024"
TILE_PX = 2048


def get_tile(x0, y0, x1, y1, w, h):
    params = {"SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetMap", "LAYERS": LAYER,
              "STYLES": "", "SRS": "EPSG:32632", "BBOX": f"{x0},{y0},{x1},{y1}",
              "WIDTH": w, "HEIGHT": h, "FORMAT": "image/jpeg"}
    for attempt in range(5):
        try:
            r = requests.get(WMS, params=params, timeout=120)
            if r.ok and r.headers.get("content-type", "").startswith("image"):
                return np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"))
            print("  bad response", r.status_code, r.text[:200])
        except requests.RequestException as exc:
            print("  retry:", exc)
        time.sleep(3 * (attempt + 1))
    raise RuntimeError("tile failed")


def fetch(name, xmin, ymin, xmax, ymax, res=0.1, force=False):
    out = DATA / "ortho" / f"{name}.tif"
    if out.exists() and not force:
        print(f"{out} exists (use --force to download again)")
        return out
    cfg = Config()
    ox, oy = cfg.center_e, cfg.center_n
    X0, Y0, X1, Y1 = xmin + ox, ymin + oy, xmax + ox, ymax + oy
    W, H = int(round((X1 - X0) / res)), int(round((Y1 - Y0) / res))
    img = np.zeros((H, W, 3), np.uint8)
    step = TILE_PX * res
    n = 0
    for row in range(0, H, TILE_PX):
        for col in range(0, W, TILE_PX):
            w, h = min(TILE_PX, W - col), min(TILE_PX, H - row)
            tx0, ty1 = X0 + col * res, Y1 - row * res
            img[row:row + h, col:col + w] = get_tile(tx0, ty1 - h * res, tx0 + w * res, ty1, w, h)
            n += 1
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out, "w", driver="GTiff", width=W, height=H, count=3, dtype="uint8",
                       crs="EPSG:32632", transform=from_origin(X0, Y1, res, res),
                       compress="jpeg", photometric="ycbcr", tiled=True) as dst:
        dst.write(img.transpose(2, 0, 1))
    print(f"{out}: {W}x{H} px from {n} tiles ({step:.0f} m)")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("box", type=float, nargs=4)
    ap.add_argument("--res", type=float, default=0.1)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    fetch(a.name, *a.box, res=a.res, force=a.force)
