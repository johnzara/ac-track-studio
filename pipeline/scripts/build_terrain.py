#!/usr/bin/env python3
"""The final ground: heights and land-cover layers on the terrain grid.

1. Flatten every road into the terrain: across its width (+ shoulder) the ground is set to the
   road's smoothed profile, then blended back to the natural terrain beside it.
2. Rasterize land cover (+ roads, + steep slopes as rock) into one mask per layer and an index.

Input:  output/dem_natural.tif, output/roads_local.geojson, output/landcover_local.geojson
Output: output/dem_final.tif, output/terrain_layers/*.png, output/terrain_layer_index.npy,
        output/landcover_preview.png
"""

import json

import numpy as np
import rasterio
from PIL import Image
from rasterio.features import rasterize
from rasterio.transform import from_origin
from shapely.geometry import shape

from build_osm import LANDCOVER_ORDER
from common import OFFROAD, OUTPUT, Config, landmark_registry, road_polygon, road_widths

JUNCTION_FILLET_M = 2.5
CLIFF_SLOPE_DEG = 50.0
CUT_SLOPE_DEG = 40.0
LAYER_COLOURS = {
    "grass": (120, 160, 80), "farmland": (190, 170, 110), "meadow": (140, 190, 90),
    "orchard": (110, 150, 60), "vineyard": (150, 120, 160), "scrub": (100, 120, 70),
    "forest": (40, 90, 40), "urban": (170, 160, 150), "industrial": (150, 140, 160),
    "rock": (120, 110, 100), "water": (60, 110, 200), "asphalt": (60, 60, 60),
    "gravel": (200, 190, 160), "asphalt_main": (45, 45, 48),
}
LAYERS = ["grass"] + LANDCOVER_ORDER + ["asphalt", "gravel", "asphalt_main"]


def local_transform(cfg):
    """Raster transform in local coordinates (pixel centres = heightmap samples)."""
    s, n, half = cfg.mpp, cfg.size_px, cfg.half
    return from_origin(-half - s / 2, -half + (n - 1) * s + s / 2, s, s)


RANK_W = {"major": 4.0, "minor": 3.0, "track": 1.5, "trail": 0.5}
# 1 m terrain cells: a flat shoulder of ~1.5 cells beyond the edge (gutter / verge), then the
# ground blends back to the natural slope (cut faces and embankments of a mountain road)
SHOULDER_M = {"major": 1.8, "minor": 1.5, "track": 1.0, "trail": 0.6}
FALLOFF_M = {"major": 6.0, "minor": 5.0, "track": 3.5, "trail": 2.5}
PIECE_M = 40.0          # roads are shaped piece by piece, so hairpin legs blend into each other
PIECE_OVERLAP_M = 6.0
CORE_M = 0.3                                                             # carriageway + this


def road_pieces(roads):
    """(feature, nodes, widths) in pieces of about PIECE_M along each road, sharing end nodes.
    Within one piece every terrain sample takes the height of its nearest point; between pieces
    (and roads) heights blend by edge distance, so the ground between two legs of a hairpin
    slopes from one to the other instead of stepping at the midline."""
    for feat in roads:
        p = feat["properties"]
        if p["tunnel"] or p["bridge"]:
            continue
        pts = np.asarray(feat["geometry"]["coordinates"], float)
        widths = road_widths(feat)
        s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(pts[:, :2], axis=0), axis=1))])
        n = max(1, int(round(s[-1] / PIECE_M)))
        cuts = np.unique(np.searchsorted(s, np.linspace(0, s[-1], n + 1)).clip(0, len(pts) - 1))
        cuts[0], cuts[-1] = 0, len(pts) - 1
        for a, b in zip(cuts[:-1], cuts[1:]):
            if b <= a:
                continue
            # internal piece ends overlap the neighbour by PIECE_OVERLAP_M and have no round cap
            # (a cap would carry the end node's height a half-width into the next piece)
            a2 = a if a == 0 else min(a - 1, int(np.searchsorted(s, s[a] - PIECE_OVERLAP_M)))
            b2 = b if b == len(pts) - 1 else min(max(b + 1, int(np.searchsorted(s, s[b] + PIECE_OVERLAP_M))), len(pts) - 1)
            yield feat, pts[a2:b2 + 1], widths[a2:b2 + 1], a == 0, b == len(pts) - 1


def flatten_roads(cfg, dem, roads):
    """Terrain shaped to the road profiles (road_profiles.py).

    For each road: distance d of every nearby terrain sample to the centreline, the road's
    height at the nearest point, and its half-width hw there (per-node widths).
      * carriageway (d <= hw + 0.3): the sample takes the height of the most important road
        whose carriageway covers it: a carriageway is never cut by another road's surface
      * flat shoulder up to SHOULDER_M beyond the edge (3.6 m for paved roads, the diagonal of
        a 2.5 m terrain cell, so every terrain triangle touching the road is flat), then a
        smooth falloff to the natural ground
      * outside carriageways, where several roads compete (junction corners, roads side by
        side at different heights), heights blend smoothly (inverse edge-distance weights,
        scaled by road rank) instead of the nearest road winning: no cliffs at corners
    """
    n, s, half = cfg.size_px, cfg.mpp, cfg.half
    core_rank = np.full(dem.shape, -1.0, np.float32)
    core_z = np.zeros_like(dem)
    core_e = np.full(dem.shape, np.inf, np.float32)
    sum_kz = np.zeros(dem.shape, np.float64)
    sum_k = np.zeros(dem.shape, np.float64)
    weight = np.zeros_like(dem)
    for feat, pts, widths, cap0, cap1 in road_pieces(roads):
        p = feat["properties"]
        grp = p["group"]
        shoulder, falloff = SHOULDER_M.get(grp, 2.0), FALLOFF_M.get(grp, 5.0)
        rank = 6.0 if p.get("climb") else RANK_W.get(grp, 1.0) + (0.5 if p.get("roundabout") else 0.0)
        reach = float(widths.max()) / 2 + shoulder + falloff
        x0, x1 = pts[:, 0].min() - reach, pts[:, 0].max() + reach
        y0, y1 = pts[:, 1].min() - reach, pts[:, 1].max() + reach
        c0 = max(int(np.floor((x0 + half) / s)), 0)
        c1 = min(int(np.ceil((x1 + half) / s)), n - 1)
        j0 = max(int(np.floor((y0 + half) / s)), 0)
        j1 = min(int(np.ceil((y1 + half) / s)), n - 1)
        if c0 > c1 or j0 > j1:
            continue
        gx = np.arange(c0, c1 + 1) * s - half
        gy = np.arange(j0, j1 + 1) * s - half
        X, Y = np.meshgrid(gx, gy)
        D = np.full(X.shape, np.inf)
        Z = np.zeros(X.shape)
        HW = np.zeros(X.shape)
        last = len(pts) - 2
        for si, (a, b, wa, wb) in enumerate(zip(pts[:-1], pts[1:], widths[:-1], widths[1:])):
            # this segment's window inside the road's box (rows here are south-up: gy ascending)
            sc0 = max(int(np.floor((min(a[0], b[0]) - reach - gx[0]) / s)), 0)
            sc1 = min(int(np.ceil((max(a[0], b[0]) + reach - gx[0]) / s)), len(gx) - 1)
            sr0 = max(int(np.floor((min(a[1], b[1]) - reach - gy[0]) / s)), 0)
            sr1 = min(int(np.ceil((max(a[1], b[1]) + reach - gy[0]) / s)), len(gy) - 1)
            if sc0 > sc1 or sr0 > sr1:
                continue
            xs, ys = X[sr0:sr1 + 1, sc0:sc1 + 1], Y[sr0:sr1 + 1, sc0:sc1 + 1]
            ab = b[:2] - a[:2]
            L2 = float(ab @ ab) or 1e-9
            t_raw = ((xs - a[0]) * ab[0] + (ys - a[1]) * ab[1]) / L2
            t = np.clip(t_raw, 0, 1)
            d = np.hypot(xs - (a[0] + t * ab[0]), ys - (a[1] + t * ab[1]))
            if si == 0 and not cap0:
                d = np.where(t_raw < 0, np.inf, d)
            if si == last and not cap1:
                d = np.where(t_raw > 1, np.inf, d)
            Dw = D[sr0:sr1 + 1, sc0:sc1 + 1]
            better = d < Dw
            Dw[better] = d[better]
            Z[sr0:sr1 + 1, sc0:sc1 + 1][better] = (a[2] + t * (b[2] - a[2]))[better]
            HW[sr0:sr1 + 1, sc0:sc1 + 1][better] = ((wa + t * (wb - wa)) / 2)[better]
        e = D - HW                                   # distance beyond the carriageway edge
        inside = np.isfinite(D) & (e <= shoulder + falloff)
        if not inside.any():
            continue
        u = np.clip((shoulder + falloff - e) / falloff, 0, 1)
        w = np.where(inside, u * u * (3 - 2 * u), 0.0)
        k = np.where(inside, rank / (np.maximum(e, 0.0) + 0.3) ** 3, 0.0)
        rows = slice(n - 1 - j1, n - j0)             # grid rows are north-up
        cols = slice(c0, c1 + 1)
        w, k, Zf, ef = w[::-1], k[::-1], Z[::-1], e[::-1]
        W = weight[rows, cols]
        np.maximum(W, w.astype(np.float32), out=W)
        sum_kz[rows, cols] += k * Zf
        sum_k[rows, cols] += k
        core = (ef <= CORE_M) & np.isfinite(ef)
        CR, CZ, CE = core_rank[rows, cols], core_z[rows, cols], core_e[rows, cols]
        # the more important road wins; between equals (pieces of one road, hairpin legs) the
        # nearer carriageway
        take = core & ((rank > CR) | ((rank == CR) & (ef < CE)))
        CR[take] = rank
        CZ[take] = Zf[take]
        CE[take] = ef[take]
    blended = np.where(sum_k > 0, sum_kz / np.maximum(sum_k, 1e-12), dem).astype(np.float32)
    target = np.where(core_rank >= 0, core_z, blended)
    out = dem * (1 - weight) + target * weight
    changed = weight > 0
    delta = np.abs(out - dem)[changed]
    print(f"Flattened roads over {changed.mean():.1%} of the terrain; "
          f"median change {np.median(delta):.2f} m, 99th pct {np.percentile(delta, 99):.2f} m")
    return out.astype(np.float32), weight, core_rank >= 0


def apply_pads(cfg, dem):
    """Level the ground where hand-built landmarks need it (landmarks.json "terrain_pads": a
    ring of [x, y, z]; the ground inside follows the plane through those points, blending back
    to the terrain over "falloff" metres)."""
    from scipy.ndimage import distance_transform_edt
    from shapely.geometry import Polygon
    tr = local_transform(cfg)
    n = cfg.size_px
    for lid, spec in landmark_registry().items():
        for pad in spec.get("terrain_pads", []):
            ring = np.asarray(pad["ring"], float)
            A = np.column_stack([ring[:, 0], ring[:, 1], np.ones(len(ring))])
            coef = np.linalg.lstsq(A, ring[:, 2], rcond=None)[0]
            fall = pad.get("falloff", 2.0)
            poly = Polygon(ring[:, :2])
            x0, y0, x1, y1 = poly.buffer(fall + 2 * cfg.mpp).bounds
            c0, r0 = ~tr * (x0, y1)
            c1, r1 = ~tr * (x1, y0)
            c0, r0 = max(int(c0), 0), max(int(r0), 0)
            c1, r1 = min(int(np.ceil(c1)) + 1, n), min(int(np.ceil(r1)) + 1, n)
            win_tr = tr * tr.translation(c0, r0)
            inside = rasterize([(poly, 1)], out_shape=(r1 - r0, c1 - c0), transform=win_tr, fill=0,
                               all_touched=True, dtype="uint8").astype(bool)
            d = distance_transform_edt(~inside) * cfg.mpp
            u = np.clip(1 - d / fall, 0, 1)
            w = u * u * (3 - 2 * u)
            cols, rows = np.meshgrid(np.arange(c0, c1), np.arange(r0, r1))
            X, Y = tr * (cols + 0.5, rows + 0.5)
            plane = coef[0] * X + coef[1] * Y + coef[2]
            sub = dem[r0:r1, c0:c1]
            dem[r0:r1, c0:c1] = sub * (1 - w) + plane * w
            print(f"Terrain pad for {lid}: {inside.sum()} px levelled, plane z {plane[inside].min():.2f}..{plane[inside].max():.2f}")
    return dem


def keep_ground_behind_walls(cfg, dem, carriageway=None):
    """Behind the retaining walls planned by boundaries.py the ground stays at its natural
    height (road flattening would otherwise slope it down to the road behind the wall)."""
    path = OUTPUT / "boundaries.json"
    if not path.exists():
        return dem
    import shapely
    from scipy.ndimage import distance_transform_edt
    from shapely.geometry import LineString, Polygon
    with rasterio.open(OUTPUT / "dem_natural.tif") as src:
        nat = src.read(1)
    tr = local_transform(cfg)
    n = cfg.size_px
    count = 0
    for r in json.loads(path.read_text()):
        if not r["kind"].startswith("retain"):
            continue
        A = np.asarray(r["pts"], float)[:, :2]
        out = np.asarray(r["out"], float)
        back = A + out * 0.25                                   # back face of the wall
        band = Polygon(np.vstack([back, (back + out * 5.0)[::-1]]))
        if not band.is_valid:
            band = band.buffer(0)
        x0, y0, x1, y1 = band.bounds
        c0, r0 = ~tr * (x0, y1)
        c1, r1 = ~tr * (x1, y0)
        c0, r0 = max(int(c0) - 1, 0), max(int(r0) - 1, 0)
        c1, r1 = min(int(np.ceil(c1)) + 2, n), min(int(np.ceil(r1)) + 2, n)
        if c1 <= c0 or r1 <= r0:
            continue
        win_tr = tr * tr.translation(c0, r0)
        inside = rasterize([(band, 1)], out_shape=(r1 - r0, c1 - c0), transform=win_tr, fill=0,
                           dtype="uint8").astype(bool)
        if not inside.any():
            continue
        # weight from the true distance behind the wall: flattened up to 1 m behind it (the
        # 2 m terrain cells must not bulge over the road in front of the wall), natural from
        # 2.5 m; fading out over the band's far end and sides
        cols, rows = np.meshgrid(np.arange(c0, c1), np.arange(r0, r1))
        X, Y = tr * (cols + 0.5, rows + 0.5)     # pixel centres
        pts = shapely.points(X.ravel(), Y.ravel())
        back_line = LineString(back)
        d_back = shapely.distance(back_line, pts).reshape(X.shape)
        fade = np.clip(distance_transform_edt(inside) * cfg.mpp / 2.0, 0, 1)
        sub, nsub = dem[r0:r1, c0:c1], nat[r0:r1, c0:c1]
        top = np.asarray(r["pts"], float)[:, 3]
        if r.get("road") == "climb":
            # surveyed wall height: the ground starts at the wall's top right behind it and
            # rises to the natural slope over ~3 m
            sb = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(back, axis=0), axis=1))])
            zt = np.interp(shapely.line_locate_point(back_line, pts), sb, top).reshape(X.shape)
            u = np.clip((d_back - 0.3) / 3.0, 0, 1)
            target = np.where(nsub > zt, zt + (nsub - zt) * u * u * (3 - 2 * u), np.maximum(nsub, zt - d_back))
            w = fade * inside
        else:
            # weight from the true distance behind the wall: flattened up to 1 m behind it (the
            # 2 m terrain cells must not bulge over the road in front of the wall), natural from
            # 2.5 m; fading out over the band's far end and sides
            target = nsub
            w = np.clip((d_back - 1.0) / 1.5, 0, 1) * fade * inside
        if carriageway is not None:                    # never another road's surface (hairpin legs)
            w = np.where(carriageway[r0:r1, c0:c1], 0.0, w)
        dem[r0:r1, c0:c1] = np.where(target > sub, sub * (1 - w) + target * w, sub)
        count += 1
    print(f"Kept the natural ground behind {count} retaining walls")
    return dem


def rock_mask(cfg, dem, road_weight):
    """Bare rock: cliffs (very steep), rock seen in the orthophoto on steep ground (pale grey,
    unsaturated, not green), and road cut faces; speckle removed. Forested 40-45 degree slopes
    stay forest floor."""
    from scipy.ndimage import binary_closing, binary_opening, gaussian_filter
    gy, gx = np.gradient(gaussian_filter(dem, 1.5 / cfg.mpp), cfg.mpp)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    rock = slope > CLIFF_SLOPE_DEG
    from common import DATA
    path = DATA / "ortho" / "fullmap.tif"
    if path.exists():
        with rasterio.open(path) as src:
            img = src.read().astype(np.float32)
        if img.shape[1] == dem.shape[0]:
            R, G, B = (gaussian_filter(c, 1.5) for c in img)
            mx, mn = np.maximum(np.maximum(R, G), B), np.minimum(np.minimum(R, G), B)
            pale = ((R + G + B) / 3 > 135) & ((mx - mn) / np.maximum(mx, 1) < 0.16) & (G < R + 6)
            rock |= pale & (slope > 28)
    rock |= (road_weight > 0.05) & (slope > CUT_SLOPE_DEG)          # cut faces beside the roads
    rock = binary_opening(rock, iterations=2)
    rock = binary_closing(rock, iterations=2)
    print(f"Rock: {rock.mean():.1%} of the terrain (cliffs > {CLIFF_SLOPE_DEG} deg, pale rock in the photo, road cuts > {CUT_SLOPE_DEG} deg)")
    return rock


def micro_relief(cfg, dem, road_weight, amp=0.12, seed=3):
    """Natural roughness below the 5 m source resolution (fractal noise, ~amp m), faded out
    on and beside the roads."""
    from scipy.ndimage import gaussian_filter
    rng = np.random.default_rng(seed)
    n = np.zeros(dem.shape, np.float32)
    for scale, a in ((1.0, 0.25), (2.5, 0.35), (6.0, 0.4)):
        layer = gaussian_filter(rng.standard_normal(dem.shape).astype(np.float32), scale / cfg.mpp)
        n += a * layer / (layer.std() + 1e-9)
    keep = np.clip(1.0 - road_weight * 1.5, 0, 1)
    return dem + amp * n * keep


def build_layers(cfg, dem, roads, road_weight):
    n = cfg.size_px
    tr = local_transform(cfg)
    landcover = json.loads((OUTPUT / "landcover_local.geojson").read_text())["features"]
    landcover.sort(key=lambda f: f["properties"]["priority"])
    index = np.zeros((n, n), dtype=np.uint8)  # 0 = grass (default ground)
    shapes = [(shape(f["geometry"]), LAYERS.index(f["properties"]["class"])) for f in landcover]
    if shapes:
        index = rasterize(shapes, out_shape=(n, n), transform=tr, fill=0, dtype="uint8")

    rock = rock_mask(cfg, dem, road_weight)
    zones = OUTPUT / "climb_rock_zones.geojson"          # climb_furniture.py: surveyed rock cuttings
    if zones.exists():
        geoms = [shape(f["geometry"]) for f in json.loads(zones.read_text())["features"] if f["geometry"]]
        geoms = [g for g in geoms if not g.is_empty]
        if geoms:
            rock |= rasterize([(g, 1) for g in geoms], out_shape=(n, n), transform=tr, fill=0, dtype="uint8").astype(bool)
    index[rock & (index != LAYERS.index("water"))] = LAYERS.index("rock")

    # roads, as in the stock Italy level: terrain asphalt reaching EXTRA_ASPHALT_M past the
    # carriageway (Italy: ~1 m at 1 m cells; 1.2 m here at 2.5 m cells, so the material blend band
    # lies under Italy's 2.6 m grassy edge decal instead of inside the lanes), Italy's
    # groundmodel_asphalt1 on main roads and groundmodel_asphalt_old on the rest; dirt under
    # tracks and trails
    from road_geometry import EXTRA_ASPHALT_M, EXTRA_DIRT_M, EXTRA_TRAIL_M
    road_shapes, paved = [], {"asphalt": [], "asphalt_main": []}
    for f in roads:
        p = f["properties"]
        if p["tunnel"]:
            continue
        if p["group"] == "trail":
            road_shapes.append((road_polygon(f, EXTRA_TRAIL_M), LAYERS.index("gravel")))
        elif p["group"] in OFFROAD:
            road_shapes.append((road_polygon(f, EXTRA_DIRT_M), LAYERS.index("gravel")))
        else:
            main = p["group"] == "major" or p["highway"] in {"tertiary", "tertiary_link"}
            paved["asphalt_main" if main else "asphalt"].append(road_polygon(f, EXTRA_ASPHALT_M))
    from shapely.geometry import LineString as _LS
    from shapely.geometry import Point as _Pt
    from shapely.ops import unary_union
    from road_profiles import junctions
    pav = [f for f in roads if f["properties"]["group"] not in OFFROAD and not f["properties"]["tunnel"]]
    near_j = unary_union([_Pt(pt[0], pt[1]).buffer(15.0)
                          for _, pt in junctions(pav, [_LS([c[:2] for c in f["geometry"]["coordinates"]]) for f in pav])])
    for layer in ("asphalt", "asphalt_main"):          # main roads drawn last = on top
        if not paved[layer]:
            continue
        # corners filled (closing) only around junctions, so the ground between hairpin legs or
        # close parallel roads stays ground
        u = unary_union(paved[layer])
        closed = u.buffer(JUNCTION_FILLET_M, join_style="round").buffer(-JUNCTION_FILLET_M, join_style="round")
        u = unary_union([u, closed.difference(u).intersection(near_j)])
        road_shapes += [(g, LAYERS.index(layer)) for g in getattr(u, "geoms", [u])]
    sites = OUTPUT / "paved_areas.geojson"                   # sites.py: e.g. a summit base's aprons
    if sites.exists():
        road_shapes += [(shape(f["geometry"]), LAYERS.index("asphalt"))
                        for f in json.loads(sites.read_text())["features"]]
    order = {LAYERS.index("gravel"): 0, LAYERS.index("asphalt"): 1, LAYERS.index("asphalt_main"): 2}
    road_shapes.sort(key=lambda sv: order.get(sv[1], 0))   # later = on top
    if road_shapes:
        roads_idx = rasterize(road_shapes, out_shape=(n, n), transform=tr, fill=255, dtype="uint8")
        index = np.where(roads_idx != 255, roads_idx, index)

    out_dir = OUTPUT / "terrain_layers"
    out_dir.mkdir(exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    for i, name in enumerate(LAYERS):
        mask = index == i
        if mask.any():
            Image.fromarray((mask * 255).astype(np.uint8)).save(out_dir / f"{name}.png")
    counts = {name: f"{(index == i).mean():.1%}" for i, name in enumerate(LAYERS) if (index == i).any()}
    print(f"Terrain layers: {counts}")
    palette = np.array([LAYER_COLOURS[name] for name in LAYERS], dtype=np.uint8)
    Image.fromarray(palette[index][::2, ::2]).save(OUTPUT / "landcover_preview.png")
    np.save(OUTPUT / "terrain_layer_index.npy", index)


def main():
    cfg = Config()
    with rasterio.open(OUTPUT / "dem_natural.tif") as src:
        dem = src.read(1)
        profile = src.profile
    roads = json.loads((OUTPUT / "roads_local.geojson").read_text())["features"]
    weight = np.zeros_like(dem)
    carriageway = None
    if cfg["roads"]["flatten_terrain"]:
        dem, weight, carriageway = flatten_roads(cfg, dem, roads)
    if cfg.mpp <= 1.5:
        dem = micro_relief(cfg, dem, weight)
    dem = apply_pads(cfg, dem)
    import landmarks_climb
    dem = landmarks_climb.pond_basins(cfg, dem, local_transform(cfg))
    dem = keep_ground_behind_walls(cfg, dem, carriageway)
    with rasterio.open(OUTPUT / "dem_final.tif", "w", **profile) as dst:
        dst.write(dem, 1)
    build_layers(cfg, dem, roads, weight)


if __name__ == "__main__":
    main()
