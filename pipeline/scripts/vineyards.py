#!/usr/bin/env python3
"""Vineyard parcels and rows in every OSM / GeoDBT vineyard, for vegetation.py (parcels) and
ac_vineyards.py (rows_in: the 3D rows near the road).

Model (from the Street View survey): espalier/Guyot rows ~2.6 m apart following the slope
contours on slopes, wooden posts, a leafy wall from ~0.5 to ~1.9 m. Vineyards are split into
~70 m parcels (each with its own row direction and headland) and kept clear of roads and
buildings.
"""

import json
import math

import numpy as np
from shapely.geometry import LineString, box, shape
from shapely.ops import unary_union
from shapely.prepared import prep

from common import OUTPUT, road_polygon

ROW_SPACING_M = 2.6
PARCEL_M = 70.0
HEADLAND_M = 1.5
CONTOUR_SLOPE = 0.07       # above this mean slope, rows follow the contours
PIECE_SHORT = 4.0          # shortest row kept (m)


# ---------------------------------------------------------------- planting

def parcels(cfg, hf, grad):
    lc = json.loads((OUTPUT / "landcover_local.geojson").read_text())["features"]
    vines = unary_union([shape(f["geometry"]) for f in lc if f["properties"]["class"] == "vineyard"])
    vines = vines.intersection(box(-cfg.half + 5, -cfg.half + 5, cfg.half - 5, cfg.half - 5))
    roads = json.loads((OUTPUT / "roads_local.geojson").read_text())["features"]
    blds = json.loads((OUTPUT / "buildings_local.geojson").read_text())["features"]
    obstacles = unary_union(
        [road_polygon(f, 1.5) for f in roads]
        + [shape(f["geometry"]).buffer(2.5) for f in blds])
    vines = vines.difference(obstacles)
    rng = np.random.default_rng(42)
    out = []
    x0, y0, x1, y1 = vines.bounds
    pv = prep(vines)
    for cx in np.arange(x0 - x0 % PARCEL_M, x1, PARCEL_M):
        for cy in np.arange(y0 - y0 % PARCEL_M, y1, PARCEL_M):
            cell = box(cx, cy, cx + PARCEL_M, cy + PARCEL_M)
            if not pv.intersects(cell):
                continue
            geom = vines.intersection(cell).buffer(-HEADLAND_M)
            if geom.is_empty or geom.area < 120:
                continue
            # mean downhill gradient over the parcel
            px, py = np.meshgrid(np.linspace(cx + 5, cx + PARCEL_M - 5, 8),
                                 np.linspace(cy + 5, cy + PARCEL_M - 5, 8))
            gx = sample_grid(grad[0], cfg, px.ravel(), py.ravel()).mean()
            gy = sample_grid(grad[1], cfg, px.ravel(), py.ravel()).mean()
            slope = math.hypot(gx, gy)
            if slope > CONTOUR_SLOPE:
                direction = math.atan2(gx, -gy)          # perpendicular to the gradient
            else:
                with np.errstate(all="ignore"):
                    rect = np.asarray(geom.minimum_rotated_rectangle.exterior.coords)
                edges = np.diff(rect[:3], axis=0)
                e = edges[np.argmax(np.hypot(*edges.T))]
                direction = math.atan2(e[1], e[0])
            direction += math.radians(rng.normal(0, 3))
            out.append((geom, direction, rng.uniform(0, ROW_SPACING_M)))
    return out


def sample_grid(arr, cfg, x, y):
    n = arr.shape[0]
    c = np.clip(np.round((x + cfg.half) / cfg.mpp).astype(int), 0, n - 1)
    r = np.clip(np.round((n - 1) - (y + cfg.half) / cfg.mpp).astype(int), 0, n - 1)
    return arr[r, c]


def rows_in(geom, direction, phase):
    d = np.array([math.cos(direction), math.sin(direction)])
    nrm = np.array([-d[1], d[0]])
    coords = np.vstack([np.asarray(g.exterior.coords) for g in getattr(geom, "geoms", [geom])])
    proj_n = coords @ nrm
    proj_d = coords @ d
    lines = []
    for off in np.arange(proj_n.min() + phase, proj_n.max(), ROW_SPACING_M):
        a = nrm * off + d * (proj_d.min() - 1)
        b = nrm * off + d * (proj_d.max() + 1)
        cut = geom.intersection(LineString([a, b]))
        for part in getattr(cut, "geoms", [cut]):
            if part.geom_type == "LineString" and part.length >= PIECE_SHORT:
                lines.append(np.asarray(part.coords))
    return lines
