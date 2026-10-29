"""Place-name and tourist signs along the hill climb (config.json "name_signs").

Italian practice (Codice della strada): a locality sign is a white plate with a black inner
border and the name in black; its back, seen by traffic leaving the place, is the same plate
with a red diagonal bar ("fine"). Comuni and frazioni are written in capitals, contrade in
mixed case. Tourist destinations are brown plates with white text and border.

Each entry: {"route_m", "text", "kind": "locality" | "tourist", "locality": "above" | "below"
(which way the place lies along the climb, for locality boundaries), "side": "left" | "right"
(optional; default: on the right of the traffic the front faces), "subtext" (tourist)}.
The front of a locality sign faces the traffic entering the place.

build() writes output/scenery/name_signs/ (one Collada with every sign, DDS plate textures,
materials) for ac_scenery.py; scenery_inputs.py calls it.
"""

import json
import math
import re

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import dds
from boundaries import _quad
from buildings_mesh import TexMesh, collada_lods
from climb_report import climb_polyline
from common import OUTPUT, Config, Heightfield

FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
PX_PER_M = 600
POST = "metal_dark"                # our galvanised steel (output/textures/metal_dark.png)
WHITE, BLACK, RED = (246, 246, 242), (22, 22, 22), (200, 30, 35)
BROWN, BROWN_TEXT = (112, 66, 40), (250, 250, 246)


def _slug(text, variant):
    return "sign_" + re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") + "_" + variant


def _font(px):
    try:
        return ImageFont.truetype(FONT, px)
    except OSError:
        return ImageFont.load_default()


def plate_size(text, kind, sub=None):
    h = 0.6 if kind == "tourist" else (0.55 if text.isupper() else 0.42)
    f = _font(200)
    tw = max(f.getlength(t) for t in [text] + ([sub] if sub else [])) / 200
    th = 0.42 * h if not sub else 0.3 * h
    w = max(0.9, tw * th / 0.72 + 0.32 * h)
    return round(w, 2), h


def render(text, kind, end=False, sub=None):
    w, h = plate_size(text, kind, sub)
    W, H = int(w * PX_PER_M), int(h * PX_PER_M)
    bg, fg = (BROWN, BROWN_TEXT) if kind == "tourist" else (WHITE, BLACK)
    im = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(im)
    m, t = int(0.045 * H), max(3, int(0.035 * H))
    d.rounded_rectangle([m, m, W - m, H - m], radius=int(0.08 * H), outline=fg, width=t)
    lines = [text] + ([sub] if sub else [])
    size = int(H * (0.42 if not sub else 0.3))
    f = _font(size)
    while max(f.getlength(x) for x in lines) > W - 4 * m and size > 10:
        size -= 2
        f = _font(size)
    if sub:
        d.text((W / 2, H * 0.38), text, font=f, fill=fg, anchor="mm")
        d.text((W / 2, H * 0.72), sub, font=_font(int(size * 0.75)), fill=fg, anchor="mm")
    else:
        d.text((W / 2, H / 2 + size * 0.03), text, font=f, fill=fg, anchor="mm")
    if end:                                   # red bar from bottom left to top right
        bar = int(0.11 * H)
        d.line([(m * 2, H - m * 2), (W - m * 2, m * 2)], fill=RED, width=bar)
    tw = 1 << int(math.ceil(math.log2(max(W, 64))))
    return im.resize((min(tw, 2048), min(tw, 2048) // 4 if w / h > 3 else min(tw, 2048) // 2), Image.LANCZOS)


BLUE, WOOD, WOOD_TEXT = (24, 74, 160), (104, 70, 42), (236, 222, 190)


def render_arrow(text, colour, arrow):
    """Italian direction plate: white text on blue (destinations) or brown (tourist), white
    border, arrow at the pointing end; white plates (businesses) with dark text."""
    W, H = 1024, 200
    bg, fg = {"blue": (BLUE, (250, 250, 250)), "brown": (BROWN, BROWN_TEXT), "white": (WHITE, (40, 40, 40))}[colour]
    im = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(im)
    d.rectangle([6, 6, W - 7, H - 7], outline=fg, width=7)
    ax = 70 if arrow == "left" else W - 70
    sgn = -1 if arrow == "left" else 1
    d.polygon([(ax + sgn * 45, H / 2), (ax - sgn * 10, H / 2 - 50), (ax - sgn * 10, H / 2 + 50)], fill=fg)
    d.rectangle([min(ax - sgn * 10, ax - sgn * 55), H / 2 - 14, max(ax - sgn * 10, ax - sgn * 55), H / 2 + 14], fill=fg)
    size = 112
    f = _font(size)
    while f.getlength(text) > W - 210 and size > 30:
        size -= 4
        f = _font(size)
    cx = W / 2 + (40 if arrow == "left" else -40)
    d.text((cx, H / 2), text, font=f, fill=fg, anchor="mm")
    return im


def render_rustic(text):
    """Contrada name on a brown timber board, letters routed and painted cream."""
    W, H = 1024, 256
    rng = np.random.default_rng(len(text))
    arr = np.array(WOOD, np.float32)[None, None, :] * (0.9 + 0.12 * rng.random((H, 1, 1))) * np.ones((H, W, 3))
    for y in range(0, H, 64):
        arr[y:y + 2] *= 0.6                                   # plank joints
    im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(im)
    size = 120
    f = _font(size)
    while f.getlength(text) > W - 90 and size > 30:
        size -= 4
        f = _font(size)
    d.text((W / 2 + 3, H / 2 + 3), text, font=f, fill=(58, 38, 24), anchor="mm")
    d.text((W / 2, H / 2), text, font=f, fill=WOOD_TEXT, anchor="mm")
    return im


def plan(cfg):
    c, w, s = climb_polyline()
    hf = Heightfield(cfg, OUTPUT / "dem_final.tif")
    out = []
    for e in cfg.raw.get("name_signs", []):
        if "route_m" not in e:
            continue
        m = float(np.clip(e["route_m"], 3, s[-1] - 3))
        p = np.array([np.interp(m, s, c[:, k]) for k in range(2)])
        a = np.array([np.interp(m - 2, s, c[:, k]) for k in range(2)])
        b = np.array([np.interp(m + 2, s, c[:, k]) for k in range(2)])
        d = (b - a) / np.linalg.norm(b - a)                 # uphill
        left = np.array([-d[1], d[0]])
        if e["kind"] in ("locality",):
            facing_up = e.get("locality", "above") == "above"   # entering uphill: faces uphill traffic
        else:
            facing_up = e.get("faces", "uphill") == "uphill"
        travel = d if facing_up else -d                       # traffic that reads the front
        side = e.get("side")
        if side is None:
            off = -left if facing_up else left                # on that traffic's right
        else:
            off = left if side == "left" else -left
        half_w = float(np.interp(m, s, w)) / 2
        q = p + off * (half_w + 0.9)
        z = float(hf.sample(np.array([q[0]]), np.array([q[1]]))[0])
        out.append({**e, "x": float(q[0]), "y": float(q[1]), "z": z, "front": (-travel).tolist()})
    return out


def build(cfg, out_dir):
    """The signs as out_dir/name_signs/name_signs.dae + main.materials.json + textures/ (ac_scenery.py)."""
    signs = plan(cfg)
    if not signs:
        return
    shape_dir = out_dir / "name_signs"
    tex_dir = shape_dir / "textures"
    tex_dir.mkdir(parents=True, exist_ok=True)
    mesh, col = TexMesh(), TexMesh()
    mats = {}

    def material(name, img):
        if name not in mats:
            dds.write(img, tex_dir / f"{name}.color.dds", "DXT1")
            mats[name] = {"name": name, "Stages": [{"colorMap": f"textures/{name}.color.dds"}]}
        return name

    for sg in signs:
        kind, text, sub = sg["kind"], sg.get("text", ""), sg.get("subtext")
        n = np.array(sg["front"])
        right = np.array([-n[1], n[0]])
        base = np.array([sg["x"], sg["y"]])
        if kind == "direction_stack":
            plates = sg["plates"]
            _post(mesh, col, base, sg["z"] - 0.4, sg["z"] + 1.0 + 0.3 * len(plates) + 0.1)
            for k, pl in enumerate(plates):
                img = render_arrow(pl["text"], pl["colour"], pl["arrow"])
                mname = material(_slug(pl["text"] + pl["colour"] + pl["arrow"], "dir"), img)
                z0 = sg["z"] + 1.0 + 0.3 * (len(plates) - 1 - k)
                wpl = 1.3
                off = right * (wpl / 2 - 0.1) * (1 if pl["arrow"] == "right" else -1)
                q = base + n * 0.05 + off
                v = [[*(q - right * wpl / 2), z0], [*(q + right * wpl / 2), z0], [*(q + right * wpl / 2), z0 + 0.26], [*(q - right * wpl / 2), z0 + 0.26]]
                _quad(mesh, mname, v, [(0, 0), (1, 0), (1, 1), (0, 1)], [n[0], n[1], 0])
                qb = base + n * 0.03 + off
                v = [[*(qb + right * wpl / 2), z0], [*(qb - right * wpl / 2), z0], [*(qb - right * wpl / 2), z0 + 0.26], [*(qb + right * wpl / 2), z0 + 0.26]]
                _quad(mesh, POST, v, [(0, 0), (1, 0), (1, 1), (0, 1)], [-n[0], -n[1], 0])
            continue
        if kind == "rustic":
            board = material(_slug(text, "rustic"), render_rustic(text))
            wood = material("sign_wood_post", Image.new("RGB", (64, 64), (86, 58, 36)))
            w, h = max(1.1, 0.085 * len(text) + 0.3), 0.3
            for sx in (-1, 1):
                pp = base + right * sx * (w / 2 - 0.06)
                _box(mesh, wood, pp, n, 0.1, sg["z"] - 0.4, sg["z"] + 1.75)
            z0 = sg["z"] + 1.05
            q = base + n * 0.07
            v = [[*(q - right * w / 2), z0], [*(q + right * w / 2), z0], [*(q + right * w / 2), z0 + h], [*(q - right * w / 2), z0 + h]]
            _quad(mesh, board, v, [(0, 0), (1, 0), (1, 1), (0, 1)], [n[0], n[1], 0])
            v = [[*(q + right * w / 2 - n * 0.04), z0], [*(q - right * w / 2 - n * 0.04), z0], [*(q - right * w / 2 - n * 0.04), z0 + h], [*(q + right * w / 2 - n * 0.04), z0 + h]]
            _quad(mesh, board, v, [(0, 0), (1, 0), (1, 1), (0, 1)], [-n[0], -n[1], 0])
            zr = sg["z"] + 1.75                                   # little gable roof over the board
            for sn in (1, -1):
                e0, e1 = base + n * sn * 0.3 - right * (w / 2 + 0.15), base + n * sn * 0.3 + right * (w / 2 + 0.15)
                r0, r1 = base - right * (w / 2 + 0.15), base + right * (w / 2 + 0.15)
                _quad(mesh, wood, [[*e0, zr], [*e1, zr], [*r1, zr + 0.22], [*r0, zr + 0.22]], [(0, 0), (1, 0), (1, 1), (0, 1)],
                      [n[0] * sn, n[1] * sn, 1])
            for sx in (-1, 1):
                pp = base + right * sx * (w / 2 - 0.06)
                _box(col, "collision", pp, n, 0.1, sg["z"] - 0.4, sg["z"] + 1.75)
            continue
        w, h = plate_size(text, kind, sub)
        front = material(_slug(text + (sub or ""), kind), render(text, kind, sub=sub))
        back = material(_slug(text, "end"), render(text, kind, end=True)) if kind == "locality" else POST
        z0 = sg["z"] + (1.5 if kind == "locality" else 1.6)
        posts = [base] if w < 1.3 else [base - right * w * 0.3, base + right * w * 0.3]
        for pp in posts:
            _post(mesh, col, pp, sg["z"] - 0.4, z0 + h + 0.02)
        q = base + n * 0.05                                 # plate just in front of the posts
        bl, br = q - right * w / 2, q + right * w / 2
        v = [[*bl, z0], [*br, z0], [*br, z0 + h], [*bl, z0 + h]]
        _quad(mesh, front, v, [(0, 0), (1, 0), (1, 1), (0, 1)], [n[0], n[1], 0])
        qb = base + n * 0.03
        bl2, br2 = qb + right * w / 2, qb - right * w / 2     # seen from behind: mirrored
        v = [[*bl2, z0], [*br2, z0], [*br2, z0 + h], [*bl2, z0 + h]]
        _quad(mesh, back, v, [(0, 0), (1, 0), (1, 1), (0, 1)], [-n[0], -n[1], 0])
    (shape_dir / "name_signs.dae").write_text(collada_lods("name_signs", [(1, mesh)], col, "textures", "dds"))
    (shape_dir / "main.materials.json").write_text(json.dumps(mats, indent=1))
    kinds = {}
    for s_ in signs:
        kinds[s_["kind"]] = kinds.get(s_["kind"], 0) + 1
    print(f"Name signs: {len(signs)} {kinds}, {len(mats)} plate textures")


def _post(mesh, col, p, z0, z1, r=0.035, sides=8):
    ang = np.linspace(0, 2 * np.pi, sides + 1)
    ring = p + r * np.column_stack([np.cos(ang), np.sin(ang)])
    for i in range(sides):
        a, b = ring[i], ring[i + 1]
        out = (a + b) / 2 - p
        _quad(mesh, POST, [[*a, z0], [*b, z0], [*b, z1], [*a, z1]], [(0, 0), (0.1, 0), (0.1, 1), (0, 1)], [out[0], out[1], 0])
    _quad(mesh, POST, [[*ring[0], z1], [*ring[2], z1], [*ring[4], z1], [*ring[6], z1]], [(0, 0), (1, 0), (1, 1), (0, 1)], [0, 0, 1])
    # collision: a thin box
    bx = [p + np.array(o) for o in ((-r, -r), (r, -r), (r, r), (-r, r))]
    for i in range(4):
        a, b = bx[i], bx[(i + 1) % 4]
        out = (a + b) / 2 - p
        _quad(col, "collision", [[*a, z0], [*b, z0], [*b, z1], [*a, z1]], [(0, 0)] * 4, [out[0], out[1], 0])


def _box(mesh, mat, p, n, size, z0, z1):
    right = np.array([-n[1], n[0]])
    c = [p + n * sn * size / 2 + right * sr * size / 2 for sn, sr in ((1, -1), (1, 1), (-1, 1), (-1, -1))]
    for i in range(4):
        a, b = c[i], c[(i + 1) % 4]
        o = (a + b) / 2 - p
        _quad(mesh, mat, [[*a, z0], [*b, z0], [*b, z1], [*a, z1]], [(0, 0), (0.2, 0), (0.2, 1), (0, 1)], [o[0], o[1], 0])
