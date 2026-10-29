#!/usr/bin/env python3
"""Distant mountains for the Assetto Corsa track: a ring of terrain from the map's edge out to
HORIZON_M, so the sky meets the real skyline (the surrounding mountains) instead of the edge of the
8 km map.

Heights: Copernicus GLO-30 (data/ac_assets/copernicus, two 1-degree tiles) on a STEP m grid aligned
with the map edge; cells inside the map are dropped. The ring's inner edge follows our terrain's
border (dem_final.tif, 0.5 m lower) and carries a 60 m skirt, so no sky shows where they meet.
Colour: forest / meadow / scrub / rock by elevation, slope and noise, in the ground colours of
the orthophoto inside the map (measured per land-cover class), blended into the map's own edge
colours over the first kilometre. Visual only: no physics, no shadows.
Output: output/ac/horizon.npz + horizon.json and output/ac/textures/horizon.png.
"""

import json

import numpy as np
import rasterio
from PIL import Image
from rasterio.merge import merge
from scipy.ndimage import gaussian_filter, map_coordinates
from pyproj import Transformer

from ac_track import AC_DIR, TEX_DIR, _periodic_noise, save_part, vertex_normals
from common import DATA, OUTPUT, Config, Heightfield

HORIZON_M = 30000.0
STEP = 128.0                  # grid spacing; the map edge (4096 m) falls on grid lines
TEX_PX = 2048
COLOURS = {"forest": (60, 65, 40), "meadow": (85, 95, 52), "scrub": (102, 94, 68), "rock": (139, 135, 115)}


def copernicus_sampler(cfg):
    paths = sorted((DATA / "ac_assets" / "copernicus").glob("*.tif"))
    srcs = [rasterio.open(p) for p in paths]
    mosaic, transform = merge(srcs)
    for s in srcs:
        s.close()
    z = mosaic[0].astype(np.float32)
    inv = ~transform
    to_ll = Transformer.from_crs(cfg.crs, "EPSG:4326", always_xy=True)

    def sample(x, y):
        lon, lat = to_ll.transform(np.asarray(x) + cfg.center_e, np.asarray(y) + cfg.center_n)
        col, row = inv * (lon, lat)
        return map_coordinates(z, [np.asarray(row) - 0.5, np.asarray(col) - 0.5], order=1, mode="nearest")
    return sample


def colour_map(cfg, sample, hf):
    """TEX_PX^2 RGB over the HORIZON_M square (row 0 = north)."""
    n = TEX_PX
    g = (np.arange(n) + 0.5) / n * 2 * HORIZON_M - HORIZON_M
    X, Y = np.meshgrid(g, -g)
    Z = sample(X.ravel(), Y.ravel()).reshape(n, n)
    px = 2 * HORIZON_M / n
    gy, gx = np.gradient(gaussian_filter(Z, 1.0), px)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    rng = np.random.default_rng(5)
    noise = _periodic_noise(n, rng, 2.4)
    fine = _periodic_noise(n, rng, 1.2)
    # treeline ~1750 m; meadows on gentle ground and high pastures; rock on steep or high ground
    meadow_p = np.clip((Z - 1300) / 500, 0, 1) * 0.6 + np.clip((18 - slope) / 18, 0, 1) * 0.35
    rock_p = np.clip((slope - 32) / 15, 0, 1) + np.clip((Z - 1950) / 250, 0, 1)
    scrub_p = np.clip((Z - 1650) / 200, 0, 1) * np.clip((2000 - Z) / 200, 0, 1)
    rgb = np.empty((n, n, 3), np.float32)
    rgb[:] = COLOURS["forest"]
    for name, p in (("meadow", meadow_p), ("scrub", scrub_p), ("rock", rock_p)):
        m = (p + 0.35 * (noise - 0.5) > 0.55)[..., None]
        rgb = np.where(m, np.array(COLOURS[name], np.float32), rgb)
    rgb *= (0.9 + 0.2 * fine)[..., None]
    rgb = gaussian_filter(rgb, (0.7, 0.7, 0))
    # blend into the map's own ground colour near its edge
    f = 8                                                      # map colour at 8 m / px is plenty here
    base = np.asarray(Image.open(OUTPUT / "terrain_base_final.png").convert("RGB").reduce(f), np.float32)
    h = cfg.half
    cx = np.clip((X + h) / (cfg.mpp * f), 0, base.shape[1] - 1)
    cy = np.clip((h - Y) / (cfg.mpp * f), 0, base.shape[0] - 1)
    edge = np.stack([map_coordinates(gaussian_filter(base[..., k], 2), [cy, cx], order=1) for k in range(3)], -1)
    d = np.maximum(np.abs(X), np.abs(Y)) - h
    w = np.clip(1 - d / 1000.0, 0, 1)[..., None]
    rgb = w * edge + (1 - w) * rgb
    return Image.fromarray(rgb.clip(0, 255).astype(np.uint8))


def main():
    cfg = Config()
    h = cfg.half
    sample = copernicus_sampler(cfg)
    hf = Heightfield(cfg, OUTPUT / "dem_final.tif")
    k = int(round(HORIZON_M / STEP))
    g = np.arange(-k, k + 1) * STEP
    X, Y = np.meshgrid(g, g)                                 # row = south -> north
    Z = sample(X.ravel(), Y.ravel()).reshape(X.shape)
    on_edge = (np.maximum(np.abs(X), np.abs(Y)) <= h + 1e-6)
    # vertices on / inside the map border: our own border height, a little lower
    Xc, Yc = np.clip(X, -h, h - cfg.mpp), np.clip(Y, -h, h - cfg.mpp)
    Z[on_edge] = hf.sample(Xc[on_edge], Yc[on_edge]) - 0.5
    n = len(g)
    idx = np.arange(n * n).reshape(n, n)
    a, b, c, d = idx[:-1, :-1], idx[:-1, 1:], idx[1:, 1:], idx[1:, :-1]   # (row j, col i) quads
    cxq = (X[:-1, :-1] + STEP / 2)
    cyq = (Y[:-1, :-1] + STEP / 2)
    outside = (np.abs(cxq) > h) | (np.abs(cyq) > h)
    tri = np.stack([np.stack([a, b, c], -1)[outside], np.stack([a, c, d], -1)[outside]], 1).reshape(-1, 3)
    P = np.stack([X.ravel(), Y.ravel(), Z.ravel()], 1)
    used = np.unique(tri)
    remap = np.full(len(P), -1)
    remap[used] = np.arange(len(used))
    P, tri = P[used], remap[tri]
    # skirt: hang 60 m below every edge of the ring that borders the map
    border = np.abs(np.maximum(np.abs(P[:, 0]), np.abs(P[:, 1])) - h) < 1e-6
    bi = np.flatnonzero(border)
    order = np.argsort(np.arctan2(P[bi, 1], P[bi, 0]))
    ring = bi[order]
    low = len(P) + np.arange(len(ring))
    P = np.concatenate([P, P[ring] - [0, 0, 60.0]])
    nxt = np.roll(np.arange(len(ring)), -1)
    skirt = np.concatenate([np.stack([ring, ring[nxt], low[nxt]], 1), np.stack([ring, low[nxt], low], 1)])
    # face the skirt towards the map centre (seen from inside)
    cen = P[skirt].mean(1)
    fn = np.cross(P[skirt[:, 1]] - P[skirt[:, 0]], P[skirt[:, 2]] - P[skirt[:, 0]])
    flip = np.einsum("ij,ij->i", fn[:, :2], -cen[:, :2]) < 0
    skirt[flip] = skirt[flip][:, [0, 2, 1]]
    tri = np.concatenate([tri, skirt])
    uv = np.stack([(P[:, 0] + HORIZON_M) / (2 * HORIZON_M), (HORIZON_M - P[:, 1]) / (2 * HORIZON_M)], 1)
    nrm = vertex_normals(P, tri)
    TEX_DIR.mkdir(parents=True, exist_ok=True)
    colour_map(cfg, sample, hf).save(TEX_DIR / "horizon.png")
    mesh = dict(name="horizon", material="horizon", surface=None, pos=P, tri=tri, uv=uv, nrm=nrm,
                cast_shadows=False)
    materials = {"horizon": {"shader": "ksPerPixel", "textures": [["txDiffuse", "horizon.png", "DXT1"]],
                             "props": {"ksAmbient": 0.55, "ksDiffuse": 0.6, "ksSpecular": 0.0, "ksSpecularEXP": 1.0}}}
    save_part("horizon", [mesh], materials)
    print(f"ac_horizon: {len(P):,} vertices, {len(tri):,} triangles out to {HORIZON_M / 1000:.0f} km "
          f"(heights {P[:, 2].min():.0f}-{P[:, 2].max():.0f} m)")


if __name__ == "__main__":
    main()
