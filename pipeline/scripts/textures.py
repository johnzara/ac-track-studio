#!/usr/bin/env python3
"""Procedural facade and roof textures in the local style (see streetview_reference_notes.md).

Facade textures are 512 x 1024 px and cover a 3 m wide bay: the bottom half is the ground
floor (3 m), the top half an upper floor (3 m). Walls are UV-mapped so each bay shows one
window column and each floor one half of the texture.
Roof textures are 512 x 512 px covering 2 x 2 m, with tile rows running along +V (downhill).
"""

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

import textures_hd as hd

PX_PER_M = 512 / 3.0
PLASTER = {  # Veneto plaster palette (sRGB 0-255)
    "cream": (226, 212, 178), "ochre": (214, 168, 98), "salmon": (222, 160, 130),
    "pink": (226, 186, 176), "yellow": (232, 206, 126), "white": (236, 233, 222),
    "terracotta": (196, 112, 76),
}
SHUTTERS = {"green": (62, 96, 64), "brown": (104, 70, 44), "grey": (118, 124, 116)}
INDUSTRIAL = {"grey": (168, 168, 160), "beige": (200, 188, 160), "white": (222, 222, 214)}


def _noise(h, w, rng, scale=8, amp=10.0):
    small = rng.normal(0, 1, (h // scale + 2, w // scale + 2))
    img = Image.fromarray(((small - small.min()) / (np.ptp(small) + 1e-9) * 255).astype(np.uint8))
    big = np.asarray(img.resize((w, h), Image.BICUBIC), dtype=np.float32) / 255 - 0.5
    return big * amp * 2 + rng.normal(0, amp * 0.25, (h, w))


def _plaster(colour, rng, w=512, h=1024, dirt=True):
    base = np.array(colour, np.float32)[None, None, :]
    n = _noise(h, w, rng, 16, 7)[..., None] + _noise(h, w, rng, 3, 3)[..., None]
    img = base + n
    if dirt:  # darker streaks near the ground and under windows
        y = np.linspace(1, 0, h)[:, None, None]
        img *= 1 - 0.10 * np.clip((y - 0.85) / 0.15, 0, 1)
    return img


def _to_image(arr):
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _window(d, cx, sill_y, shutter, rng, w_m=1.0, h_m=1.45, grille=False):
    """Draw a window with open shutters; sill_y is the pixel row of the sill (y grows down)."""
    w, h = int(w_m * PX_PER_M), int(h_m * PX_PER_M)
    x0, y1 = cx - w // 2, sill_y
    y0 = y1 - h
    surround = (232, 228, 214)
    d.rectangle([x0 - 8, y0 - 8, x0 + w + 8, y1 + 4], fill=surround)          # stone frame
    d.rectangle([x0 - 14, y1, x0 + w + 14, y1 + 12], fill=(220, 216, 204))     # marble sill
    d.rectangle([x0, y0, x0 + w, y1], fill=(46, 52, 60))                        # glass
    d.rectangle([x0 + w // 2 - 2, y0, x0 + w // 2 + 2, y1], fill=(225, 225, 220))  # mullion
    d.rectangle([x0, y0 + h // 3 - 2, x0 + w, y0 + h // 3 + 2], fill=(225, 225, 220))
    d.polygon([(x0 + 6, y0 + 6), (x0 + w // 3, y0 + 6), (x0 + 6, y0 + h // 2)], fill=(80, 92, 104))
    if grille:
        for gx in range(x0 + 10, x0 + w, 22):
            d.rectangle([gx, y0, gx + 3, y1], fill=(40, 40, 40))
    if shutter is not None:
        sw = int(0.5 * PX_PER_M)
        for sx in (x0 - 8 - sw, x0 + w + 8):
            d.rectangle([sx, y0 - 6, sx + sw, y1 + 2], fill=shutter)
            dark = tuple(max(c - 30, 0) for c in shutter)
            for ly in range(y0, y1, 9):
                d.line([(sx + 4, ly), (sx + sw - 4, ly)], fill=dark, width=3)
            d.rectangle([sx, y0 - 6, sx + sw, y1 + 2], outline=dark, width=3)


def facade_residential(colour, shutter, seed, shop=False, plain=False):
    rng = np.random.default_rng(seed)
    img = _to_image(_plaster(PLASTER[colour], rng))
    d = ImageDraw.Draw(img)
    cx = 256
    # ground-floor plinth (zoccolo)
    plinth = tuple(int(c * 0.82) for c in PLASTER[colour])
    d.rectangle([0, 1024 - int(0.45 * PX_PER_M), 512, 1024], fill=plinth)
    if not plain:
        _window(d, cx, 512 - int(0.95 * PX_PER_M), SHUTTERS[shutter], rng)       # upper floor
        if shop:
            x0, x1 = 30, 482
            top = 1024 - int(2.7 * PX_PER_M)
            d.rectangle([x0 - 6, top - 40, x1 + 6, top], fill=(150, 150, 150))   # roller-shutter box
            d.rectangle([x0, top, x1, 1024 - 20], fill=(40, 46, 54))              # shop glass
            d.rectangle([x0, top, x1, 1024 - 20], outline=(90, 90, 90), width=8)
            d.rectangle([250, top, 262, 1024 - 20], fill=(90, 90, 90))
        else:
            _window(d, cx, 1024 - int(1.0 * PX_PER_M), SHUTTERS[shutter], rng, grille=True)
    # floor line / string course
    d.rectangle([0, 506, 512, 516], fill=tuple(int(c * 0.92) for c in PLASTER[colour]))
    return img.filter(ImageFilter.SMOOTH)


def facade_industrial(colour, seed):
    rng = np.random.default_rng(seed)
    arr = _plaster(INDUSTRIAL[colour], rng, dirt=True)
    img = _to_image(arr)
    d = ImageDraw.Draw(img)
    for x in (0, 256, 511):                                # panel joints
        d.line([(x, 0), (x, 1024)], fill=(110, 110, 104), width=4)
    d.line([(0, 512), (512, 512)], fill=(120, 120, 114), width=3)
    band0, band1 = int(0.9 * PX_PER_M), int(1.9 * PX_PER_M)  # ribbon window in the upper half
    d.rectangle([0, band0, 512, band1], fill=(52, 60, 70))
    for x in range(0, 512, 128):
        d.rectangle([x, band0, x + 6, band1], fill=(150, 150, 150))
    return img


def facade_stone(seed, colour=(176, 168, 150), rubble=True):
    rng = np.random.default_rng(seed)
    img = Image.new("RGB", (512, 1024), tuple(int(c * 0.65) for c in colour))
    d = ImageDraw.Draw(img)
    y = 0
    while y < 1024:
        hgt = int(rng.uniform(22, 48) if rubble else 52)
        x = -int(rng.uniform(0, 40))
        while x < 512:
            wid = int(rng.uniform(30, 90) if rubble else 104)
            tint = rng.uniform(0.85, 1.12)
            c = tuple(int(min(255, v * tint)) for v in colour)
            d.rounded_rectangle([x + 3, y + 3, x + wid - 3, y + hgt - 3], radius=6 if rubble else 1, fill=c)
            x += wid
        y += hgt
    return img.filter(ImageFilter.SMOOTH)


def facade_brick(seed):
    rng = np.random.default_rng(seed)
    img = Image.new("RGB", (512, 1024), (180, 170, 160))
    d = ImageDraw.Draw(img)
    for row, y in enumerate(range(0, 1024, 16)):
        off = 0 if row % 2 else 24
        for x in range(-48 + off, 512, 48):
            t = rng.uniform(0.85, 1.1)
            d.rectangle([x + 2, y + 2, x + 46, y + 14], fill=(int(160 * t), int(78 * t), int(54 * t)))
    return img


def roof_coppi(seed, dark=False):
    rng = np.random.default_rng(seed)
    base = np.array((150, 72, 48) if dark else (178, 92, 60), np.float32)
    h = w = 512
    arr = np.zeros((h, w, 3), np.float32) + base
    ch = 512 / (2.0 / 0.18)            # channel width in px (0.18 m)
    x = np.arange(w)[None, :, None]
    profile = 0.75 + 0.25 * np.cos((x % ch) / ch * 2 * np.pi)   # curved tiles across V
    arr *= profile
    rows = 512 / (2.0 / 0.40)           # tile courses every 0.40 m along V
    y = np.arange(h)[:, None, None]
    arr *= 0.9 + 0.1 * ((y % rows) / rows)
    arr += _noise(h, w, rng, 4, 8)[..., None]
    patches = _noise(h, w, rng, 64, 18)[..., None]
    arr += patches * np.array([0.6, 0.5, 0.3])
    return _to_image(arr)


def roof_flat(seed, colour=(150, 146, 138)):
    rng = np.random.default_rng(seed)
    return _to_image(np.array(colour, np.float32) + _noise(512, 512, rng, 6, 9)[..., None])


def roof_metal(seed, colour=(150, 154, 156)):
    rng = np.random.default_rng(seed)
    x = np.arange(512)[None, :, None]
    arr = np.array(colour, np.float32) * (0.85 + 0.15 * (np.sin(x / 512 * 2 * np.pi * 10) > 0))
    arr = arr + _noise(512, 512, rng, 32, 6)[..., None]
    return _to_image(np.broadcast_to(arr, (512, 512, 3)))


def soffit():
    return Image.new("RGB", (64, 64), (96, 80, 66))


# ---------------------------------------------------------------------------------------------
# roadside boundaries (boundaries.py). "*_alpha" textures are RGBA cut-outs drawn on one
# double-sided quad: 512 px = 2 m along the fence, the full height of the panel vertically.

def _fence(kind):
    w, h = 512, 256
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if kind == "iron":          # wrought iron: bars every 12 cm with spear tips, two rails
        col = (30, 30, 30, 255)
        for x in range(8, w, 31):
            d.rectangle([x, 22, x + 4, h - 6], fill=col)
            d.polygon([(x - 4, 24), (x + 2, 6), (x + 8, 24)], fill=col)
        d.rectangle([0, 40, w, 47], fill=col)
        d.rectangle([0, h - 18, w, h - 10], fill=col)
    elif kind == "picket":      # brown wooden pickets 8 cm wide, 5.5 cm gaps, two rails behind
        rng = np.random.default_rng(7)
        for x in range(4, w, 35):
            c = tuple(int(v) for v in np.array([110, 72, 44]) + rng.integers(-10, 10, 3)) + (255,)
            d.rectangle([x, 14, x + 20, h - 2], fill=c)
            d.polygon([(x, 14), (x + 10, 2), (x + 20, 14)], fill=c)
            d.line([(x + 20, 14), (x + 20, h - 2)], fill=(60, 38, 22, 255), width=2)
        for y in (70, 190):
            d.rectangle([0, y, w, y + 12], fill=(84, 54, 32, 255))
    elif kind == "mesh":        # green plastic-coated wire mesh on a frame
        col = (54, 92, 58, 255)
        for x in range(-h, w + h, 22):
            d.line([(x, 0), (x + h, h)], fill=col, width=2)
            d.line([(x + h, 0), (x, h)], fill=col, width=2)
        d.rectangle([0, 0, w, 5], fill=col)
        d.rectangle([0, h - 5, w, h], fill=col)
    return img


def _chainlink():
    w, h = 512, 512
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    col = (150, 154, 156, 255)
    for x in range(-h, w + h, 24):                      # galvanised diamond mesh
        d.line([(x, 0), (x + h, h)], fill=col, width=2)
        d.line([(x + h, 0), (x, h)], fill=col, width=2)
    d.rectangle([0, 0, w, 6], fill=(120, 124, 126, 255))  # top rail
    return img


def _barbed():
    w, h = 512, 128
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for y in (16, 64, 112):
        d.line([(0, y), (w, y)], fill=(90, 92, 94, 255), width=3)
        for x in range(8, w, 40):
            d.line([(x - 5, y - 6), (x + 5, y + 6)], fill=(90, 92, 94, 255), width=2)
            d.line([(x - 5, y + 6), (x + 5, y - 6)], fill=(90, 92, 94, 255), width=2)
    return img


def _stripes():
    img = Image.new("RGB", (256, 32), (236, 236, 232))
    d = ImageDraw.Draw(img)
    for x in range(0, 256, 64):
        d.rectangle([x, 0, x + 31, 32], fill=(196, 34, 30))
    return img


def _flat_rgb(colour, seed, amp=8):
    rng = np.random.default_rng(seed)
    return _to_image(np.array(colour, np.float32)[None, None, :] + _noise(256, 256, rng, 8, amp)[..., None])


def catalogue():
    """name -> (builder, kind) for every texture the building exporter can use."""
    cat = {}
    for i, colour in enumerate(PLASTER):
        for j, shutter in enumerate(SHUTTERS):
            cat[f"bld_res_{colour}_{shutter}"] = (lambda c=colour, s=shutter, k=i * 10 + j: hd.facade_residential(PLASTER[c], SHUTTERS[s], k), "facade")
            for var in ("balcony", "garage"):
                cat[f"bld_res_{colour}_{shutter}_{var[0]}"] = (
                    lambda c=colour, s=shutter, k=i * 10 + j, v=var: hd.facade_residential(PLASTER[c], SHUTTERS[s], k, variant=v), "facade")
        cat[f"bld_shop_{colour}"] = (lambda c=colour, k=i: hd.facade_residential(PLASTER[c], SHUTTERS["brown"], 100 + k, shop=True), "facade")
        cat[f"bld_plain_{colour}"] = (lambda c=colour, k=i: hd.facade_residential(PLASTER[c], SHUTTERS["brown"], 200 + k, plain=True), "facade")
    for i, colour in enumerate(INDUSTRIAL):
        cat[f"bld_ind_{colour}"] = (lambda c=colour, k=i: facade_industrial(c, 300 + k), "facade")
    cat["bld_stone_rubble"] = (lambda: hd.facade_stone(400), "facade")
    cat["bld_stone_ashlar"] = (lambda: hd.facade_stone(401, (214, 206, 190), rubble=False), "facade")
    cat["bld_stone_basalt"] = (lambda: hd.facade_stone(402, (92, 90, 88)), "facade")
    cat["bld_brick"] = (lambda: hd.facade_brick(403), "facade")
    cat["bld_campanile"] = (lambda: hd.campanile(404), "facade")
    cat["bld_campanile_cream"] = (lambda: hd.campanile(405, plaster=(222, 210, 176), single=True), "facade")
    cat["roof_coppi"] = (lambda: hd.roof_coppi(500), "roof")
    cat["roof_coppi_dark"] = (lambda: hd.roof_coppi(501, dark=True), "roof")
    cat["roof_coppi_light"] = (lambda: hd.roof_coppi(513, base=(196, 118, 84)), "roof")
    cat["roof_grey"] = (lambda: hd.roof_coppi(514, base=(132, 128, 124)), "roof")      # cement tiles
    cat["roof_dark"] = (lambda: hd.roof_coppi(515, base=(78, 76, 76)), "roof")         # dark tiles / slate
    cat["roof_light"] = (lambda: hd.roof_flat_hd(516, (196, 194, 186)), "roof")        # membrane / gravel
    cat["roof_flat"] = (lambda: roof_flat(502), "roof")
    cat["roof_metal"] = (lambda: roof_metal(503), "roof")
    cat["roof_green"] = (lambda: roof_metal(517, (70, 104, 74)), "roof")
    cat["roof_soffit"] = (soffit, "roof")
    cat["metal_dark"] = (lambda: roof_flat(504, (46, 48, 50)), "roof")
    cat["concrete"] = (lambda: roof_flat(505, (168, 164, 154)), "roof")
    cat["deck_asphalt"] = (lambda: roof_flat(506, (72, 72, 74)), "roof")
    cat["fence_iron_alpha"] = (lambda: _fence("iron"), "fence")
    cat["fence_picket_alpha"] = (lambda: _fence("picket"), "fence")
    cat["fence_mesh_alpha"] = (lambda: _fence("mesh"), "fence")
    cat["pillar_redbrown"] = (lambda: _flat_rgb((132, 66, 46), 507), "roof")
    cat["fence_chainlink_alpha"] = (_chainlink, "fence")
    cat["fence_barbed_alpha"] = (_barbed, "fence")
    cat["barrier_stripes"] = (_stripes, "roof")
    cat["cross_white"] = (lambda: _flat_rgb((222, 224, 222), 509, 4), "roof")
    cat["timber_dark"] = (lambda: _flat_rgb((92, 64, 42), 510, 10), "roof")
    cat["bench_wood"] = (lambda: _flat_rgb((128, 92, 58), 511, 10), "roof")
    cat["bld_military"] = (lambda: hd.facade_residential((198, 192, 172), (112, 118, 110), 512), "facade")
    cat["wall_plaster"] = (lambda: _to_image(_plaster(PLASTER["white"], np.random.default_rng(508), 512, 512)), "roof")
    return cat


def write_all(out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    cat = catalogue()
    for name, (builder, _) in cat.items():
        path = out_dir / f"{name}.png"
        if path.exists() and path.stat().st_mtime > _code_mtime():
            continue
        res = builder()
        if isinstance(res, tuple):          # HD texture: colour + height -> normal map
            img, z = res
            nm = hd.normal_map(z)
            if name[-2:] in ("_b", "_g"):    # facade variants: half resolution (mod size)
                img = img.resize((img.width // 2, img.height // 2), Image.LANCZOS)
                nm = nm.resize((nm.width // 2, nm.height // 2), Image.LANCZOS)
            img.save(path, optimize=True)
            nm.save(out_dir / f"{name}_n.png", optimize=True)
        else:
            res.save(path, optimize=True)
    return cat


def _code_mtime():
    """Textures are regenerated when the texture code is newer than the image."""
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    return max(os.path.getmtime(os.path.join(here, f)) for f in ("textures.py", "textures_hd.py"))


if __name__ == "__main__":
    from common import OUTPUT
    cat = write_all(OUTPUT / "textures")
    print(f"{len(cat)} textures in {OUTPUT / 'textures'}")
