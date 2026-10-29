#!/usr/bin/env python3
"""Terrain colour (base) map from the AGEA 2024 orthophoto (data/ortho/fullmap.tif, 1.25 m,
fetched by run_all.sh), for the terrain materials' baseColorBaseTex: what the ground looks like
from mid and far distance (near the camera the materials' detail textures take over).

* roofs are removed: building footprints (padded for the photo's lean) are filled from the
  surrounding ground, otherwise roof colours would be painted on the ground beside buildings
* the photo's shadows are softened, contrast is eased a little
* north-up
Output: output/terrain_base_ortho.png (4096 x 4096)
"""

import json
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.features import rasterize
from scipy.ndimage import gaussian_filter
from shapely.geometry import shape

from common import DATA, OUTPUT, Config

OUT_PX = 4096


def build(cfg=None):
    cfg = cfg or Config()
    path = DATA / "ortho" / "fullmap.tif"
    if not path.exists():
        return None
    with rasterio.open(path) as src:
        img = src.read().astype(np.float32).transpose(1, 2, 0)
        tr = src.transform
    # roofs: building footprints padded 3 m, filled by normalised convolution
    feats = json.loads((OUTPUT / "buildings_local.geojson").read_text())["features"]
    shapes_ = [(shape(f["geometry"]).buffer(3.0), 1) for f in feats]
    from affine import Affine
    loc_tr = Affine(tr.a, tr.b, tr.c - cfg.center_e, tr.d, tr.e, tr.f - cfg.center_n)
    mask = rasterize(shapes_, out_shape=img.shape[:2], transform=loc_tr, fill=0, dtype="uint8").astype(bool)
    keep = (~mask).astype(np.float32)
    filled = img.copy()
    for sigma in (2, 6, 18):
        num = np.stack([gaussian_filter(img[..., k] * keep, sigma) for k in range(3)], -1)
        den = gaussian_filter(keep, sigma)[..., None]
        est = num / np.maximum(den, 1e-4)
        todo = mask & (den[..., 0] > 0.05)
        filled[todo] = est[todo]
        keep = np.maximum(keep, todo.astype(np.float32))
        mask = mask & ~todo
    img = filled
    # soften shadows / ease contrast towards the local mean
    lum = img.mean(-1)
    local = gaussian_filter(lum, 12)
    lift = np.clip((local - lum) / 60.0, 0, 1)[..., None]          # darker than surroundings = shadow
    img = img + (local[..., None] - lum[..., None]) * 0.5 * lift
    img = 255 * (np.clip(img, 0, 255) / 255) ** 0.92
    out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).resize((OUT_PX, OUT_PX), Image.LANCZOS)
    out.save(OUTPUT / "terrain_base_ortho.png")
    print(f"Terrain base colour from the orthophoto: {OUT_PX} px ({cfg.half * 2 / OUT_PX:.2f} m/px), roofs removed")
    return np.asarray(out)




# ---------------------------------------------------------------------------------------------
# Base colour map for the terrain (scenery_inputs.py -> output/terrain_base_final.png): the terrain
# materials apply their detail textures at only 25-50 %, so the base colour sets the look up close.
# The raw orthophoto (saturated summer greens, 2.5 m pixels) read as "very bright green, low res".
# So: a muted, smooth colour per land-cover layer (ground_palette.json: measured on the stock
# BeamNG Italy terrain when the pipeline still made BeamNG levels), modulated by the orthophoto's
# blurred brightness and a little of its hue, so fields, woods and vineyards keep their pattern.

PALETTE = Path(__file__).resolve().parent / "ground_palette.json"


def ground_palette():
    """sRGB base colour per land-cover layer (build_terrain.LAYERS names)."""
    if not PALETTE.exists():
        raise SystemExit(f"{PALETTE} is missing (the ground colour per land-cover layer)")
    return {k: v for k, v in json.loads(PALETTE.read_text()).items() if not k.startswith("_")}


def base_colour_map(cfg, layer_index, layers, ortho=None):
    """4096 x 4096 RGB base colour map (north-up) from the layer index and the orthophoto."""
    pal = ground_palette()
    fallback = pal.get("grass", [89, 88, 47])
    lut = np.array([pal.get(name, fallback) for name in layers], np.float32)
    n = layer_index.shape[0]
    road = np.isin(layer_index, [layers.index(k) for k in ("asphalt", "asphalt_main") if k in layers])
    # under asphalt the base colour only shows in the edge blend band: give it the verge's colour
    idx = layer_index.copy()
    if road.any():
        from scipy.ndimage import distance_transform_edt
        _, (ri, ci) = distance_transform_edt(road, return_indices=True)
        idx = layer_index[ri, ci]
    col = lut[idx]
    if ortho is not None:
        o = np.asarray(Image.fromarray(ortho).resize((n, n), Image.BILINEAR), np.float32) if ortho.shape[0] != n else ortho.astype(np.float32)
        o = np.stack([gaussian_filter(o[..., k], 1.5) for k in range(3)], -1)
        lum_o = o.mean(-1) + 1.0
        lum_c = col.mean(-1) + 1.0
        out = np.empty_like(col)
        for k in np.unique(idx):
            m = idx == k
            ref = float(np.median(lum_o[m]))
            v = np.clip(lum_o[m] / ref - 1.0, -0.35, 0.35)          # brightness pattern of the photo
            hue_o = o[m] / lum_o[m][:, None]
            hue_c = col[m] / lum_c[m][:, None]
            hue = 0.8 * hue_c + 0.2 * hue_o                          # a little of the photo's hue
            out[m] = hue * (lum_c[m] * (1 + 0.6 * v))[:, None]
        col = out
    col = np.stack([gaussian_filter(col[..., k], 0.8) for k in range(3)], -1)   # soften 2.5 m cell edges
    return np.clip(col, 0, 255).astype(np.uint8)


if __name__ == "__main__":
    build()
