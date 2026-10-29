#!/usr/bin/env python3
"""Top-down map of everything the pipeline generates: hillshaded land cover, vineyard rows,
buildings (coloured by style), bridges, roads and the climb route, plus place labels.

Place labels and zoom crops come from config.json "map_preview": {"labels": [[name, lat, lon], ...],
"zooms": [[name, lat, lon, radius_m], ...]} (both optional).

Output: output/map_preview.png (full map, 1 px = 2 m) and output/map_preview_small.png."""

import json

import numpy as np
import rasterio
from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import shape

from build_terrain import LAYER_COLOURS, LAYERS
from common import OUTPUT, Config
from preview import hillshade

STYLE_COLOURS = {"residential": (200, 96, 64), "annex": (170, 130, 110), "rural": (140, 90, 70),
                 "industrial": (150, 154, 162), "commercial": (210, 120, 80), "church": (240, 236, 220),
                 "tower": (120, 60, 50)}


def main():
    cfg = Config()
    n, s = cfg.size_px, cfg.mpp
    dem = rasterio.open(OUTPUT / "dem_final.tif").read(1)
    idx = np.load(OUTPUT / "terrain_layer_index.npy")
    pal = np.array([LAYER_COLOURS[k] for k in LAYERS], float)
    pal[LAYERS.index("vineyard")] = (170, 160, 110)          # bare soil between vine rows
    shade = hillshade(dem, s)[..., None]
    rgb = pal[idx] * (0.45 + 0.7 * shade)
    img = Image.fromarray(rgb.clip(0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img)
    px = lambda x, y: ((x + cfg.half) / s, n - 1 - (y + cfg.half) / s)  # noqa: E731

    forest = OUTPUT / "forest_instances.npz"
    furniture = []
    if forest.exists():
        z = np.load(forest)
        for typ, (x, y, _, yaw, _, sc) in zip(z["types"], z["data"]):
            X, Y = px(x, y)
            if typ == "grape_vine_group":
                c, s_ = np.cos(yaw), np.sin(yaw)
                for k in (-1, 0, 1):                    # three rows per group
                    ox, oy = -s_ * k * 2.5 / s, -c * k * 2.5 / s
                    d.line([(X - c * 3.5 + ox, Y + s_ * 3.5 + oy), (X + c * 3.5 + ox, Y - s_ * 3.5 + oy)],
                           fill=(52, 92, 36), width=1)
            elif typ.startswith("italy_"):
                furniture.append((typ, X, Y, yaw))
            else:
                r = (1.6 if "bush" in typ else 2.4) * sc
                col = {"olive_tree": (110, 128, 84), "cypress_tree": (24, 60, 30)}.get(typ, (38, 84, 38))
                d.ellipse([X - r, Y - r, X + r, Y + r], fill=col)
    roads = json.loads((OUTPUT / "roads_local.geojson").read_text())["features"]
    order = {"trail": -1, "track": 0, "minor": 1, "major": 2}
    for f in sorted(roads, key=lambda f: order[f["properties"]["group"]]):
        p = f["properties"]
        col = {"major": (250, 245, 235), "minor": (238, 236, 230), "track": (215, 200, 160), "trail": (190, 160, 110)}[p["group"]]
        pts = [px(*c[:2]) for c in f["geometry"]["coordinates"]]
        w = max(1, round(p["width"] / s))
        if p["bridge"]:
            d.line(pts, fill=(60, 60, 60), width=w + 4)
        d.line(pts, fill=col, width=w)
    for f in json.loads((OUTPUT / "buildings_local.geojson").read_text())["features"]:
        g = shape(f["geometry"])
        col = STYLE_COLOURS.get(f["properties"]["style"], (200, 96, 64))
        for poly in getattr(g, "geoms", [g]):
            d.polygon([px(*c) for c in poly.exterior.coords], fill=col, outline=tuple(int(c * 0.6) for c in col))
    for typ, X, Y, yaw in furniture:
        c, s_ = np.cos(yaw), np.sin(yaw)
        col = (220, 40, 40) if "guardrail" in typ else (90, 80, 70)
        h = 1.5 / s
        d.line([(X - c * h, Y + s_ * h), (X + c * h, Y - s_ * h)], fill=col, width=2)
    route = OUTPUT / "route_climb.geojson"
    if route.exists():
        xy = json.loads(route.read_text())["features"][0]["properties"]["local_xy"]
        d.line([px(*c) for c in xy], fill=(30, 110, 220), width=5, joint="curve")

    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 64)
    except OSError:
        font = ImageFont.load_default()
    mp = cfg.raw.get("map_preview", {})
    for name, lat, lon in mp.get("labels", []):
        x, y = cfg.lonlat_to_local(lon, lat)
        X, Y = px(x, y)
        d.text((X + 14, Y - 70), name, font=font, fill=(20, 20, 20), stroke_width=5, stroke_fill=(255, 255, 255))
        d.ellipse([X - 8, Y - 8, X + 8, Y + 8], fill=(20, 20, 20))
    img.save(OUTPUT / "map_preview.png")
    img.resize((2048, 2048), Image.LANCZOS).save(OUTPUT / "map_preview_small.png")
    # zoom crops
    for name, lat, lon, r in mp.get("zooms", []):
        x, y = cfg.lonlat_to_local(lon, lat)
        X, Y = px(x, y)
        h = r / s
        img.crop((int(X - h), int(Y - h), int(X + h), int(Y + h))).save(OUTPUT / f"map_zoom_{name}.png")
    print("Wrote output/map_preview.png, map_preview_small.png and zoom crops")


if __name__ == "__main__":
    main()
