#!/usr/bin/env python3
"""Textures for the Assetto Corsa track: road surface, terrain layers, rock faces, walls, guardrails.

Photo sources, all CC0:
  Poly Haven (data/ac_assets/polyhaven_tex): asphalt_04 (road grain), aerial_asphalt_01 (patches),
    rock_ground (gravel shoulders, stony ground), forest_leaves_02 (leaf litter), marble_cliff_01 and
    rock_face_03 (limestone cuttings), rock_wall_08 (dry-stone walls), rock_wall_13 (mortared stone
    walls), concrete_wall_007 (board-formed concrete)
  ambientCG (data/ac_assets/ambientcg): Grass004 (meadow)

Normal maps follow AC's decoding (ksPerPixelNM, ksPerPixelMultiMap_NMDetail, ksPerPixelAT_NM):
n = (2r - 1) T + (1 - 2g) B + (2b - 1) N, with T = dP/du and B = N x T. Walls, rock faces and tree
cards are laid out so that B points to the top of the image; the road's v grows in the driving
direction, so there B points to the bottom of the image.

Road surface (ksPerPixelMultiMap_NMDetail): one 4096 x 4096 "macro" set (colour, normal, maps) of
ROAD_STRIPS strips side by side, each the full road width (u, 0 = left edge) by ROAD_SEG_M along
the road (v); the road picks a strip per ROAD_SEG_M segment. Strips 0-3 are worn asphalt (a few
cracks, little patching), 4-7 patched (repairs, alligator cracking, sealed cracks, potholes),
matched to the Street View survey. Wheel paths, the centre oil band and the crumbling, dusty edges
run the whole length; every strip-specific feature fades out within ROAD_JOINT_M of the strip
ends, so any two strips meet seamlessly. The grain is a separate detail photo (asphalt_04, high-
passed so only the stones remain) tiled every ROAD_SEG_M / ROAD_DETAIL_MULT m.
"""

import json

import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt, map_coordinates

from common import DATA, OUTPUT

PH = DATA / "ac_assets" / "polyhaven_tex"
ACG = DATA / "ac_assets" / "ambientcg"
TEX_DIR = OUTPUT / "ac" / "textures"
Image.MAX_IMAGE_PIXELS = None

ROAD_PX = 4096
ROAD_STRIPS = 8
ROAD_SEG_M = 45.0                         # strip length (m)
ROAD_W_NOM = ROAD_SEG_M / ROAD_STRIPS     # nominal road width that keeps texels square (5.625 m)
ROAD_JOINT_M = 1.5
ROAD_DETAIL_MULT = 11                     # detail repeats every 45 / 11 = 4.09 m (asphalt_04 is 4.04 m)
ROAD_TARGET = np.array([0.365, 0.360, 0.348])    # mean sRGB albedo of the finished road (v0.5: 0.335)
DETAIL_MEAN = 0.78                        # mean of the high-passed detail photos (macro = target / this)
WORN_STRIPS, PATCHED_STRIPS = (0, 1, 2, 3), (4, 5, 6, 7)


# ----------------------------------------------------------------------------- noise
def _hash(ix, iy, seed):
    h = (ix * 374761393 + iy * 668265263 + (seed + 1) * 1442695041) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    return h ^ (h >> 16)


def perlin(x, y, seed=0, period=None):
    """Gradient noise in about [-1, 1] at lattice coordinates (x, y); `period` (px, py) in lattice
    cells makes it periodic."""
    x = np.asarray(x, np.float64)
    y = np.asarray(y, np.float64)
    x0, y0 = np.floor(x), np.floor(y)
    fx, fy = x - x0, y - y0
    ix, iy = x0.astype(np.int64), y0.astype(np.int64)

    def g(i, j, dx, dy):
        if period is not None:
            i, j = i % period[0], j % period[1]
        a = (_hash(i, j, seed) & 0xFFFF) * (2 * np.pi / 65536)
        return np.cos(a) * dx + np.sin(a) * dy

    u = fx * fx * fx * (fx * (fx * 6 - 15) + 10)
    v = fy * fy * fy * (fy * (fy * 6 - 15) + 10)
    n0 = g(ix, iy, fx, fy) + u * (g(ix + 1, iy, fx - 1, fy) - g(ix, iy, fx, fy))
    n1 = g(ix, iy + 1, fx, fy - 1) + u * (g(ix + 1, iy + 1, fx - 1, fy - 1) - g(ix, iy + 1, fx, fy - 1))
    return (n0 + v * (n1 - n0)) * 1.41


def fbm(x, y, wavelength, octaves=4, seed=0, gain=0.5, period=None):
    """Fractal noise in about [-1, 1] at world coordinates (m); period (m, m) for tileable noise
    (multiples of the wavelength)."""
    total, amp, norm = 0.0, 1.0, 0.0
    for o in range(octaves):
        f = 2 ** o / wavelength
        per = None if period is None else (int(round(period[0] * f)), int(round(period[1] * f)))
        total = total + amp * perlin(np.asarray(x) * f, np.asarray(y) * f, seed + 31 * o, per)
        norm += amp
        amp *= gain
    return total / norm


def smoothstep(e0, e1, x):
    t = np.clip((np.asarray(x, np.float64) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


# ----------------------------------------------------------------------------- image helpers
def load_rgb(path, size=None):
    im = Image.open(path).convert("RGB")
    if size and im.size != size:
        im = im.resize(size, Image.LANCZOS)
    return np.asarray(im, np.float32) / 255


def load_gray(path, size=None):
    im = Image.open(path)
    a = np.asarray(im, np.float32)
    if a.ndim == 3:
        a = a[..., :3].mean(-1)
    a = a / (65535.0 if a.max() > 255 else 255.0)
    if size and (a.shape[1], a.shape[0]) != size:
        a = np.asarray(Image.fromarray(a).resize(size, Image.LANCZOS), np.float32)
    return a


def save(arr, name, mode=None):
    """Float [0, 1] array (h, w, c) -> output/ac/textures/<name> (PNG)."""
    TEX_DIR.mkdir(parents=True, exist_ok=True)
    a = (np.clip(arr, 0, 1) * 255).round().astype(np.uint8)
    Image.fromarray(a, mode).save(TEX_DIR / name)
    return name


def blur_wrap(a, sigma):
    """Gaussian blur of a tileable image (wrap-around), in the frequency domain."""
    a = np.asarray(a, np.float32)
    h, w = a.shape[:2]
    g = np.exp(-2 * (np.pi * sigma) ** 2 * (np.fft.fftfreq(h)[:, None] ** 2 + np.fft.rfftfreq(w)[None, :] ** 2))
    if a.ndim == 2:
        return np.fft.irfft2(np.fft.rfft2(a) * g, s=(h, w)).astype(np.float32)
    return np.stack([np.fft.irfft2(np.fft.rfft2(a[..., k]) * g, s=(h, w)) for k in range(a.shape[2])], -1).astype(np.float32)


def grad_wrap(h):
    """Central differences with wrap-around: (d/dx towards the right, d/dy towards the bottom), per px."""
    return (np.roll(h, -1, 1) - np.roll(h, 1, 1)) / 2, (np.roll(h, -1, 0) - np.roll(h, 1, 0)) / 2


def height_normal(h, px_m, b_down, wrap=True):
    """Tangent-space normal map (AC encoding, floats 0..1) from a height field in metres (row 0 = top
    of the image). b_down: the mesh's B points to the bottom of the image."""
    if wrap:
        dx, dy = grad_wrap(h)
    else:
        dy, dx = np.gradient(h)
    dx, dy = dx / px_m, dy / px_m
    nb = dy if b_down else -dy                       # height gradient along B
    n = np.stack([-dx, -nb, np.ones_like(h)], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    return np.stack([(n[..., 0] + 1) / 2, (1 - n[..., 1]) / 2, (n[..., 2] + 1) / 2], -1)


def gl_to_ac(nm, b_down):
    """A Poly Haven OpenGL normal map (floats, green = towards the top of the image) in AC's encoding
    for a mapping whose B points to the bottom (b_down) or the top of the image."""
    out = nm.copy()
    if not b_down:
        out[..., 1] = 1 - out[..., 1]
    return out


def blend_normals(a, b, wb=1.0):
    """Whiteout blend of two encoded normal maps (b weighted by wb)."""
    na, nb = a * 2 - 1, b * 2 - 1
    nb = nb * np.array([wb, wb, 1.0])
    n = np.stack([na[..., 0] + nb[..., 0], na[..., 1] + nb[..., 1], na[..., 2] * nb[..., 2]], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    return (n + 1) / 2


def highpass_detail(rgb, sigma_px, mean=DETAIL_MEAN, desat=0.8, contrast=1.0):
    """A photo reduced to its small-scale texture: divided by its own blur (tileable), desaturated,
    rescaled to `mean` (the colour then comes from the macro / base texture)."""
    low = blur_wrap(rgb, sigma_px)
    d = rgb / np.maximum(low, 1e-3)
    lum = d.mean(-1, keepdims=True)
    d = lum + (d - lum) * (1 - desat)
    d = 1 + (d - 1) * contrast
    return np.clip(d * mean, 0, 1)


def photo(name, kind, res="2k"):
    """Path of a Poly Haven map: kind in diff, nor_gl, rough, disp."""
    folder = PH / name
    pat = {"diff": ["_diff_", "_diffuse_"], "nor_gl": ["_nor_gl_"], "rough": ["_rough_"], "disp": ["_disp_"]}[kind]
    for f in sorted(folder.iterdir()):
        if any(p in f.name for p in pat) and f"_{res}." in f.name:
            return f
    raise FileNotFoundError(f"{name} {kind} {res}")


# ----------------------------------------------------------------------------- road surface
def _line_mask(dist, width, soft=0.6):
    """Coverage of a line of `width` (m) given the distance (m) to its axis."""
    return smoothstep(width / 2 + soft * width, width / 2 - soft * width * 0.3, dist)


def _strip_features(k, X, Y, rng, patched, aerial):
    """Strip-specific layers for strip k on the grid X (across, m from the left edge), Y (along, m):
    returns (shade multiplier, tint add (3), height add (m), spec add, gloss add)."""
    W, L = ROAD_W_NOM, ROAD_SEG_M
    shade = np.ones_like(X)
    tint = np.zeros(X.shape + (3,))
    h = np.zeros_like(X)
    spec = np.zeros_like(X)
    gloss = np.zeros_like(X)
    seed = 100 + 17 * k
    # large-scale tone: older / newer stretches, damp and dusty areas; ravelled (lighter, rough) spots
    shade *= 1 + 0.08 * fbm(X, Y, 14.0, 3, seed) + 0.04 * fbm(X, Y, 3.0, 3, seed + 1)
    tint += (0.015 * fbm(X, Y, 9.0, 2, seed + 2))[..., None] * np.array([1.0, 0.6, -0.4])
    ravel = smoothstep(0.35, 0.7, fbm(X, Y, 1.2, 4, seed + 3)) * (0.5 + 0.5 * smoothstep(-0.2, 0.4, fbm(X, Y, 6.0, 2, seed + 4)))
    shade *= 1 + 0.06 * ravel
    gloss -= 0.15 * ravel

    def crack(dist, width, darkness, depth, wn):
        """A crack of varying width along a centre distance field."""
        w = width * (0.55 + 0.9 * (0.5 + 0.5 * wn))
        m = _line_mask(dist, w)
        return m, 1 - darkness * m, -depth * m

    # longitudinal cracks, some sealed with bitumen (glossy black "tar snakes")
    n_long = rng.integers(1, 4) + (2 if patched else 0)
    for c in range(n_long):
        q0 = rng.choice([rng.uniform(0.22, 0.34), rng.uniform(0.66, 0.78), rng.uniform(0.06, 0.14), rng.uniform(0.86, 0.94)])
        Yc = Y + 0.0 * X
        xc = (q0 * W + 0.15 * fbm(Yc * 0 + c, Yc, 5.0, 2, seed + 10 + c) + 0.05 * fbm(Yc * 0 + c, Yc, 0.9, 3, seed + 20 + c)
              + 0.012 * fbm(Yc * 0 + c, Yc, 0.12, 2, seed + 25 + c))
        y0 = rng.uniform(0, L * 0.7)
        y1 = y0 + rng.uniform(L * 0.08, L * 0.4)
        on = smoothstep(y0, y0 + 0.3, Y) * smoothstep(y1, y1 - 0.3, Y) * smoothstep(-0.05, 0.2, fbm(X * 0, Y, 1.2, 3, seed + 30 + c))
        wn = fbm(X * 0 + c, Y, 0.5, 2, seed + 35 + c)
        sealed = rng.random() < (0.5 if patched else 0.25)
        if sealed:
            m, s, _ = crack(np.abs(X - xc), rng.uniform(0.025, 0.05), 0.35, 0.0, wn)
            m *= on
            shade *= 1 - 0.35 * m
            spec += 0.25 * m
            gloss += 0.25 * m
            h += 0.0006 * m
        m, s, dh = crack(np.abs(X - xc), rng.uniform(0.006, 0.012), 0.4, 0.004, wn)
        shade *= 1 - (1 - s) * on
        h += dh * on
    # transverse cracks
    for c in range(rng.integers(0, 3) + (2 if patched else 0)):
        Xc = X + 0.0 * Y
        yc = (rng.uniform(ROAD_JOINT_M + 1, L - ROAD_JOINT_M - 1) + 0.18 * fbm(Xc, Xc * 0 + c, 2.0, 2, seed + 40 + c)
              + 0.03 * fbm(Xc, Xc * 0 + c, 0.3, 2, seed + 45 + c))
        x0, x1 = sorted(rng.uniform(0, W, 2))
        x0, x1 = (0.0, W) if rng.random() < 0.4 else (x0, x1)
        span = smoothstep(x0, x0 + 0.2, X) * smoothstep(x1, x1 - 0.2, X) * smoothstep(-0.3, -0.05, fbm(X, Y * 0 + c, 0.8, 2, seed + 47 + c))
        m, s, dh = crack(np.abs(Y - yc), rng.uniform(0.006, 0.014), 0.5, 0.004, fbm(X, Y * 0 + c, 0.4, 2, seed + 48 + c))
        shade *= 1 - (1 - s) * span
        h += dh * span
    # patched strips: repairs, alligator cracking in the wheel paths, potholes
    if patched:
        for p in range(rng.integers(3, 7)):
            pl, pw = rng.uniform(1.0, 5.0), rng.uniform(0.8, min(3.2, W))
            if rng.random() < 0.25:                                      # trench / strip repair along the road
                pl, pw = rng.uniform(8, 20), rng.uniform(0.45, 0.8)
            cx = rng.uniform(pw / 2, W - pw / 2)
            cy = rng.uniform(ROAD_JOINT_M + pl / 2 + 0.3, L - ROAD_JOINT_M - pl / 2 - 0.3) if pl < L - 2 * ROAD_JOINT_M - 1 else L / 2
            ang = np.radians(rng.uniform(-3, 3))
            u = (X - cx) * np.cos(ang) + (Y - cy) * np.sin(ang)
            v = -(X - cx) * np.sin(ang) + (Y - cy) * np.cos(ang)
            jag = 0.02 * fbm(X, Y, 0.15, 2, seed + 50 + p)
            d = np.maximum(np.abs(u) - pw / 2, np.abs(v) - pl / 2) + jag          # < 0 inside
            inside = smoothstep(0.01, -0.01, d)
            seam = _line_mask(np.abs(d), 0.025)
            # fill: a crop of the aerial asphalt scan (its stains and a hint of its coarser grain)
            ay, ax = rng.integers(0, aerial.shape[0]), rng.integers(0, aerial.shape[1])
            fill = aerial[(ay + (Y / 0.0073).astype(int)) % aerial.shape[0], (ax + (X / 0.0073).astype(int)) % aerial.shape[1]]
            fill = 1 + (fill / fill.mean() - 1) * 0.35
            tone = rng.uniform(0.8, 1.04)
            shade = shade * (1 - inside) + shade * inside * tone * fill
            shade *= 1 - 0.3 * seam
            h += 0.003 * inside - 0.0015 * seam
            gloss += (0.15 if tone < 0.9 else -0.05) * inside
        # alligator cracking: polygon networks in a wheel path, cells of varying size, some edges open
        for a_ in range(rng.integers(1, 3)):
            q = rng.choice([0.32, 0.68]) * W
            ya, yb = sorted(rng.uniform(ROAD_JOINT_M + 1, L - ROAD_JOINT_M - 1, 2))
            yb = max(yb, ya + 2.5)
            zone = smoothstep(0.75, 0.35, np.abs(X - q) + 0.2 * fbm(X, Y, 1.0, 2, seed + 60 + a_)) * smoothstep(ya, ya + 0.6, Y) * smoothstep(yb, yb - 0.6, Y)
            wx = X + 0.06 * fbm(X, Y, 0.5, 2, seed + 64 + a_)
            wy = Y + 0.06 * fbm(X, Y, 0.5, 2, seed + 65 + a_)
            cells = _voronoi_edges(wx, wy, rng.uniform(0.16, 0.26), seed + 70 + a_)
            open_ = smoothstep(-0.45, -0.1, fbm(X, Y, 0.35, 2, seed + 75 + a_))
            dark = 0.25 + 0.35 * (0.5 + 0.5 * fbm(X, Y, 0.3, 2, seed + 76 + a_))
            m = _line_mask(cells, 0.008 + 0.006 * zone) * zone * open_
            shade *= 1 - dark * m
            h -= 0.003 * m
        for p in range(rng.integers(1, 4)):
            cx, cy, r = rng.uniform(0.6, W - 0.6), rng.uniform(ROAD_JOINT_M + 1, L - ROAD_JOINT_M - 1), rng.uniform(0.12, 0.3)
            d = np.hypot(X - cx, Y - cy) * (1 + 0.35 * fbm(X, Y, 0.25, 2, seed + 80 + p)) - r
            m = smoothstep(0.03, -0.03, d)
            shade *= 1 - 0.4 * m
            h -= 0.02 * m * smoothstep(0, -r, d)
    # stains: oil drops in the centre band, dark damp blotches
    stain = smoothstep(0.55, 0.85, fbm(X, Y, 0.7, 3, seed + 90)) * smoothstep(0.9, 0.2, np.abs(X - W / 2))
    shade *= 1 - 0.12 * stain
    gloss += 0.2 * stain
    win = smoothstep(0, ROAD_JOINT_M, Y) * smoothstep(L, L - ROAD_JOINT_M, Y)
    return 1 + (shade - 1) * win, tint * win[..., None], h * win, spec * win, gloss * win


def _voronoi_edges(X, Y, cell, seed):
    """Distance (m) to the nearest Voronoi edge of jittered grid points of spacing `cell`."""
    gx, gy = X / cell, Y / cell
    ix, iy = np.floor(gx).astype(np.int64), np.floor(gy).astype(np.int64)
    d1 = np.full(X.shape, 9.0)
    d2 = np.full(X.shape, 9.0)
    for oy in (-1, 0, 1):
        for ox in (-1, 0, 1):
            cx, cy = ix + ox, iy + oy
            hx = (_hash(cx, cy, seed) & 0xFFFF) / 65536
            hy = (_hash(cx, cy, seed + 7) & 0xFFFF) / 65536
            d = np.hypot(gx - (cx + hx), gy - (cy + hy))
            closer = d < d1
            d2 = np.where(closer, d1, np.minimum(d2, d))
            d1 = np.where(closer, d, d1)
    return (d2 - d1) * cell / 2


def make_road_textures():
    """road_macro.png (RGBA: colour, alpha 0 = full detail), road_macro_nm.png, road_macro_maps.png,
    road_detail.png, road_detail_nm.png."""
    n, sw = ROAD_PX, ROAD_PX // ROAD_STRIPS
    W, L = ROAD_W_NOM, ROAD_SEG_M
    xs = (np.arange(sw) + 0.5) / sw * W
    ys = (np.arange(n) + 0.5) / n * L
    X, Y = np.meshgrid(xs, ys)
    e = np.minimum(X, W - X)                                     # m from the nearer edge
    # shared along the whole road (periodic in Y): wheel paths, centre band, edges
    per = (W * 64, L)                                            # periodic in Y only
    wheel = np.exp(-0.5 * ((np.abs(X - W / 2) - 0.85) / 0.28) ** 2)
    centre = np.exp(-0.5 * ((X - W / 2) / 0.16) ** 2)
    wn = fbm(X, Y, 3.0, 3, 5, period=per)
    side = np.where(X < W / 2, 0.0, 50.0)
    edge_w = 0.22 + 0.12 * fbm(X * 0 + side, Y, 1.5, 3, 6, period=per)
    dust = smoothstep(edge_w + 0.18, 0.0, e) * (0.55 + 0.45 * fbm(X * 0 + side, Y, 5.0, 2, 11, period=per))
    crumble_n = fbm(X, Y, 0.125, 3, 7, period=per) + 0.6 * fbm(X, Y, 0.625, 2, 8, period=per)
    crumble = smoothstep(0.04, -0.02, e - 0.10 * (1 + crumble_n))
    base_shade = (1 - 0.07 * wheel * (0.8 + 0.4 * wn)) * (1 - 0.05 * centre * (0.6 + 0.6 * fbm(X, Y, 0.75, 2, 9, period=per)))
    base_h = -0.004 * wheel - 0.012 * crumble * (0.5 + 0.5 * fbm(X, Y, 0.075, 2, 10, period=per))
    base_spec = 0.3 + 0.2 * wheel - 0.15 * dust
    base_gloss = 0.3 + 0.25 * wheel - 0.15 * dust
    dust_col = np.array([0.60, 0.58, 0.53])
    grav_col = np.array([0.52, 0.49, 0.44])
    aerial = load_rgb(photo("aerial_asphalt_01", "diff", "4k")).mean(-1)
    aerial = aerial / blur_wrap(aerial, 200)                    # keep its grain and small stains only
    colour = np.zeros((n, n, 3), np.float32)
    height = np.zeros((n, n), np.float32)
    spec = np.zeros((n, n), np.float32)
    gloss = np.zeros((n, n), np.float32)
    for k in range(ROAD_STRIPS):
        rng = np.random.default_rng(1000 + k)
        sh, ti, hh, sp, gl = _strip_features(k, X, Y, rng, k in PATCHED_STRIPS, aerial)
        col = (ROAD_TARGET / DETAIL_MEAN) * (base_shade * sh)[..., None] + ti
        col = col * (1 - 0.5 * dust[..., None]) + dust_col / DETAIL_MEAN * 0.5 * dust[..., None]
        col = col * (1 - crumble[..., None]) + grav_col / DETAIL_MEAN * crumble[..., None]
        sl = slice(k * sw, (k + 1) * sw)
        colour[:, sl] = col
        height[:, sl] = base_h + hh
        spec[:, sl] = base_spec + sp - 0.2 * crumble
        gloss[:, sl] = base_gloss + gl - 0.2 * crumble
        print(f"  road strip {k}: {'patched' if k in PATCHED_STRIPS else 'worn'}", flush=True)
    px_m = L / n
    nm = height_normal(height.astype(np.float64), px_m, b_down=True)
    rgba = np.concatenate([colour, np.zeros((n, n, 1), np.float32)], -1)
    save(rgba, "road_macro.png", "RGBA")
    save(nm, "road_macro_nm.png")
    save(np.stack([np.clip(spec, 0, 1), np.clip(gloss, 0, 1), np.full_like(spec, 0.25)], -1), "road_macro_maps.png")
    # detail: asphalt_04 grain (4.04 m photo on a 4.09 m repeat), its normal map
    det = highpass_detail(load_rgb(photo("asphalt_04", "diff")), 60, contrast=1.15)
    save(det, "road_detail.png")
    dn = gl_to_ac(load_rgb(photo("asphalt_04", "nor_gl")), b_down=True)
    save(dn, "road_detail_nm.png")
    meta = {"strips": ROAD_STRIPS, "seg_m": L, "worn": list(WORN_STRIPS), "patched": list(PATCHED_STRIPS),
            "detail_mult": ROAD_DETAIL_MULT}
    (TEX_DIR / "road_macro.json").write_text(json.dumps(meta))
    return meta


# ----------------------------------------------------------------------------- rock, walls, guardrails
ROCK_TILE_M = 4.3              # marble_cliff_01 covers 4.3 m
ROCK_DETAIL_MULT = 4           # rock_face_03 grain every 1.07 m
WALLS = {   # material -> (photo, metres per tile, brightness, desaturation)
    "wall_drystone": ("rock_wall_08", 1.8, 1.45, 0.35),
    "wall_mortared": ("rock_wall_13", 2.0, 1.5, 0.3),
    "wall_concrete": ("concrete_wall_007", 2.16, 0.98, 0.45),
}
GUARDRAIL_TILE_M = 4.0


def _desat(rgb, amount, tint=(1.0, 1.0, 1.0)):
    lum = rgb.mean(-1, keepdims=True)
    return (lum * np.array(tint) * amount + rgb * (1 - amount))


def make_rock_wall_textures():
    """rock_face(.png RGBA, alpha = detail mask) + _nm + _maps + rock_detail; one colour + normal map per
    wall material; guardrail and post textures (procedural galvanised steel)."""
    # limestone: the marble-cliff scan turned grey (pre-Alpine limestone is pale grey, water-streaked)
    rgb = load_rgb(photo("marble_cliff_01", "diff"))
    rgb = _desat(rgb, 0.8, (0.97, 0.98, 1.0)) * 0.86
    from common import Config                               # config.json assetto_corsa.rock_tint (e.g. volcanic rock)
    rgb = rgb * np.asarray(Config().raw.get("assetto_corsa", {}).get("rock_tint", [1.0, 1.0, 1.0]))
    alpha = np.full(rgb.shape[:2] + (1,), 0.35)
    save(np.concatenate([rgb, alpha], -1), "rock_face.png", "RGBA")
    save(gl_to_ac(load_rgb(photo("marble_cliff_01", "nor_gl")), b_down=False), "rock_face_nm.png")
    rough = load_gray(photo("marble_cliff_01", "rough"))
    save(np.stack([(1 - rough) * 0.5, (1 - rough) * 0.5, np.zeros_like(rough)], -1), "rock_face_maps.png")
    det = highpass_detail(load_rgb(photo("rock_face_03", "diff")), 80, desat=0.9, contrast=1.3)
    save(det, "rock_detail.png")
    save(gl_to_ac(load_rgb(photo("rock_face_03", "nor_gl")), b_down=False), "rock_detail_nm.png")
    for mat, (name, _, bright, desat) in WALLS.items():
        rgb = _desat(load_rgb(photo(name, "diff")), desat, (1.0, 0.99, 0.97)) * bright
        save(rgb, f"{mat}.png")
        save(gl_to_ac(load_rgb(photo(name, "nor_gl")), b_down=False), f"{mat}_nm.png")
    # galvanised W-beam: u along the rail (GUARDRAIL_TILE_M), v down the profile; zinc bloom, rain
    # streaks down the profile, road grime on the lower half, a few rust spots
    w_, h_ = 1024, 128
    X, Y = np.meshgrid((np.arange(w_) + 0.5) / w_ * GUARDRAIL_TILE_M, (np.arange(h_) + 0.5) / h_ * 0.31)
    per = (GUARDRAIL_TILE_M, 0.31 * 40)
    base = 0.64 + 0.04 * fbm(X, Y * 0, 0.25, 3, 900, period=per) + 0.05 * fbm(X, Y, 1.0, 3, 901, period=per)
    bloom = smoothstep(0.35, 0.8, fbm(X, Y, 0.2, 3, 902, period=per))
    grime = smoothstep(0.12, 0.31, Y) * (0.6 + 0.4 * fbm(X, Y * 0, 0.5, 2, 903, period=per))
    rust = smoothstep(0.72, 0.9, fbm(X, Y, 0.15, 3, 904, period=per))
    col = base[..., None] * np.array([0.98, 1.0, 1.03])
    col = col * (1 + 0.12 * bloom[..., None])
    col = col * (1 - 0.22 * grime[..., None] * np.array([0.9, 1.0, 1.15]))
    col = col * (1 - rust[..., None]) + np.array([0.42, 0.27, 0.16]) * rust[..., None]
    save(col, "guardrail.png")
    post = np.full((64, 64, 3), 0.5) * np.array([0.98, 1.0, 1.03])
    save(post, "guardrail_post.png")
    save(np.full((16, 16, 3), 0.28), "steel_dark.png")


# ----------------------------------------------------------------------------- terrain
CLASS_LAYERS = {"R": ("grass", "meadow", "farmland", "orchard", "vineyard", "scrub"), "G": ("forest",),
                "B": ("rock", "gravel", "water"), "A": ("asphalt", "asphalt_main")}
SHARED_LAYERS = {"urban": (0.6, 0.0, 0.4, 0.0), "industrial": (0.0, 0.0, 0.7, 0.3)}   # R G B A: lawns / gravel yards
TERRAIN_DETAIL = {   # ksMultilayer channel -> (source path, metres per repeat)
    "R": (ACG / "Grass004" / "Grass004_2K-JPG_Color.jpg", 3.0),
    "G": (PH / "forest_leaves_02" / "forest_leaves_02_diffuse_2k.jpg", 3.0),
    "B": (PH / "rock_ground" / "rock_ground_diff_2k.jpg", 2.0),
    "A": (PH / "asphalt_04" / "asphalt_04_diff_2k.jpg", 4.04),
}
MAGIC_MULT = 2.0               # ksMultilayer: colour = txDiffuse * sum(mask * detail) * magicMult
PHOTO_MIX = 0.75               # open land: share of the (toned) orthophoto colour in the base colour
PHOTO_DESAT = 0.3              # ... and how much of its saturation is taken out
PHOTO_SHADOW_M = 6.0           # ... pixels much darker than the field within this are tree shadows / crowns
FOREST_BLEND = 0.6             # forest floor: 0 = the photo's canopy colour, 1 = the leaf-litter photo
FOREST_TONE = 0.72             # leaf litter under the canopy, darker than in the open
GRAVEL_TONE = 1.08             # limestone gravel, a little paler than the scan
ASPHALT_TONE = 0.72
SHOULDER_M = (0.45, 0.25)      # gravel shoulder beside the climb: mean width, +- noise (m)
FOOTPRINT_M = 3.0              # the level's asphalt layer within this of the climb is the climb's own (not a side road)


def make_terrain_details():
    """terrain_detail_<c>.png: the four ksMultilayer detail photos with their largest blotches
    flattened (they repeat every 2-4 m); returns their mean colours."""
    means = {}
    for c, (path, _) in TERRAIN_DETAIL.items():
        rgb = load_rgb(path)
        flat = rgb / np.maximum(blur_wrap(rgb, rgb.shape[0] / 8), 1e-3) * rgb.reshape(-1, 3).mean(0)
        out = np.clip(0.55 * flat + 0.45 * rgb, 0, 1)
        save(out, f"terrain_detail_{c}.png")
        means[c] = out.reshape(-1, 3).mean(0)
    return means


class TerrainPainter:
    """Land-cover weights (R meadow, G forest floor, B gravel / stony ground, A asphalt / villages)
    and the ksMultilayer base colour for any square window at any resolution: the 1 m layer maps,
    the level's base colour map (photo pattern, roads removed) and noise in world coordinates, so
    tiles of different sizes agree where they meet. Near the climb (`road` given) the boundaries
    are broken up and sharpened and a gravel shoulder is laid along the asphalt edge."""

    def __init__(self, cfg, hf, means):
        self.cfg, self.means = cfg, means
        self.half, self.mpp = cfg.half, cfg.mpp
        cls = {}
        for c, names in CLASS_LAYERS.items():
            acc = None
            for k in names:
                p = OUTPUT / "terrain_layers" / f"{k}.png"
                if p.exists():
                    a = np.asarray(Image.open(p).convert("L"), np.uint16)
                    acc = a if acc is None else acc + a
            cls[c] = np.clip(acc, 0, 255).astype(np.uint8)
        self.cls = cls
        self.shared = {k: np.asarray(Image.open(OUTPUT / "terrain_layers" / f"{k}.png").convert("L"))
                       for k in SHARED_LAYERS if (OUTPUT / "terrain_layers" / f"{k}.png").exists()}
        self.base = np.asarray(Image.open(OUTPUT / "terrain_base_final.png").convert("RGB"))
        meadow = self.cls["R"][::8, ::8] > 200                 # typical meadow colour, for village lawns
        self.meadow_col = np.median(self.base[::8, ::8][meadow].reshape(-1, 3), axis=0) / 255
        self._open_land_photo(cfg)
        # paved side roads (OSM, with their widths): they keep their asphalt beside the climb
        import shapely
        from shapely.geometry import LineString
        polys = []
        for f in json.loads((OUTPUT / "roads_local.geojson").read_text())["features"]:
            p = f["properties"]
            if str(p.get("climb")) == "True" or p.get("group") == "track":
                continue
            xy = np.asarray(f["geometry"]["coordinates"], float)[:, :2]
            w = p.get("widths")
            w = float(np.mean(json.loads(w) if isinstance(w, str) else w)) if w else float(p.get("width") or 3.0)
            if len(xy) >= 2:
                polys.append(LineString(xy).buffer(w / 2 + 0.3))
        self.side_roads = polys
        self.side_tree = shapely.STRtree(polys)
        gy, gx = np.gradient(hf.z.astype(np.float32), self.mpp)
        self.slope = np.degrees(np.arctan(np.hypot(gx, gy))).astype(np.float32)
        del gx, gy

    def _open_land_photo(self, cfg):
        """Open land (meadows, fields, vineyards, orchards) takes its colour from the orthophoto itself:
        every field its own colour, mowing stripes, crop rows, tracks. The photo is matched to the
        level's base colour over open land (per-channel gain) and desaturated by PHOTO_DESAT so it
        does not read as the raw aerial's bright green; PHOTO_MIX of it replaces the base colour.
        The 1 m full-map photo everywhere, the 15 cm corridor photo where it exists (corridor tiles)."""
        import rasterio
        self.photo = None
        path = DATA / "ortho" / "fullmap.tif"
        if PHOTO_MIX <= 0 or not path.exists():
            return
        with rasterio.open(path) as src:
            img = src.read().transpose(1, 2, 0)[..., :3]
            tr = src.transform
        if img.shape[0] != self.base.shape[0] * 2 and img.shape[0] != self.cls["R"].shape[0]:
            print(f"  open-land photo: {img.shape[:2]} px does not match the layer grid, skipped")
            return
        self.photo = img
        n = img.shape[0]
        open_ = (self.cls["R"] > 200)
        sub = (slice(None, None, 4), slice(None, None, 4))
        ph = img[sub][open_[sub]].reshape(-1, 3).astype(np.float64) / 255
        bs = np.asarray(Image.fromarray(self.base).resize((n, n), Image.BILINEAR))[sub][open_[sub]].reshape(-1, 3) / 255
        self.photo_gain = bs.mean(0) / np.maximum(ph.mean(0), 1e-3)
        self.photo_mpp = 2 * self.half / n
        try:
            from fetch_corridor import CorridorOrtho
            self.corridor_photo = CorridorOrtho(cfg.raw.get("roads", {}).get("route", "climb"))
        except Exception as exc:                                  # noqa: BLE001 - no corridor tiles
            print(f"  corridor photo not used: {exc}")
            self.corridor_photo = None
        print(f"  open-land photo: gain {np.round(self.photo_gain, 3)}, mix {PHOTO_MIX}, desaturated {PHOTO_DESAT}")

    def _photo_colour(self, X, Y, fine):
        """Toned orthophoto colour (0..1) at local X, Y; the corridor's 15 cm photo where it has pixels."""
        n = self.photo.shape[0]
        col = (X + self.half) / self.photo_mpp - 0.5
        row = (self.half - Y) / self.photo_mpp - 0.5
        r0, r1 = int(max(np.floor(row.min()) - 2, 0)), int(min(np.ceil(row.max()) + 3, n))
        c0, c1 = int(max(np.floor(col.min()) - 2, 0)), int(min(np.ceil(col.max()) + 3, n))
        win = self.photo[r0:r1, c0:c1]
        coords = [np.clip(row - r0, 0, win.shape[0] - 1), np.clip(col - c0, 0, win.shape[1] - 1)]
        rgb = np.stack([map_coordinates(win[..., k].astype(np.float32), coords, order=1, mode="nearest")
                        for k in range(3)], -1) / 255
        if fine and self.corridor_photo is not None:
            hi = self.corridor_photo.sample(X, Y) / 255
            ok = np.isfinite(hi).all(-1)
            rgb[ok] = hi[ok]
        # shadows and crowns of trees in the photo (dark against the field around them): the field's
        # own colour there, so no shadow lies on open ground where AC has no tree
        from scipy.ndimage import gaussian_filter
        px = float(np.hypot(X[0, 1] - X[0, 0], Y[0, 1] - Y[0, 0])) or 1.0
        sig = PHOTO_SHADOW_M / px
        blur = np.stack([gaussian_filter(rgb[..., k], sig, mode="nearest") for k in range(3)], -1)
        lum, lum_b = rgb.mean(-1), np.maximum(blur.mean(-1), 1e-3)
        shade = smoothstep(0.8, 0.55, lum / lum_b)[..., None]
        rgb = rgb * (1 - shade) + blur * shade
        rgb = rgb * self.photo_gain
        grey = rgb.mean(-1, keepdims=True)
        return np.clip(grey + (rgb - grey) * (1 - PHOTO_DESAT), 0, 1)

    def _sample(self, arr, X, Y, order=1):
        """Bilinear sample of a north-up map at local (X, Y)."""
        n = arr.shape[0]
        col = (X + self.half) / self.mpp - 0.5
        row = (self.half - Y) / self.mpp - 0.5
        r0, r1 = int(max(np.floor(row.min()) - 2, 0)), int(min(np.ceil(row.max()) + 3, n))
        c0, c1 = int(max(np.floor(col.min()) - 2, 0)), int(min(np.ceil(col.max()) + 3, n))
        win = arr[r0:r1, c0:c1]
        coords = [np.clip(row - r0, 0, win.shape[0] - 1), np.clip(col - c0, 0, win.shape[1] - 1)]
        if win.ndim == 2:
            return map_coordinates(win.astype(np.float32), coords, order=order, mode="nearest")
        return np.stack([map_coordinates(win[..., k].astype(np.float32), coords, order=order, mode="nearest")
                         for k in range(win.shape[2])], -1)

    def paint(self, x0, y1, span, n, road_quads=None):
        """Window [x0, x0 + span] x [y1 - span, y1] (row 0 at the north edge y1), n x n pixels.
        road_quads: the climb's asphalt as quads (k, 4, 2) for the shoulder (corridor tiles).
        Returns (base RGB, weights RGBA), floats."""
        px = span / n
        g = (np.arange(n) + 0.5) * px
        X, Y = np.meshgrid(x0 + g, y1 - g)
        fine = road_quads is not None
        if fine:
            wx = X + 1.4 * fbm(X, Y, 6.0, 3, 501)
            wy = Y + 1.4 * fbm(X, Y, 6.0, 3, 502)
        else:
            wx, wy = X, Y
        # asphalt / villages keep their straight edges (the level's layer includes the climb's own,
        # narrower footprint: warped, it would show past the shoulder)
        w = np.stack([self._sample(self.cls[c], wx, wy) for c in "RGB"] + [self._sample(self.cls["A"], X, Y)], -1) / 255
        # villages and industrial areas: lawns, gravel yards (and some asphalt), split by noise
        split = 0.5 + 0.5 * fbm(X, Y, 9.0 if fine else 18.0, 3, 505)
        village = np.zeros(X.shape)
        for k, share in SHARED_LAYERS.items():
            if k in self.shared:
                lw = self._sample(self.shared[k], wx, wy) / 255
                village = np.maximum(village, lw)
                sh = np.array(share)
                sh = sh[None, None, :] * (1 + 1.2 * (split[..., None] - 0.5) * np.array([1.0, 0, -1.0, -1.0]))
                w += lw[..., None] * np.clip(sh, 0, None)
        if fine:
            for k in range(3):
                w[..., k] *= np.exp(0.9 * fbm(X, Y, 2.2, 3, 510 + k))
            w = w ** 2.0
        tot = w.sum(-1, keepdims=True)
        w = np.where(tot > 0.02, w / np.maximum(tot, 1e-6), np.array([1.0, 0, 0, 0]))
        # stony ground on steep banks (the steepest faces are separate rock meshes)
        slope = self._sample(self.slope, X, Y)
        stony = smoothstep(36, 48, slope + 6 * fbm(X, Y, 3.0, 2, 520)) * (1 - 0.6 * w[..., 1])
        w[..., 2] += 1.2 * stony
        w /= w.sum(-1, keepdims=True)
        if fine:
            d = self._asphalt_distance(x0, y1, span, n, road_quads)
            # the level's asphalt layer was painted wider than the climb (even widened): within
            # FOOTPRINT_M of the climb, paved ground that is not a side road becomes the ground
            # found just beyond it
            side = self._side_roads(x0, y1, span, n)
            own = w[..., 3] * smoothstep(FOOTPRINT_M, FOOTPRINT_M - 1.0, d) * ~side
            natural = (w[..., 3] < 0.2) & (d > 0.3)
            if own.any() and natural.any():
                _, (ri, ci) = distance_transform_edt(~natural, return_indices=True)
                fill = w[ri, ci, :3]
                fill /= np.maximum(fill.sum(-1, keepdims=True), 1e-6)
                w[..., :3] += own[..., None] * fill
                w[..., 3] -= own
            gw = np.clip(SHOULDER_M[0] + SHOULDER_M[1] * fbm(X, Y, 1.2, 3, 530), 0.2, 0.95)
            band = smoothstep(gw + 0.06, gw - 0.06, d) * (1 - smoothstep(0.3, 0.7, w[..., 3]))   # not on paved ground
            w = w * (1 - band[..., None]) + band[..., None] * np.array([0, 0, 1.0, 0])
        # colour: each class's target colour divided by what its detail photo will add
        base = self._sample(self.base, X, Y) / 255
        m = self.means
        open_base, lawn = base, np.broadcast_to(self.meadow_col, base.shape)
        if self.photo is not None:
            photo = self._photo_colour(X, Y, fine)
            open_base = (1 - PHOTO_MIX) * base + PHOTO_MIX * photo
            lawn = 0.5 * lawn + 0.5 * photo                       # gardens: half the photo (roofs lean into it)
        tR = open_base * (1 - village[..., None]) + lawn * village[..., None]     # lawns, not the map's grey
        if fine:
            tR = tR * (1 + 0.08 * fbm(X, Y, 1.5, 3, 540) + 0.06 * fbm(X, Y, 7.0, 2, 541))[..., None]
        tG = (1 - FOREST_BLEND) * base + FOREST_BLEND * FOREST_TONE * m["G"]
        if fine:
            tG = tG * (1 + 0.1 * fbm(X, Y, 2.0, 3, 542))[..., None]
        tB = GRAVEL_TONE * m["B"] * (1 + 0.06 * fbm(X, Y, 2.5, 2, 543))[..., None]
        tA = np.broadcast_to(ASPHALT_TONE * m["A"], X.shape + (3,))
        target = sum(w[..., k:k + 1] * t for k, t in enumerate((tR, tG, tB, tA)))
        mix = sum(w[..., k:k + 1] * m[c] for k, c in enumerate("RGBA"))
        col = np.clip(target / (MAGIC_MULT * np.maximum(mix, 0.05)), 0, 1)
        return col, w

    def _side_roads(self, x0, y1, span, n):
        """Mask of the paved side roads in the window."""
        from PIL import ImageDraw
        from shapely.geometry import box as sbox
        px = span / n
        img = Image.new("L", (n, n), 0)
        draw = ImageDraw.Draw(img)
        for k in self.side_tree.query(sbox(x0, y1 - span, x0 + span, y1)):
            g = self.side_roads[k]
            for poly in getattr(g, "geoms", [g]):
                c = np.asarray(poly.exterior.coords)
                draw.polygon(list(zip((c[:, 0] - x0) / px, (y1 - c[:, 1]) / px)), fill=255)
                for hole in poly.interiors:
                    c = np.asarray(hole.coords)
                    draw.polygon(list(zip((c[:, 0] - x0) / px, (y1 - c[:, 1]) / px)), fill=0)
        return np.asarray(img) > 0

    @staticmethod
    def _asphalt_distance(x0, y1, span, n, quads):
        """Metres from the climb's asphalt for every pixel of the window (0 on the asphalt)."""
        from PIL import ImageDraw
        px = span / n
        img = Image.new("L", (n, n), 0)
        draw = ImageDraw.Draw(img)
        cx = (quads[..., 0] - x0) / px
        cy = (y1 - quads[..., 1]) / px
        inside = (cx.max(1) > -2) & (cx.min(1) < n + 2) & (cy.max(1) > -2) & (cy.min(1) < n + 2)
        for qx, qy in zip(cx[inside], cy[inside]):
            draw.polygon(list(zip(qx, qy)), fill=255)
        mask = np.asarray(img) > 0
        if not mask.any():
            return np.full((n, n), 99.0)
        return distance_transform_edt(~mask) * px


if __name__ == "__main__":
    make_road_textures()
    make_rock_wall_textures()
    print(make_terrain_details())


def make_line_texture(w=64, h=1024):
    """White road paint, 4 m along the line (v) by its width (u): slightly off-white, worn into the
    asphalt at random (alpha), more at the edges of the stripe."""
    rng = np.random.default_rng(21)
    u = (np.arange(w) + 0.5) / w
    v = np.arange(h)
    grain = perlin(u[None, :] * 6, v[:, None] / h * 48, seed=3, period=(6, 48)) * 0.5 + \
        perlin(u[None, :] * 24, v[:, None] / h * 192, seed=4, period=(24, 192)) * 0.35
    edge = np.abs(u - 0.5)[None, :] * 2
    wear = grain + rng.normal(0, 0.12, (h, w)) - 0.9 * edge ** 6
    alpha = np.clip((wear + 0.55) * 4, 0, 1)
    col = np.clip(0.86 + 0.06 * grain[..., None] + rng.normal(0, 0.015, (h, w, 1)), 0, 1) * np.array([0.98, 0.98, 0.96])
    img = np.concatenate([col, alpha[..., None]], -1)
    TEX_DIR.mkdir(parents=True, exist_ok=True)
    Image.fromarray((img * 255).round().astype(np.uint8), "RGBA").save(TEX_DIR / "road_lines.png")
    print(f"road lines: paint texture, {alpha.mean():.0%} covered")


def make_timber_texture(w=256, h=1024):
    """Weathered timber (fence posts and rails), grain along v: 1 m along by 0.25 m across."""
    rng = np.random.default_rng(31)
    u = (np.arange(w) + 0.5) / w
    v = (np.arange(h) + 0.5) / h
    g = perlin(u[None, :] * 40, v[:, None] * 3, seed=5, period=(40, 3)) * 0.6 + \
        perlin(u[None, :] * 90, v[:, None] * 6, seed=6, period=(90, 6)) * 0.3
    base = np.array([0.43, 0.36, 0.28])
    col = base * (0.82 + 0.25 * g[..., None]) + rng.normal(0, 0.012, (h, w, 1))
    col = 0.75 * col + 0.25 * col.mean(-1, keepdims=True)                 # grey, weathered
    TEX_DIR.mkdir(parents=True, exist_ok=True)
    Image.fromarray((np.clip(col, 0, 1) * 255).round().astype(np.uint8)).save(TEX_DIR / "timber.png")


def make_vine_texture(w=512, h=1024):
    """Vine row foliage (ac_vineyards.py), 2 m along the row (u) by: rows 0-80 % the side of the
    leafy wall (top of the image = top of the wall, 1.35 m), 82-100 % the canopy seen from above.
    Leaves as noise blobs (alpha-tested holes), lighter and darker leaves, a few yellowing ones,
    grape bunches low on the wall; tileable along u."""
    rng = np.random.default_rng(41)
    u = (np.arange(w) + 0.5) / w
    v = (np.arange(h) + 0.5) / h
    U, Vv = np.meshgrid(u, v)
    side_h = 0.8
    t = np.clip(Vv / side_h, 0, 1)                       # 0 top of the wall .. 1 bottom
    lobes = perlin(U * 22, Vv * 22 * (h / w) / 2, seed=11, period=(22, 1000000)) * 0.6 + \
        perlin(U * 60, Vv * 60 * (h / w) / 2, seed=12, period=(60, 1000000)) * 0.4
    large = perlin(U * 5, Vv * 5 * (h / w) / 2, seed=13, period=(5, 1000000))
    # density: full in the middle of the wall, a ragged fringe on top, sparse low down (trunks)
    dens = np.where(Vv < side_h, smoothstep(0.0, 0.12, t) * (1 - 0.75 * smoothstep(0.62, 1.0, t)), 0.92)
    field = lobes + 0.35 * large + (dens - 0.5) * 1.3
    alpha = (field > 0.12).astype(float)
    leaf = np.array([0.24, 0.35, 0.12])
    tone = 0.85 + 0.25 * (perlin(U * 40, Vv * 40 * (h / w) / 2, seed=14, period=(40, 1000000)) * 0.5 + 0.5)
    # clumps of leaves: lit on their upper edge, shaded where the foliage is thin
    gy = np.gradient(lobes, axis=0)
    tone *= 0.72 + 0.45 * np.clip(field - 0.12, 0, 0.6) / 0.6 + np.clip(-gy * 40, -0.12, 0.12)
    col = leaf[None, None, :] * tone[..., None]
    yellow = (perlin(U * 9, Vv * 9 * (h / w) / 2, seed=15, period=(9, 1000000)) > 0.55)
    col[yellow] = col[yellow] * 0.6 + np.array([0.42, 0.40, 0.16]) * 0.4
    col *= (0.75 + 0.25 * smoothstep(1.0, 0.0, t))[..., None]       # darker inside / low down
    # grape bunches on the lower half of the wall
    for _ in range(14):
        cu, cv = rng.uniform(0, 1), rng.uniform(0.45, 0.72) * side_h
        r = rng.uniform(0.012, 0.02)
        m = ((U - cu) % 1.0 - 0.0) ** 2 / (r * r) + ((Vv - cv) * (h / w)) ** 2 / (2.2 * r) ** 2 < 1
        col[m] = np.array([0.20, 0.13, 0.20]) * (0.8 + 0.4 * rng.random())
        alpha[m] = 1.0
    col += rng.normal(0, 0.012, col.shape)
    img = np.concatenate([np.clip(col, 0, 1), alpha[..., None]], -1)
    TEX_DIR.mkdir(parents=True, exist_ok=True)
    Image.fromarray((img * 255).round().astype(np.uint8), "RGBA").save(TEX_DIR / "vine_leaves.png")
    print(f"vine leaves: {alpha[:int(h * side_h)].mean():.0%} of the wall covered")
