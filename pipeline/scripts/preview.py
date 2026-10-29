#!/usr/bin/env python3
"""Render preview images: a hillshade of the heightmap with optional OSM overlays.

  preview.py                 -> output/preview_natural.png (from dem_natural.tif)
  preview.py --final         -> output/preview_final.png   (from dem_final.tif, roads drawn on top)
"""

import argparse
import json

import numpy as np
import rasterio
from PIL import Image, ImageDraw

from common import OUTPUT, Config

ROAD_COLOURS = {
    "major": (230, 60, 40), "minor": (255, 220, 60), "track": (160, 110, 60), "trail": (120, 80, 40), "other": (255, 255, 255),
}


def hillshade(z, cellsize, azimuth=315.0, altitude=45.0):
    gy, gx = np.gradient(z, cellsize)
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)  # rows run north->south, so +row is -y
    az, alt = np.radians(azimuth), np.radians(altitude)
    shade = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    return np.clip(shade, 0, 1)


def render(dem, cfg, scale, roads=None, mask=None):
    step = int(round(1 / scale))
    z = dem[::step, ::step]
    shade = hillshade(z, cfg.mpp * step)
    lo, hi = np.percentile(z, [1, 99])
    t = np.clip((z - lo) / (hi - lo), 0, 1)
    # elevation tint: green lowlands -> brown -> pale summits
    tint = np.stack([0.45 + 0.45 * t, 0.6 + 0.2 * t - 0.1 * t ** 2, 0.4 + 0.35 * t], -1)
    rgb = (tint * (0.35 + 0.65 * shade[..., None]) * 255).astype(np.uint8)
    if mask is not None:
        m = mask[::step, ::step]
        rgb[m] = (rgb[m] * 0.6 + np.array([60, 60, 200]) * 0.4).astype(np.uint8)
    img = Image.fromarray(rgb)
    if roads:
        draw = ImageDraw.Draw(img)
        n = z.shape[0]
        px = 1 / (cfg.mpp * step)
        for feat in roads["features"]:
            pts = [((x + cfg.half) * px, n - 1 - (y + cfg.half) * px)
                   for x, y, *_ in feat["geometry"]["coordinates"]]
            group = feat["properties"].get("group", "other")
            width = 3 if group == "major" else 2 if group == "minor" else 1
            draw.line(pts, fill=ROAD_COLOURS.get(group, ROAD_COLOURS["other"]), width=width)
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", action="store_true", help="preview dem_final.tif with roads")
    ap.add_argument("--scale", type=float, default=0.5, help="output scale relative to the grid")
    args = ap.parse_args()
    cfg = Config()
    name = "dem_final.tif" if args.final else "dem_natural.tif"
    with rasterio.open(OUTPUT / name) as src:
        dem = src.read(1)
    roads = None
    roads_path = OUTPUT / "roads_local.geojson"
    if args.final and roads_path.exists():
        roads = json.loads(roads_path.read_text())
    mask = None
    src_path = OUTPUT / "dem_source.npy"
    if not args.final and src_path.exists():
        mask = np.load(src_path) > 0  # tint everything that is not LiDAR
    img = render(dem, cfg, args.scale, roads, mask)
    out = OUTPUT / ("preview_final.png" if args.final else "preview_natural.png")
    img.save(out)
    print(f"Wrote {out} ({img.width}x{img.height})")


if __name__ == "__main__":
    main()
