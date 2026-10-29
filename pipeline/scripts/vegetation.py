#!/usr/bin/env python3
"""Plant the map with trees, bushes and vines, driven by OSM land cover and the orthophoto.

Type names are a planting catalogue taken over from the stock BeamNG Italy level (holm_oak_filler_1,
olive_tree, grape_vine_group, …): labels only now; ac_trees.SPECIES maps them to card / 3D tree
kinds and heights.

  forest      canopy trees (holm oak / scraggly fillers) on a jittered ~8 m grid, bushes under
              and along the edges
  scrub       bushes, a few small trees
  orchard     olive trees in rows (5.9 m, like the Italy olive groves)
  vineyard    grape_vine_group tiles (14 m along the rows x 7.7 m), rows along the contours
  gardens     a few garden trees and cypress pairs around houses in the hills
  hedgerows   bushes along meadow/field boundaries

Nothing is placed on roads (+ verge), buildings, water or bridges.
Output: list of instances (type, x, y, z, yaw, pitch, scale) for scenery_inputs.py
(output/forest_instances.npz), and output/vegetation_points.npz for previews.
"""

import json
import math

import numpy as np
import shapely
from shapely import STRtree
from shapely.geometry import box, shape
from shapely.ops import unary_union

import vineyards as V
from common import OUTPUT, Config, Heightfield, landmark_registry, road_polygon

RNG = np.random.default_rng(7)
FOREST_TREES = [("holm_oak_filler_1", 3), ("holm_oak_filler_2", 3), ("holm_oak_filler_center", 2),
                ("scraggly_tree_filler_1", 3), ("scraggly_tree", 1)]
FOREST_BUSHES = [("holm_oak_bush", 2), ("generibush", 2), ("fluffy_bush", 2), ("scraggly_bush", 1)]
SCRUB = [("scraggly_bush", 3), ("generibush", 3), ("fluffy_bush", 2), ("scraggly_tree_filler_1", 1)]
GARDEN = [("holm_oak_city_small", 3), ("holm_oak_city_tall", 1), ("fluffy_bush", 2)]
VINE_GROUP_L, VINE_GROUP_W = 14.0, 7.7
OLIVE_SPACING = 6.5


def choose(table, n):
    names = [t for t, _ in table]
    w = np.array([w for _, w in table], float)
    return RNG.choice(names, size=n, p=w / w.sum())


def jitter_grid(geom, spacing, jitter=0.4):
    """Points on a jittered grid inside geom (vectorised point-in-polygon)."""
    x0, y0, x1, y1 = geom.bounds
    xs = np.arange(x0, x1, spacing)
    ys = np.arange(y0, y1, spacing)
    if not len(xs) or not len(ys):
        return np.zeros((0, 2))
    X, Y = np.meshgrid(xs, ys)
    P = np.column_stack([X.ravel(), Y.ravel()]) + RNG.uniform(-jitter, jitter, (X.size, 2)) * spacing
    inside = shapely.contains_xy(geom, P[:, 0], P[:, 1])
    return P[inside]


def build_exclusion(cfg):
    roads = json.loads((OUTPUT / "roads_local.geojson").read_text())["features"]
    blds = json.loads((OUTPUT / "buildings_local.geojson").read_text())["features"]
    water = json.loads((OUTPUT / "water_local.geojson").read_text())["features"]
    parts = [road_polygon(f, {"track": 1.2, "trail": 0.6}.get(f["properties"]["group"], 2.5)) for f in roads]
    parts += [shape(f["geometry"]).buffer(2.5) for f in blds]
    parts += [shape(f["geometry"]).buffer(1.5) for f in water]
    paved = OUTPUT / "paved_areas.geojson"
    if paved.exists():
        parts += [shape(f["geometry"]).buffer(1.0) for f in json.loads(paved.read_text())["features"]]
    import sites
    parts += sites.clear_polygons(cfg)
    return unary_union(parts)


def landcover(cls_names):
    lc = json.loads((OUTPUT / "landcover_local.geojson").read_text())["features"]
    return unary_union([shape(f["geometry"]) for f in lc if f["properties"]["class"] in cls_names])


def place(points, types, hf, scale_range=(0.8, 1.2), yaw=None):
    out = []
    if not len(points):
        return out
    z = hf.sample(points[:, 0], points[:, 1])
    yaws = RNG.uniform(0, 2 * math.pi, len(points)) if yaw is None else yaw
    sc = RNG.uniform(*scale_range, len(points))
    for (x, y), zz, t, a, s in zip(points, z, types, yaws, sc):
        out.append((str(t), float(x), float(y), float(zz) - 0.1, float(a), 0.0, float(s)))
    return out


def plant(cfg):
    hf = Heightfield(cfg, OUTPUT / "dem_final.tif")
    area = box(-cfg.half + 2, -cfg.half + 2, cfg.half - 2, cfg.half - 2)
    excl = build_exclusion(cfg)
    inst = []

    def usable(cls_names):
        return landcover(cls_names).intersection(area).difference(excl)

    # forests: trees where the orthophoto shows crowns (+ infill under dense canopy),
    # understory and edge bushes
    forest = usable({"forest"})
    canopy = canopy_trees(cfg)
    if canopy is None:
        pts = jitter_grid(forest, 8.0)
    else:
        cr = canopy["crowns"]
        ok = shapely.contains_xy(area, cr[:, 0], cr[:, 1]) & ~shapely.contains_xy(excl, cr[:, 0], cr[:, 1])
        in_forest = ok & shapely.contains_xy(forest, cr[:, 0], cr[:, 1])
        pts = cr[in_forest, :2]
        grid = jitter_grid(forest, 8.0)
        if len(grid) and len(pts):
            from scipy.spatial import cKDTree
            d, _ = cKDTree(pts).query(grid)
            col, row = ~canopy["transform"] * (grid[:, 0] + canopy["offset"][0], grid[:, 1] + canopy["offset"][1])
            fr = canopy["frac"]
            row = np.clip(np.asarray(row).astype(int), 0, fr.shape[0] - 1)
            col = np.clip(np.asarray(col).astype(int), 0, fr.shape[1] - 1)
            grid = grid[(d > 5.5) & (fr[row, col] > 0.5)]
            pts = np.vstack([pts, grid])
    inst += place(pts, choose(FOREST_TREES, len(pts)), hf, (0.75, 1.25))
    pts = jitter_grid(forest, 24.0)
    inst += place(pts, choose(FOREST_BUSHES, len(pts)), hf, (0.8, 1.2))
    edge = forest.difference(forest.buffer(-6))
    pts = jitter_grid(edge, 9.0)
    pts = pts[RNG.random(len(pts)) < 0.5]
    inst += place(pts, choose(FOREST_BUSHES, len(pts)), hf, (0.7, 1.1))
    n_forest = len(inst)

    scrub = usable({"scrub"})
    pts = jitter_grid(scrub, 7.0)
    inst += place(pts, choose(SCRUB, len(pts)), hf, (0.7, 1.2))

    # orchards: olive rows aligned with each parcel's long side
    orchards = landcover({"orchard"}).intersection(area)
    n_olive = 0
    for poly in getattr(orchards, "geoms", [orchards]):
        if poly.is_empty or poly.area < 100:
            continue
        with np.errstate(all="ignore"):
            rect = np.asarray(poly.minimum_rotated_rectangle.exterior.coords)
        e = np.diff(rect[:3], axis=0)
        e = e[np.argmax(np.hypot(*e.T))]
        ang = math.atan2(e[1], e[0])
        c, s = math.cos(ang), math.sin(ang)
        rot = lambda P: P @ np.array([[c, s], [-s, c]])  # noqa: E731  (to parcel frame)
        unrot = lambda P: P @ np.array([[c, -s], [s, c]])  # noqa: E731
        local = shapely.transform(poly.difference(excl), lambda P: rot(P))
        if local.is_empty:
            continue
        pts = unrot(jitter_grid(local, OLIVE_SPACING, 0.05))
        inst += place(pts, ["olive_tree"] * len(pts), hf, (0.85, 1.15))
        n_olive += len(pts)

    # vineyards: Italy grape_vine_group tiles, oriented like our contour rows
    grad = np.gradient(hf.z, cfg.mpp)
    grad = (grad[1], -grad[0])
    n_vine = 0
    for geom, direction, _ in V.parcels(cfg, hf, grad):
        d = np.array([math.cos(direction), math.sin(direction)])
        nrm = np.array([-d[1], d[0]])
        coords = np.vstack([np.asarray(g.exterior.coords) for g in getattr(geom, "geoms", [geom])])
        pd, pn = coords @ d, coords @ nrm
        for a in np.arange(pd.min() + VINE_GROUP_L / 2, pd.max(), VINE_GROUP_L):
            for b in np.arange(pn.min() + VINE_GROUP_W / 2, pn.max(), VINE_GROUP_W):
                ctr = d * a + nrm * b
                corners = [ctr + d * sx * VINE_GROUP_L / 2 + nrm * sy * VINE_GROUP_W / 2
                           for sx in (-0.45, 0.45) for sy in (-0.45, 0.45)]
                cs = np.array(corners)
                if not shapely.contains_xy(geom, cs[:, 0], cs[:, 1]).all():
                    continue
                p0, p1 = ctr - d * VINE_GROUP_L / 2, ctr + d * VINE_GROUP_L / 2
                z0, z1 = hf.sample(p0[0], p0[1]), hf.sample(p1[0], p1[1])
                pitch = math.atan2(z1 - z0, VINE_GROUP_L)
                inst.append(("grape_vine_group", float(ctr[0]), float(ctr[1]),
                             float((z0 + z1) / 2) - 0.05, direction, pitch, float(RNG.uniform(0.93, 1.0))))
                n_vine += 1

    if canopy is not None:
        # every other tree (gardens, field edges, hedgerows, town trees) where the orthophoto
        # has a crown; orchards and vineyards keep their planted rows
        cr = canopy["crowns"]
        ok = shapely.contains_xy(area, cr[:, 0], cr[:, 1]) & ~shapely.contains_xy(excl, cr[:, 0], cr[:, 1])
        ok &= ~shapely.contains_xy(forest, cr[:, 0], cr[:, 1])
        ok &= ~shapely.contains_xy(landcover({"orchard", "vineyard"}), cr[:, 0], cr[:, 1])
        in_field = shapely.contains_xy(landcover({"farmland"}), cr[:, 0], cr[:, 1])
        ok &= ~(in_field & (cr[:, 3] > FIELD_MAX_L))
        ok &= cr[:, 3] < LONE_TREE_MAX_L                 # lone trees: dark, shadowed crowns only
        P = cr[ok, :2]
        town = shapely.contains_xy(landcover({"urban", "industrial"}), P[:, 0], P[:, 1])
        types = np.where(town, np.where(RNG.random(len(P)) < 0.25, "cypress_tree", choose(GARDEN, len(P))),
                         np.where(RNG.random(len(P)) < 0.6, choose(FOREST_TREES, len(P)), choose(GARDEN, len(P))))
        inst += place(P, types, hf, (0.8, 1.2))
        n_garden = len(P)
        pts = np.zeros((0, 2))
    else:
        # gardens around hill houses + cypress pairs
        blds = json.loads((OUTPUT / "buildings_local.geojson").read_text())["features"]
        n_garden = 0
        garden_pts, garden_types = [], []
        for f in blds:
            if f["properties"]["style"] not in {"residential", "rural"} or RNG.random() > 0.35:
                continue
            g = shape(f["geometry"])
            ring = g.buffer(6).exterior
            k = RNG.integers(1, 3)
            for t in RNG.uniform(0, ring.length, k):
                p = ring.interpolate(t)
                garden_pts.append((p.x, p.y))
                garden_types.append("cypress_tree" if RNG.random() < 0.3 else choose(GARDEN, 1)[0])
        if garden_pts:
            P = np.array(garden_pts)
            keep = ~shapely.contains_xy(excl, P[:, 0], P[:, 1]) & shapely.contains_xy(area, P[:, 0], P[:, 1])
            inst += place(P[keep], np.array(garden_types)[keep], hf, (0.8, 1.15))
            n_garden = int(keep.sum())

        # hedgerows on field / meadow boundaries
        fields = landcover({"farmland", "meadow"}).intersection(area)
        bounds = fields.boundary.difference(excl.buffer(1))
        pts = []
        for line in getattr(bounds, "geoms", [bounds]):
            if line.length < 20:
                continue
            for t in np.arange(RNG.uniform(0, 8), line.length, 8.0):
                if RNG.random() < 0.45:
                    p = line.interpolate(t)
                    pts.append((p.x, p.y))
        pts = np.array(pts) if pts else np.zeros((0, 2))
        inst += place(pts, choose(SCRUB, len(pts)), hf, (0.6, 1.1))


    # hand-built landmarks: clear their gardens, then add their own trees
    from shapely.geometry import Polygon
    n_hand = 0
    for spec in landmark_registry().values():
        if spec.get("vegetation_clear"):
            clear = Polygon(spec["vegetation_clear"])
            xy = np.array([(i[1], i[2]) for i in inst])
            keep = ~shapely.contains_xy(clear, xy[:, 0], xy[:, 1])
            inst = [i for i, k in zip(inst, keep) if k]
        for t in spec.get("trees", []):
            z = float(hf.sample(np.array([t["x"]]), np.array([t["y"]]))[0])
            inst.append((t["type"], t["x"], t["y"], z - 0.1, float(RNG.uniform(0, 2 * math.pi)), 0.0, t.get("scale", 1.0)))
            n_hand += 1

    n_before = len(inst)
    inst = mountain_forest(cfg, inst)
    print(f"  thinned with distance from the climb / pines at altitude: {n_before} -> {len(inst)}")
    print(f"Vegetation: {len(inst)} instances (forest {n_forest}, olives {n_olive}, "
          f"vine groups {n_vine}, trees outside forests {n_garden}, hedgerow {len(pts)}, landmark trees {n_hand})")
    return inst


if __name__ == "__main__":
    inst = plant(Config())
    np.savez_compressed(OUTPUT / "vegetation_points.npz",
                        types=np.array([i[0] for i in inst]), xy=np.array([i[1:3] for i in inst]))


# ------------------------------------------------------------------ trees from the orthophoto

def canopy_trees(cfg, path=None):
    """Tree crowns detected in the AGEA 2024 orthophoto (data/ortho/fullmap.tif, 1.25 m):
    canopy = green, darker than open ground and textured; one tree per crown top (local
    maximum of the sunlit canopy, >= 5 m apart). Returns (x, y, crown score) in local metres."""
    import rasterio
    from scipy.ndimage import gaussian_filter, maximum_filter, uniform_filter
    from common import DATA
    path = path or DATA / "ortho" / "fullmap.tif"
    if not path.exists():
        return None
    with rasterio.open(path) as src:
        img = src.read().astype(np.float32).transpose(1, 2, 0)
        tr = src.transform
    R, G, B = img[..., 0], img[..., 1], img[..., 2]
    L = img.mean(-1)
    exg = (2 * G - R - B) / (R + G + B + 1)
    std = np.sqrt(np.maximum(uniform_filter(L * L, 5) - uniform_filter(L, 5) ** 2, 0))
    Ls = gaussian_filter(L, 2.0)
    canopy = (gaussian_filter(exg, 1.0) > CANOPY_EXG) & (Ls < CANOPY_MAX_L) & (std > CANOPY_STD)
    frac = uniform_filter(canopy.astype(np.float32), 5)
    tops = gaussian_filter(L, 1.5) * (frac > 0.55)
    peaks = (tops == maximum_filter(tops, size=CROWN_MIN_PX)) & (tops > 0) & canopy
    r, c = np.nonzero(peaks)
    x, y = tr * (c + 0.5, r + 0.5)
    x, y = np.asarray(x) - cfg.center_e, np.asarray(y) - cfg.center_n
    return {"crowns": np.column_stack([x, y, frac[r, c], Ls[r, c]]), "frac": frac, "transform": tr,
            "offset": (cfg.center_e, cfg.center_n)}


CANOPY_EXG = 0.08      # excess-green index
CANOPY_MAX_L = 100.0   # mean RGB (smoothed): canopy is darker than meadows and fields
CANOPY_STD = 12.0      # crowns are textured
CROWN_MIN_PX = 4       # 5 m between crown tops at 1.25 m/px
FIELD_MAX_L = 70.0     # in farmland only darker crowns count (dark crops are not trees)
LONE_TREE_MAX_L = 95.0 # outside woods


# ------------------------------------------------------------------ mountain map (hill climb)

NEAR_M, FAR_M, FAR_KEEP = 350.0, 1200.0, 0.3     # full density near the climb, thinned far away
PINE_FROM_Z, PINE_FULL_Z, PINE_MAX = 900.0, 1250.0, 0.6
SUMMIT_R, SUMMIT_PINE = 300.0, 0.85     # conifer stands around the finish (config.json "vegetation" overrides)
BROADLEAF = {"holm_oak_filler_1", "holm_oak_filler_2", "holm_oak_filler_center", "scraggly_tree_filler_1",
             "scraggly_tree"}


def mountain_forest(cfg, inst):
    """Hill-climb map: what the driver sees is a corridor along the climb, so far from it trees
    are thinned (kept ones a little larger, so the canopy still closes) to keep the forest
    count and loading time moderate; above ~950 m some broadleaf trees become pines (beech /
    spruce / larch woods on the higher slopes; the Italy set has no spruce, its Scots pine is the
    nearest conifer)."""
    route = cfg["roads"].get("route")
    path = OUTPUT / f"route_{route}.geojson"
    if not path.exists():
        return inst
    from scipy.ndimage import distance_transform_edt
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    from shapely.geometry import LineString
    cell = 8.0
    n = int(2 * cfg.half / cell)
    tr = from_origin(-cfg.half, cfg.half, cell, cell)
    line = LineString(json.loads(path.read_text())["features"][0]["properties"]["local_xy"])
    on = rasterize([(line.buffer(cell), 1)], out_shape=(n, n), transform=tr, fill=0, dtype="uint8")
    dist = distance_transform_edt(on == 0) * cell
    rng = np.random.default_rng(11)
    finish = np.asarray(json.loads(path.read_text())["features"][0]["properties"]["local_xy"][-1])
    vc = cfg.raw.get("vegetation", {})
    summit_r, summit_pine = float(vc.get("summit_r", SUMMIT_R)), float(vc.get("summit_pine", SUMMIT_PINE))
    out = []
    for typ, x, y, z, yaw, pitch, scale in inst:
        r = min(max(int((cfg.half - y) / cell), 0), n - 1)
        c = min(max(int((x + cfg.half) / cell), 0), n - 1)
        d = dist[r, c]
        if typ in BROADLEAF or typ in {"holm_oak_bush", "generibush", "fluffy_bush", "scraggly_bush"}:
            u = np.clip((d - NEAR_M) / (FAR_M - NEAR_M), 0, 1)
            keep = 1 - u * (1 - FAR_KEEP)
            if typ not in BROADLEAF:
                keep *= 1 - 0.5 * u                     # understory matters even less far away
            if rng.random() > keep:
                continue
            scale *= 1 + 0.2 * u
        if typ in BROADLEAF:
            p = PINE_MAX * np.clip((z - PINE_FROM_Z) / (PINE_FULL_Z - PINE_FROM_Z), 0, 1)
            if math.hypot(x - finish[0], y - finish[1]) < summit_r:
                p = max(p, summit_pine)
            if rng.random() < p:
                typ = "scots_pine_tree" if rng.random() < 0.6 else "scots_pine_filler"
        out.append((typ, x, y, z, yaw, pitch, scale))
    return out
