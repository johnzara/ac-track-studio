#!/usr/bin/env python3
"""Stack the elevation sources and resample them onto the terrain grid.

Priority: Veneto LiDAR 5 m  >  Veneto DTM5 (bias-matched)  >  Copernicus GLO-30 (bias-matched).
Where a lower-priority source fills a hole, it is blended in over `seam_blend_m` so that no
step appears at the edge of the LiDAR coverage.

Output: output/dem_natural.tif  float32 GeoTIFF in the config CRS, one pixel per heightmap
        sample, north-up ("natural" = before roads are flattened into it)
        output/dem_source.npy   uint8 per sample: 0 LiDAR, 1 DTM5, 2 Copernicus
"""

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.merge import merge
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject
from scipy.ndimage import distance_transform_edt, gaussian_filter

from common import DATA, OUTPUT, VENETO_FUSO12, Config, ensure_dirs

NODATA = -9999.0
SEAM_BLEND_M = 60.0
FILLER_SMOOTH_M = 8.0
DEDUPE_SIGMA_M = 2.0


def mosaic_lidar():
    """Stitch the 5 m LiDAR ASCII tiles into one array on their shared grid."""
    tiles = sorted((DATA / "dem" / "veneto_lidar5").glob("*.asc"))
    if not tiles:
        raise SystemExit("No LiDAR tiles found; run fetch_dem.py first")
    srcs = [rasterio.open(p) for p in tiles]
    try:
        arr, transform = merge(srcs, nodata=NODATA, dtype="float32")
    finally:
        for s in srcs:
            s.close()
    return arr[0], transform, CRS.from_proj4(VENETO_FUSO12)


def mosaic_dtm5():
    sheets = sorted((DATA / "dem" / "veneto_dtm5").glob("*.tif"))
    if not sheets:
        return None
    srcs = [rasterio.open(p) for p in sheets]
    try:
        arr, transform = merge(srcs, nodata=NODATA, dtype="float32")
        crs = srcs[0].crs
    finally:
        for s in srcs:
            s.close()
    return arr[0], transform, crs


def copernicus():
    path = DATA / "dem" / "copernicus_glo30.tif"
    if not path.exists():
        return None
    with rasterio.open(path) as src:
        arr = src.read(1).astype(np.float32)
        arr[arr == src.nodata] = NODATA
        return arr, src.transform, src.crs


def target_grid(cfg):
    """Raster transform whose pixel centres are exactly the heightmap samples."""
    s, n, half = cfg.mpp, cfg.size_px, cfg.half
    west = cfg.center_e - half - s / 2
    north = cfg.center_n - half + (n - 1) * s + s / 2
    return from_origin(west, north, s, s), (n, n)


def warp(source, cfg, resampling):
    arr, src_transform, src_crs = source
    dst_transform, shape = target_grid(cfg)
    out = np.full(shape, NODATA, dtype=np.float32)
    reproject(source=arr, destination=out, src_transform=src_transform, src_crs=src_crs,
              src_nodata=NODATA, dst_transform=dst_transform, dst_crs=CRS.from_user_input(cfg.crs),
              dst_nodata=NODATA, resampling=resampling)
    return out


def fill(dem, source_id, filler, filler_id, name, blend_px):
    """Fill holes in `dem` from `filler`, matching its level and feathering the seam."""
    have = dem != NODATA
    both = have & (filler != NODATA)
    holes = ~have & (filler != NODATA)
    if not holes.any():
        return
    bias = float(np.median(dem[both] - filler[both])) if both.any() else 0.0
    filler = filler + bias
    # inside the original coverage, near its edge, fade from the existing data to the filler
    dist = distance_transform_edt(have)
    w = np.clip(dist / blend_px, 0, 1)
    edge = have & (w < 1) & (filler != NODATA - bias)
    dem[edge] = w[edge] * dem[edge] + (1 - w[edge]) * filler[edge]
    dem[holes] = filler[holes]
    source_id[holes] = filler_id
    print(f"  {name}: filled {holes.mean():.2%} of the grid (level offset {bias:+.2f} m)")


def main():
    ensure_dirs()
    cfg = Config()
    blend_px = SEAM_BLEND_M / cfg.mpp

    print("LiDAR 5 m")
    dem = warp(mosaic_lidar(), cfg, Resampling.cubic)
    source_id = np.where(dem != NODATA, 0, 255).astype(np.uint8)
    print(f"  covers {(dem != NODATA).mean():.2%} of the grid")
    dtm5 = mosaic_dtm5()
    if dtm5 is not None:
        fill(dem, source_id, warp(dtm5, cfg, Resampling.cubic), 1, "DTM5", blend_px)
    cop = copernicus()
    if cop is not None:
        fill(dem, source_id, warp(cop, cfg, Resampling.bilinear), 2, "Copernicus", blend_px)
    if (dem == NODATA).any():
        raise SystemExit("Grid still has holes after gap filling")

    # DTM5 is interpolated from contours and shows flat triangular facets; soften it,
    # fading the smoothing in with distance from the LiDAR coverage.
    if (source_id != 0).any():
        smooth = gaussian_filter(dem, FILLER_SMOOTH_M / cfg.mpp)
        w = np.clip(distance_transform_edt(source_id != 0) / blend_px, 0, 1)
        dem = (1 - w) * dem + w * smooth
        print(f"Smoothed filled areas (sigma {FILLER_SMOOTH_M:.0f} m)")

    # the 5 m LiDAR, interpolated to a finer grid, has ~0.3 m corrugations at its own 5 m
    # spacing (resampling overshoot and ground-filter residue under forest); a 2 m Gaussian
    # removes them (build_terrain.py adds a little natural micro-relief back off the roads)
    if cfg.mpp < 2.5:
        dem = gaussian_filter(dem, DEDUPE_SIGMA_M / cfg.mpp)
        print(f"Smoothed below the source resolution (sigma {DEDUPE_SIGMA_M} m)")
    lo, hi = float(dem.min()), float(dem.max())
    print(f"Elevation range {lo:.1f} .. {hi:.1f} m")

    transform, shape = target_grid(cfg)
    out = OUTPUT / "dem_natural.tif"
    profile = dict(driver="GTiff", width=shape[1], height=shape[0], count=1, dtype="float32",
                   crs=CRS.from_user_input(cfg.crs), transform=transform, compress="deflate",
                   predictor=3, tiled=True)
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(dem, 1)
    np.save(OUTPUT / "dem_source.npy", source_id)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
