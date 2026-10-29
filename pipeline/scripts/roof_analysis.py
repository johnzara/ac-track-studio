#!/usr/bin/env python3
"""Roof shape and colour of every generated building, read from the AGEA 2024 orthophoto.

The orthophoto is a summer late-morning image: each roof facet faces the sun at its own angle,
so a pitched roof shows as two (gable) or four (hip) areas of near-constant brightness split
along the ridge / hip lines, and a flat roof as one uniform area. For each block with a
roughly rectangular outline, the roof pixels (footprint eroded 0.6 m) are explained by each
candidate model, piecewise constant per facet:

  flat | gable, ridge along the long axis | gable, ridge along the short axis | hip

and the model with the best penalised fit (BIC) wins; the facet split of a gable also tells
which way the ridge runs. Roof colour: the median colour of the roof pixels, binned to a
small palette (terracotta light/mid/dark, grey, dark/metal, light membrane).

    roof_analysis.py      fetch the 25 cm orthophoto tiles over built-up areas (cached in
                          data/ortho/tiles/) and write output/roofs.json
Run after build_buildings.py; buildings_mesh.py reads output/roofs.json.
"""

import io
import json
import math
import time

import numpy as np
import requests
import shapely
import shapely.affinity
from PIL import Image
from rasterio.features import rasterize
from rasterio.transform import from_origin
from shapely.geometry import shape

from common import DATA, OUTPUT, Config

WMS = "https://idt2-geoserver.regione.veneto.it/geoserver/ows"
LAYER = "rv:ortofoto_agea_2024"
RES = 0.25          # m / px
TILE_M = 256.0      # 1024 px tiles
MIN_PX = 60         # roof pixels needed for a decision
RECT_MIN = 0.78     # outline area / its minimum rotated rectangle

PALETTE = {          # roof material -> reference colour in the orthophoto (sRGB)
    "roof_coppi": (178, 104, 78),
    "roof_coppi_light": (196, 132, 104),
    "roof_coppi_dark": (138, 84, 66),
    "roof_grey": (128, 126, 122),
    "roof_dark": (74, 74, 76),
    "roof_light": (196, 194, 188),
}


def tile_path(ix, iy):
    return DATA / "ortho" / "tiles" / f"t25_{ix}_{iy}.jpg"


def fetch_tile(cfg, ix, iy):
    p = tile_path(ix, iy)
    if p.exists():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    x0, y0 = ix * TILE_M + cfg.center_e, iy * TILE_M + cfg.center_n
    n = int(TILE_M / RES)
    params = {"SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetMap", "LAYERS": LAYER, "STYLES": "",
              "SRS": "EPSG:32632", "BBOX": f"{x0},{y0},{x0 + TILE_M},{y0 + TILE_M}", "WIDTH": n, "HEIGHT": n,
              "FORMAT": "image/jpeg"}
    for attempt in range(5):
        try:
            r = requests.get(WMS, params=params, timeout=120)
            if r.ok and r.headers.get("content-type", "").startswith("image"):
                p.write_bytes(r.content)
                return
        except requests.RequestException:
            pass
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"tile {ix},{iy} failed")


def load_tile(ix, iy):
    return np.asarray(Image.open(io.BytesIO(tile_path(ix, iy).read_bytes())).convert("RGB"), np.float32)


def facet_labels(s, t, a, b, model):
    """Facet index per pixel; s along the long axis (half-length a), t across (half-width b)."""
    if model == "flat":
        return np.zeros_like(s, int)
    if model == "gable_long":                   # ridge along the long axis: facets either side
        return (t > 0).astype(int)
    if model == "gable_short":                  # ridge across: facets at each end
        return (s > 0).astype(int)
    # hip: end facets where the distance to the short edge is smaller
    end = (a - np.abs(s)) < (b - np.abs(t))
    return np.where(end, 2 + (s > 0), (t > 0).astype(int))


def classify(px_rgb, s, t, a, b, warm=True):
    lum = px_rgb @ np.array([0.3, 0.59, 0.11])
    n = len(lum)
    best = None
    models = (("gable_long", 2), ("gable_short", 2), ("hip", 4)) if warm else \
        (("flat", 1), ("gable_long", 2), ("gable_short", 2), ("hip", 4))   # coppi roofs are never flat
    for model, k in models:
        lab = facet_labels(s, t, a, b, model)
        rss = 0.0
        for f in range(k):
            m = lab == f
            if m.sum() < 4:
                rss = None
                break
            rss += ((lum[m] - lum[m].mean()) ** 2).sum()
        if rss is None:
            continue
        bic = n * math.log(max(rss / n, 1e-6)) + 4 * k * math.log(n)
        if best is None or bic < best[0]:
            best = (bic, model)
    return best[1] if best else "hip"


def roof_colour(px_rgb):
    """Warm (terracotta) roofs by lightness; cold ones: dark tiles/metal, grey cement tiles,
    light membrane/gravel. Thresholds from the AGEA 2024 summer image, where coppi read pale."""
    med = np.median(px_rgb, 0)
    warmth, lum = med[0] - med[2], float(med.mean())
    if warmth >= WARM:
        name = "roof_coppi_light" if lum > 168 else "roof_coppi" if lum > 125 else "roof_coppi_dark"
    else:
        name = "roof_dark" if lum < 95 else "roof_grey" if lum < 168 else "roof_light"
    return name, [int(v) for v in med]


WARM = 24                 # R - B of the roof's median colour


def align(grad, tr_inv, ring, max_m=2.0, step=0.25):
    """Shift (dx, dy) that puts the footprint outline on the strongest roof edges in the
    orthophoto (the photo leans a little away from the vertical)."""
    from scipy.ndimage import map_coordinates
    shifts = np.arange(-max_m, max_m + 1e-6, step)
    col, row = tr_inv * (ring[:, 0], ring[:, 1])
    col, row = np.asarray(col), np.asarray(row)
    best, best_s = None, (0.0, 0.0)
    base = None
    for dx in shifts:
        for dy in shifts:
            v = map_coordinates(grad, [row - dy / RES, col + dx / RES], order=1, mode="nearest").mean()
            if dx == 0 and dy == 0:
                base = v
            if best is None or v > best:
                best, best_s = v, (dx, dy)
    return best_s if best > 1.15 * base else (0.0, 0.0)


def analyse(cfg=None, only=None):
    cfg = cfg or Config()
    feats = json.loads((OUTPUT / "buildings_local.geojson").read_text())["features"]
    todo = []
    for i, f in enumerate(feats):
        if only is not None and i not in only:
            continue
        g = shape(f["geometry"])
        if g.geom_type != "Polygon" or g.area < 25:
            continue
        with np.errstate(all="ignore"):
            rect = g.minimum_rotated_rectangle
        todo.append((i, g, rect))
    tiles = sorted({(int(math.floor(x / TILE_M)), int(math.floor(y / TILE_M)))
                    for _, g, _ in todo for x, y in [g.centroid.coords[0]]})
    print(f"Roof analysis: {len(todo)} buildings, {len(tiles)} orthophoto tiles (25 cm)")
    for k, (ix, iy) in enumerate(tiles):
        fetch_tile(cfg, ix, iy)
        if k % 50 == 49:
            print(f"  fetched {k + 1}/{len(tiles)}")
    by_tile = {}
    for item in todo:
        c = item[1].centroid
        by_tile.setdefault((int(math.floor(c.x / TILE_M)), int(math.floor(c.y / TILE_M))), []).append(item)
    out, stats = {}, {}
    from scipy.ndimage import gaussian_gradient_magnitude
    for (ix, iy), items in by_tile.items():
        img = load_tile(ix, iy)
        tr = from_origin(ix * TILE_M, (iy + 1) * TILE_M, RES, RES)
        grad = gaussian_gradient_magnitude(img.mean(-1), 1.0)
        for i, g, rect in items:
            ring = np.array([g.exterior.interpolate(d).coords[0] for d in np.arange(0, g.exterior.length, 0.5)])
            dx, dy = align(grad, ~tr, ring)
            g = shapely.affinity.translate(g, dx, dy)
            rect = shapely.affinity.translate(rect, dx, dy)
            core = g.buffer(-0.6)
            if core.is_empty:
                continue
            mask = rasterize([(core, 1)], out_shape=img.shape[:2], transform=tr, fill=0, dtype="uint8").astype(bool)
            if mask.sum() < MIN_PX:
                continue
            rows, cols = np.nonzero(mask)
            xs = ix * TILE_M + (cols + 0.5) * RES
            ys = (iy + 1) * TILE_M - (rows + 0.5) * RES
            px_rgb = img[rows, cols]
            colour, med = roof_colour(px_rgb)
            rec = {"colour": colour, "rgb": med}
            rectangular = isinstance(rect, type(g)) and g.area / max(rect.area, 1e-6) >= RECT_MIN
            if rectangular:
                c = np.asarray(rect.exterior.coords)[:4]
                e1, e2 = c[1] - c[0], c[2] - c[1]
                if np.linalg.norm(e1) < np.linalg.norm(e2):
                    e1, e2 = e2, e1
                u = e1 / np.linalg.norm(e1)
                v = np.array([-u[1], u[0]])
                ctr = c.mean(0)
                a, b = np.linalg.norm(e1) / 2, np.linalg.norm(e2) / 2
                d = np.column_stack([xs, ys]) - ctr
                s, t = d @ u, d @ v
                model = classify(px_rgb, s, t, a, b, warm=colour.startswith("roof_coppi"))
                if model == "gable_long":
                    rec.update(roof="gable", ridge=[float(u[0]), float(u[1])])
                elif model == "gable_short":
                    rec.update(roof="gable", ridge=[float(v[0]), float(v[1])])
                else:
                    rec["roof"] = model
            out[str(i)] = rec
            stats[rec.get("roof", "complex")] = stats.get(rec.get("roof", "complex"), 0) + 1
    (OUTPUT / "roofs.json").write_text(json.dumps(out))
    apply(feats, out)
    (OUTPUT / "buildings_local.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    cols = {}
    for r in out.values():
        cols[r["colour"]] = cols.get(r["colour"], 0) + 1
    print(f"Roofs: {stats}; colours {cols}")
    return out



def apply(feats, roofs):
    """Write the analysis into the building blocks: roof shape, ridge, roof material, and the
    side facing the nearest road (garage doors, balconies)."""
    from shapely import STRtree
    from shapely.geometry import LineString, Point
    roads = json.loads((OUTPUT / "roads_local.geojson").read_text())["features"]
    lines = [LineString([c[:2] for c in f["geometry"]["coordinates"]]) for f in roads
             if f["properties"]["group"] in {"major", "minor"}]
    tree = STRtree(lines)
    for i, f in enumerate(feats):
        p = f["properties"]
        r = roofs.get(str(i))
        if r and p["roof"] == "flat" and r["colour"].startswith("roof_coppi") and p["style"] in {"residential", "rural", "commercial"}:
            p["roof"] = "hip"                     # a terracotta roof is pitched, whatever its width
        if r and p["roof"] not in {"flat", "pyramid"} and p["style"] not in {"industrial", "military", "tower", "church"}:
            shape_ = r.get("roof", "hip")
            if shape_ == "flat":
                p["roof"], p["roof_mat"] = "flat", "roof_light" if r["colour"] == "roof_light" else "roof_flat"
            else:
                p["roof"] = shape_
                p["roof_mat"] = r["colour"] if r["colour"] != "roof_light" else "roof_grey"
                if shape_ == "gable":
                    p["ridge"] = r["ridge"]
        g = shape(f["geometry"])
        c = g.centroid
        j = tree.query_nearest(c)
        if len(j):
            q = lines[j[0]].interpolate(lines[j[0]].project(c))
            v = np.array([q.x - c.x, q.y - c.y])
            if np.linalg.norm(v) > 0 and g.boundary.distance(Point(q.x, q.y)) < 40:
                p["front"] = [round(float(x), 3) for x in v / np.linalg.norm(v)]


if __name__ == "__main__":
    analyse()
