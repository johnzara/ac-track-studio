"""High-definition procedural textures for the generated buildings, with normal maps.

Every builder paints a colour image and a height field together; textures.write_all saves
the colour as <name>.png and the height-derived normal map as <name>_n.png (OpenGL / Y+
convention). Sizes keep the UV layout of textures.py:
  facades 1024 x 2048 px = one 3 m bay x two 3 m floors (bottom half = ground floor),
  roofs   1024 x 1024 px = 2 x 2 m, tile courses running along +V (downhill),
  campanile 1024 x 4096 px = one tower face, the full tower height (belfry at the top).
Details follow the Street View survey (streetview_reference_notes.md): plastered villas with a
darker plinth, string courses, stone window surrounds and sills, brown or green louvred
shutters or roller shutters, grilles on ground-floor windows, rain streaks under the sills;
coppi roofs; brick bell towers with a stone-framed twin-arched belfry.
"""

import numpy as np
from PIL import Image, ImageDraw

W, H = 1024, 2048
PX = W / 3.0                       # px per metre on facades


def _noise(h, w, rng, scale, amp):
    small = rng.normal(0, 1, (h // scale + 2, w // scale + 2)).astype(np.float32)
    img = Image.fromarray(small).resize((w, h), Image.BICUBIC)
    return np.asarray(img, np.float32) * amp


class Canvas:
    """Colour (h, w, 3) and height (h, w) painted together."""

    def __init__(self, w, h, rgb, rng):
        self.w, self.h, self.rng = w, h, rng
        self.c = np.zeros((h, w, 3), np.float32) + np.array(rgb, np.float32)
        self.z = np.zeros((h, w), np.float32)

    def _mask(self, draw):
        m = Image.new("L", (self.w, self.h), 0)
        draw(ImageDraw.Draw(m))
        return np.asarray(m, np.float32) / 255.0

    def rect(self, x0, y0, x1, y1, rgb=None, z=None, add_z=None, alpha=1.0):
        x0, y0, x1, y1 = (int(round(v)) for v in (x0, y0, x1, y1))
        x0, x1 = max(x0, 0), min(x1, self.w)
        y0, y1 = max(y0, 0), min(y1, self.h)
        if x1 <= x0 or y1 <= y0:
            return
        if rgb is not None:
            sl = self.c[y0:y1, x0:x1]
            sl[:] = sl * (1 - alpha) + np.asarray(rgb, np.float32) * alpha
        if z is not None:
            self.z[y0:y1, x0:x1] = z
        if add_z is not None:
            self.z[y0:y1, x0:x1] += add_z

    def poly(self, pts, rgb=None, z=None, alpha=1.0):
        m = self._mask(lambda d: d.polygon([tuple(p) for p in pts], fill=255))[..., None] * alpha
        if rgb is not None:
            self.c = self.c * (1 - m) + np.asarray(rgb, np.float32) * m
        if z is not None:
            self.z = self.z * (1 - m[..., 0]) + z * m[..., 0]

    def grain(self, amp_c, amp_z, scales=((2, 1.0), (12, 0.6), (90, 0.8))):
        for s, k in scales:
            n = _noise(self.h, self.w, self.rng, s, 1.0)
            self.c += n[..., None] * amp_c * k
            self.z += n * amp_z * k * (1.0 if s < 20 else 0.2)

    def streak(self, x0, x1, y0, length, strength=0.18):
        """Rain streak: darkening that fades downwards."""
        y0, y1 = int(y0), int(min(self.h, y0 + length))
        if y1 <= y0:
            return
        fade = np.linspace(1, 0, y1 - y0)[:, None, None] ** 1.5
        jitter = 0.6 + 0.4 * self.rng.random((1, int(x1) - int(x0), 1))
        sl = self.c[y0:y1, int(x0):int(x1)]
        sl *= 1 - strength * fade * jitter

    def images(self, normal_strength=2.0):
        rgb = Image.fromarray(np.clip(self.c, 0, 255).astype(np.uint8))
        return rgb, self.z * normal_strength


def normal_map(z):
    """OpenGL-style normal map from a height field (image rows grow downwards = -V)."""
    dzdx = np.gradient(z, axis=1)
    dzdrow = np.gradient(z, axis=0)
    nx, ny = -dzdx, dzdrow                 # +Y is up the image
    nz = np.ones_like(z)
    n = np.stack([nx, ny, nz], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    return Image.fromarray(((n * 0.5 + 0.5) * 255).astype(np.uint8))


# ------------------------------------------------------------------ facades

MARBLE = (224, 220, 208)


def _shade(rgb, k):
    return tuple(min(255, max(0, c * k)) for c in rgb)


def _window(cv, cx, sill_y, w_m, h_m, kind, shutter, rng, grille=False, frame=(236, 234, 228)):
    """kind: 'persiane' (open louvred side shutters), 'tapparella' (roller shutter part-way
    down), 'none'. sill_y is the image row of the sill top."""
    w, h = int(w_m * PX), int(h_m * PX)
    x0, x1 = cx - w // 2, cx + w // 2
    y1, y0 = int(sill_y), int(sill_y - h)
    s = int(0.11 * PX)
    cv.rect(x0 - s, y0 - s, x1 + s, y1, rgb=_shade(MARBLE, rng.uniform(0.95, 1.03)), z=0.55)   # stone surround
    cv.rect(x0 - s, y0 - s, x1 + s, y0 - s + 4, rgb=_shade(MARBLE, 0.8))                        # top joint
    cv.rect(x0, y0, x1, y1, rgb=(70, 66, 60), z=-1.4)                                             # reveal
    g = int(0.07 * PX)
    gy = np.linspace(0, 1, max(1, y1 - y0 - 2 * g))[:, None, None]
    glass = (1 - gy) * np.array([96, 112, 124]) + gy * np.array([34, 40, 46])
    gx0, gx1, gy0, gy1 = x0 + g, x1 - g, y0 + g, y1 - g
    cv.c[gy0:gy1, gx0:gx1] = glass
    cv.z[gy0:gy1, gx0:gx1] = -1.6
    # a diagonal sky reflection
    yy, xx = np.mgrid[gy0:gy1, gx0:gx1]
    refl = (((xx - gx0) + (yy - gy0) * 0.6) % (w * 1.6) < w * 0.25)
    cv.c[gy0:gy1, gx0:gx1][refl] += 22
    # casement frames: outer, mullion, transom
    t = int(0.045 * PX)
    for a, b, c_, d in ((gx0, gy0, gx1, gy0 + t), (gx0, gy1 - t, gx1, gy1), (gx0, gy0, gx0 + t, gy1),
                        (gx1 - t, gy0, gx1, gy1), (cx - t // 2, gy0, cx + t // 2, gy1),
                        (gx0, gy0 + (gy1 - gy0) // 3, gx1, gy0 + (gy1 - gy0) // 3 + t)):
        cv.rect(a, b, c_, d, rgb=frame, z=-1.2)
    if kind == "tapparella":
        drop = int((gy1 - gy0) * rng.uniform(0.2, 0.55))
        col = np.array(shutter, np.float32)
        for k, yy0 in enumerate(range(gy0, gy0 + drop, 9)):
            cv.rect(gx0, yy0, gx1, min(yy0 + 9, gy0 + drop), rgb=_shade(col, 1.0 if k % 2 else 0.86), z=-1.0 - 0.15 * (k % 2))
        cv.rect(gx0, gy0 + drop - 5, gx1, gy0 + drop, rgb=_shade(col, 0.6), z=-1.1)
    if grille:
        for gxp in range(gx0 + 18, gx1 - 10, 30):
            cv.rect(gxp, gy0, gxp + 5, gy1, rgb=(36, 36, 36), z=-0.4)
        for gyp in (gy0 + 30, gy1 - 30):
            cv.rect(gx0, gyp, gx1, gyp + 5, rgb=(36, 36, 36), z=-0.4)
    # sill, protruding, and its rain streak
    cv.rect(x0 - s - 12, y1, x1 + s + 12, y1 + int(0.07 * PX), rgb=_shade(MARBLE, 0.97), z=1.2)
    cv.rect(x0 - s - 12, y1 + int(0.07 * PX), x1 + s + 12, y1 + int(0.07 * PX) + 4, rgb=_shade(MARBLE, 0.55))
    cv.streak(x0 - s, x1 + s, y1 + int(0.07 * PX) + 4, rng.uniform(0.4, 0.9) * PX, 0.14)
    if kind == "persiane":
        sw = (x1 - x0) // 2
        col = np.array(shutter, np.float32)
        for sx in (x0 - s - sw - 4, x1 + s + 4):
            cv.rect(sx, y0 - 6, sx + sw, y1, rgb=_shade(col, 0.8), z=0.9)
            cv.rect(sx + 10, y0 + 6, sx + sw - 10, y1 - 10, rgb=col, z=0.7)
            for k, ly in enumerate(range(y0 + 8, y1 - 12, 14)):           # louvres
                cv.rect(sx + 10, ly, sx + sw - 10, ly + 7, rgb=_shade(col, 1.12), z=0.95)
                cv.rect(sx + 10, ly + 7, sx + sw - 10, ly + 14, rgb=_shade(col, 0.7), z=0.6)
            cv.rect(sx + sw // 2 - 2, y0, sx + sw // 2 + 2, y1, rgb=_shade(col, 0.6))
            for hy in (y0 + 30, y1 - 40):                                 # hinges
                cv.rect(sx + (sw - 16 if sx < x0 else 0), hy, sx + (sw if sx < x0 else 16), hy + 8, rgb=(40, 40, 40), z=1.0)


def _door(cv, cx, w_m, h_m, rgb, kind, rng):
    """Ground-floor garage door ('garage') or a French door with railing glass ('balcony',
    upper floor, its bottom at the floor line); bottom edge at image row y1."""
    w, h = int(w_m * PX), int(h_m * PX)
    x0, x1 = cx - w // 2, cx + w // 2
    if kind == "garage":
        y1 = H - 4
        y0 = y1 - h
        cv.rect(x0 - 16, y0 - 16, x1 + 16, y1, rgb=_shade(MARBLE, 0.95), z=0.5)
        cv.rect(x0, y0, x1, y1, rgb=rgb, z=-0.8)
        for k, yy in enumerate(range(y0, y1, 22)):            # sectional panels
            cv.rect(x0, yy, x1, yy + 4, rgb=_shade(rgb, 0.65), z=-1.0)
        cv.rect(x0, y1 - 10, x1, y1, rgb=_shade(rgb, 0.5))
        return
    y1 = 1024 - 6                                               # upper floor's floor line
    y0 = y1 - h
    s = int(0.11 * PX)
    cv.rect(x0 - s, y0 - s, x1 + s, y1, rgb=_shade(MARBLE, rng.uniform(0.95, 1.03)), z=0.55)
    cv.rect(x0, y0, x1, y1, rgb=(70, 66, 60), z=-1.4)
    g = int(0.07 * PX)
    gy = np.linspace(0, 1, max(1, y1 - y0 - 2 * g))[:, None, None]
    cv.c[y0 + g:y1 - g, x0 + g:x1 - g] = (1 - gy) * np.array([96, 112, 124]) + gy * np.array([34, 40, 46])
    cv.z[y0 + g:y1 - g, x0 + g:x1 - g] = -1.6
    t = int(0.045 * PX)
    for a_, b_, c_, d_ in ((x0 + g, y0 + g, x1 - g, y0 + g + t), (cx - t // 2, y0 + g, cx + t // 2, y1 - g),
                           (x0 + g, y0 + g, x0 + g + t, y1 - g), (x1 - g - t, y0 + g, x1 - g, y1 - g)):
        cv.rect(a_, b_, c_, d_, rgb=(236, 234, 228), z=-1.2)
    if rgb is not None:                                          # open shutters either side
        sw = (x1 - x0) // 2
        for sx in (x0 - s - sw - 4, x1 + s + 4):
            cv.rect(sx, y0 - 6, sx + sw, y1, rgb=_shade(rgb, 0.8), z=0.9)
            for ly in range(y0 + 8, y1 - 12, 14):
                cv.rect(sx + 10, ly, sx + sw - 10, ly + 7, rgb=_shade(rgb, 1.12), z=0.95)


def facade_residential(colour_rgb, shutter_rgb, seed, shop=False, plain=False, variant=None):
    rng = np.random.default_rng(seed)
    cv = Canvas(W, H, colour_rgb, rng)
    cv.grain(4.5, 0.25)
    # weathering: slightly greyer towards the ground, blotchy
    cv.c *= (1 - 0.06 * np.linspace(0, 1, H)[:, None, None] ** 3)
    cv.c += _noise(H, W, rng, 220, 6.0)[..., None]
    style = "tapparella" if rng.random() < 0.45 else "persiane"
    cx = W // 2
    # string course at each floor line (top of the ground-floor band = row 1024; it repeats
    # under every upper floor), and a thin one under the eave of each floor band
    course = _shade(colour_rgb, 1.06)
    cv.rect(0, 1024 - int(0.16 * PX), W, 1024, rgb=course, z=0.8)
    cv.rect(0, 1024 - 6, W, 1024, rgb=_shade(colour_rgb, 0.7), z=0.2)
    # plinth (zoccolo): rough darker render, 0.55 m, with a lip
    py0 = H - int(0.55 * PX)
    plinth = _shade(colour_rgb, 0.72)
    cv.rect(0, py0, W, H, rgb=plinth, z=0.5)
    cv.c[py0:] += _noise(H - py0, W, rng, 3, 7.0)[..., None]
    cv.z[py0:] += _noise(H - py0, W, rng, 3, 0.5)
    cv.rect(0, py0 - 8, W, py0, rgb=_shade(colour_rgb, 0.85), z=0.9)
    if variant == "balcony":        # upper floor: French door onto a balcony
        _door(cv, cx, 1.0, 2.3, shutter_rgb, "balcony", rng)
        _window(cv, cx, H - int(1.0 * PX), 1.0, 1.45, style, shutter_rgb, rng, grille=rng.random() < 0.6)
        return cv.images(2.2)
    if variant == "garage":         # ground floor: garage door; upper floor as usual
        _window(cv, cx, 1024 - int(0.95 * PX), 1.0, 1.45, style, shutter_rgb, rng)
        _door(cv, cx, 2.4, 2.2, _shade(shutter_rgb, 1.05), "garage", rng)
        return cv.images(2.2)
    if not plain:
        _window(cv, cx, 1024 - int(0.95 * PX), 1.0, 1.45, style, shutter_rgb, rng)
        if shop:
            x0, x1, top = 40, W - 40, H - int(2.7 * PX)
            cv.rect(x0 - 10, top - 50, x1 + 10, top, rgb=(160, 160, 156), z=0.6)              # shutter box
            cv.rect(x0, top, x1, H - 22, rgb=(44, 52, 60), z=-1.4)
            cv.rect(x0, top, x1, H - 22, rgb=(44, 52, 60))
            for xx in (x0, (x0 + x1) // 2 - 6, x1 - 12):
                cv.rect(xx, top, xx + 12, H - 22, rgb=(96, 96, 96), z=-1.0)
            cv.rect(x0, top + (H - top) // 6, x1, top + (H - top) // 6 + 10, rgb=(96, 96, 96), z=-1.0)
        else:
            _window(cv, cx, H - int(1.0 * PX), 1.0, 1.45, style, shutter_rgb, rng, grille=rng.random() < 0.6)
    return cv.images(2.2)


def facade_stone(seed, colour=(176, 168, 150), rubble=True):
    rng = np.random.default_rng(seed)
    cv = Canvas(W, H, _shade(colour, 0.55), rng)
    cv.z[:] = -1.0
    y = 0
    while y < H:
        hgt = int(rng.uniform(40, 95) if rubble else 104)
        x = -int(rng.uniform(0, 80))
        while x < W:
            wid = int(rng.uniform(60, 180) if rubble else 208)
            tint = rng.uniform(0.82, 1.12)
            c = np.array(colour, np.float32) * tint
            m = 5 if rubble else 3
            cv.rect(x + m, y + m, x + wid - m, y + hgt - m, rgb=c, z=0.0)
            x0, x1, y0, y1 = max(x + m, 0), min(x + wid - m, W), y + m, min(y + hgt - m, H)
            if x1 > x0 and y1 > y0:
                yy, xx = np.mgrid[y0:y1, x0:x1]
                bulge = np.sin(np.clip((xx - x0) / max(x1 - x0, 1), 0, 1) * np.pi) * np.sin((yy - y0) / max(y1 - y0, 1) * np.pi)
                cv.z[y0:y1, x0:x1] = 0.6 * bulge ** 0.4
            x += wid
        y += hgt
    cv.grain(6.0, 0.35, ((2, 1.0), (8, 0.8)))
    return cv.images(2.5)


def facade_brick(seed):
    rng = np.random.default_rng(seed)
    cv = Canvas(W, H, (176, 166, 152), rng)
    cv.z[:] = -0.8
    bh, bw = int(0.065 * PX), int(0.25 * PX)
    for row, y in enumerate(range(0, H, bh + 4)):
        off = 0 if row % 2 else bw // 2
        for x in range(-bw + off, W, bw + 4):
            t = rng.uniform(0.8, 1.12)
            cv.rect(x + 2, y + 2, x + bw, y + bh, rgb=(158 * t, 76 * t, 52 * t), z=0.2)
    cv.grain(5.0, 0.25, ((2, 1.0), (10, 0.5)))
    return cv.images(2.0)


def campanile(seed, plaster=None, single=False):
    """One face of a bell tower over its full height (v = 0 ground, 1 eave): brick, or rendered
    in `plaster` colour; twin arches (bifora) or one wide arch (`single`) in the belfry."""
    rng = np.random.default_rng(seed)
    w, h = 1024, 4096
    cv = Canvas(w, h, (172, 160, 146), rng)
    cv.z[:] = -0.8
    if plaster is not None:
        cv.rect(0, 0, w, h, rgb=plaster, z=0.0)
        cv.c += _noise(h, w, rng, 24, 6.0)[..., None]
    else:
        bh, bw = 22, 80                               # brick courses at texture scale
        for row, y in enumerate(range(0, h, bh + 4)):
            off = 0 if row % 2 else bw // 2
            for x in range(-bw + off, w, bw + 4):
                t = rng.uniform(0.8, 1.12)
                cv.rect(x + 2, y + 2, x + bw, y + bh, rgb=(150 * t, 72 * t, 50 * t), z=0.2)
    cv.grain(5.0, 0.2, ((2, 1.0), (10, 0.5)))
    stone = (214, 206, 188)
    # stone base (bottom 9 %) and corner quoins
    cv.rect(0, int(h * 0.91), w, h, rgb=_shade(stone, 0.88), z=0.5)
    cv.c[int(h * 0.91):] += _noise(h - int(h * 0.91), w, rng, 6, 9.0)[..., None]
    for y in range(0, int(h * 0.91), 110):
        for (x0, x1) in ((0, 90 if (y // 110) % 2 else 140), (w - (90 if (y // 110) % 2 else 140), w)):
            cv.rect(x0, y + 4, x1, y + 104, rgb=_shade(stone, rng.uniform(0.9, 1.02)), z=0.5)
    # narrow slit windows on the shaft
    for yc in (0.62, 0.42):
        y0, y1 = int(h * yc), int(h * yc) + 230
        cv.rect(w // 2 - 46, y0 - 20, w // 2 + 46, y1 + 20, rgb=stone, z=0.4)
        cv.rect(w // 2 - 22, y0, w // 2 + 22, y1, rgb=(30, 28, 26), z=-1.5)
    # cornice below the belfry
    for (yc, hh) in ((0.262, 60), (0.252, 30)):
        cv.rect(0, int(h * yc), w, int(h * yc) + hh, rgb=stone, z=1.0)
    cv.rect(0, int(h * 0.262) + 60, w, int(h * 0.262) + 70, rgb=_shade(stone, 0.5))
    # belfry (top fifth of the tower): twin arches (bifora) with a central column, stone frames
    yb0, yb1 = int(h * 0.045), int(h * 0.25)
    arch_w = 520 if single else 300
    for cx in ((w // 2,) if single else (w // 2 - 170, w // 2 + 170)):
        top = yb0 + arch_w // 2
        # frame
        cv.rect(cx - arch_w // 2 - 36, top, cx + arch_w // 2 + 36, yb1, rgb=stone, z=0.6)
        cv.poly([(cx + (arch_w // 2 + 36) * np.cos(a), top - (arch_w // 2 + 36) * np.sin(a)) for a in np.linspace(0, np.pi, 24)], rgb=stone, z=0.6)
        # opening
        cv.rect(cx - arch_w // 2, top, cx + arch_w // 2, yb1 - 40, rgb=(24, 22, 20), z=-2.0)
        cv.poly([(cx + arch_w // 2 * np.cos(a), top - arch_w // 2 * np.sin(a)) for a in np.linspace(0, np.pi, 24)], rgb=(24, 22, 20), z=-2.0)
        # parapet in the opening
        cv.rect(cx - arch_w // 2, yb1 - 260, cx + arch_w // 2, yb1 - 40, rgb=_shade(stone, 0.8), z=-0.6)
        # a hint of the bell
        cv.poly([(cx - 60, yb0 + 260), (cx + 60, yb0 + 260), (cx + 90, yb0 + 470), (cx - 90, yb0 + 470)], rgb=(70, 62, 40), z=-1.6)
    if not single:
        cv.rect(w // 2 - 30, yb0 + 100, w // 2 + 30, yb1 - 40, rgb=_shade(stone, 1.03), z=0.8)   # column
    cv.rect(0, yb1 - 40, w, yb1, rgb=stone, z=1.0)                                                    # sill course
    # crowning cornice
    cv.rect(0, 0, w, int(h * 0.035), rgb=stone, z=1.2)
    cv.rect(0, int(h * 0.035), w, int(h * 0.035) + 10, rgb=_shade(stone, 0.5))
    return cv.images(2.5)


# ------------------------------------------------------------------ roofs

def roof_coppi(seed, dark=False, base=None):
    """Coppi: concave channels and convex covers alternating across U (0.18 m pitch),
    courses every 0.4 m along V; tile-by-tile tint, lichen and soot."""
    rng = np.random.default_rng(seed)
    n = 1024
    pxm = n / 2.0
    base = np.array(base if base is not None else ((140, 70, 46) if dark else (176, 92, 60)), np.float32)
    x = np.arange(n)[None, :]
    y = np.arange(n)[:, None]
    ch = 0.18 * pxm
    phase = (x % ch) / ch                                 # 0..1 across one channel+cover pair
    prof = np.cos(phase * 2 * np.pi)                      # +1 cover crest, -1 channel bottom
    course = 0.4 * pxm
    yc = (y % course) / course                            # 0 top of a course .. 1 its lower end
    z = 0.8 * prof + 0.7 * yc                             # each course thickens towards its overlap
    lit = 0.82 + 0.18 * prof * (0.6 + 0.4 * (1 - yc))
    tile_id = (np.floor(x / ch) * 131 + np.floor(y / course) * 17).astype(np.int64)
    tint = 0.84 + 0.3 * ((tile_id * 2654435761) % 1000) / 1000.0
    c = base[None, None, :] * (lit * tint)[..., None]
    shadow = np.clip((yc - 0.86) / 0.14, 0, 1)            # dark gap under the next course
    c *= (1 - 0.55 * shadow)[..., None]
    z -= 1.2 * shadow
    c += _noise(n, n, rng, 3, 7.0)[..., None]
    lichen = np.clip(_noise(n, n, rng, 24, 1.0) - 1.5, 0, 1)
    c = c * (1 - 0.35 * lichen[..., None]) + np.array([118, 116, 92]) * 0.35 * lichen[..., None]
    soot = np.clip(_noise(n, n, rng, 140, 1.0), -1, 1)
    c *= (1 - 0.08 * soot)[..., None]
    cv = Canvas(n, n, (0, 0, 0), rng)
    cv.c, cv.z = c.astype(np.float32), z.astype(np.float32)
    return cv.images(3.0)


def roof_flat_hd(seed, rgb):
    """Flat roof: membrane / gravel, seams every 1 m, a little dirt."""
    rng = np.random.default_rng(seed)
    cv = Canvas(1024, 1024, rgb, rng)
    cv.grain(6.0, 0.3, ((2, 1.0), (16, 0.6), (120, 0.8)))
    for k in range(0, 1024, 512):
        cv.rect(k, 0, k + 3, 1024, rgb=_shade(rgb, 0.85), z=0.4)
    return cv.images(1.5)
