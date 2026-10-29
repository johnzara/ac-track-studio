#!/usr/bin/env python3
"""Cut survey views and top-down road patches out of the Street View cache.

    PY tools/streetview_views.py SLUG [options]      builds/SLUG/streetview/ (from streetview_fetch.py)
    PY tools/streetview_views.py --dir DIR [options]

    --from M, --to M     only the panoramas between these metres along the route
    --no-views, --no-ground
    --camera-height M    height of the camera above the road (default 2.0, see below)
    --force              redo files that already exist

PY needs numpy, scipy and Pillow: the build's own .venv/bin/python (linked by tools/scaffold.py),
or the reference pipeline's.

For every panorama in index.json:

  views/<at_m>m.jpg    the survey sheet: ahead, back, left and right of the driving direction
                       (100 degrees wide each, looking slightly down), captioned with the distance
                       along the route, the panorama id and the capture month. "Left" and "right"
                       are the survey's sides. The views are in the car's frame, not levelled: on
                       a steep grade the verticals lean in the side views.
  ground/<at_m>m.png   the road from above, rectified from the panorama onto the road plane:
                       16 m across, 30 m along, 2 cm per pixel, driving direction up, the camera
                       in the middle. Transparent where Google blurred or painted out its own car
                       (found per panorama: 1.8 m around the camera on some cars, a blob up to 4 m
                       on others).
  ground/index.json    where each patch is: its centre (the panorama's position), the bearing of
                       "up", metres per pixel. That is enough to resample the patches into the
                       road's own (across, along) texture space once the build has its centreline.

The patch assumes the road is a plane under the car (true for the carriageway, wrong for banks,
walls and anything standing up, which smear outwards) and the stated camera height: a 5 % error in
the height scales the patch by 5 %. The panorama is in the car's frame, so this holds on slopes
too. It is sharp to about 6 m from the camera and soft beyond: good for markings, patches, cracks
and colour, not for the asphalt's grain. The default 2.0 m was measured on 2023 and 2026 imagery in
Veneto by matching the road surface between panoramas 10 m apart (best match at 1.95-2.0 m for
both cars); check it against a known width when a track uses another generation of imagery.

Google imagery: private to the user's own tracks (see streetview_fetch.py). A build that bakes
these pixels into textures must say so in its README and must never be shared.
"""

import argparse
import json
import math
import sys
from pathlib import Path

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    from scipy import ndimage as ndi
except ImportError:
    sys.exit("numpy, scipy and Pillow are needed: run this with the build's .venv/bin/python "
             "(or the studio's pipeline/.venv/bin/python)")

STUDIO = Path(__file__).resolve().parent.parent
Image.MAX_IMAGE_PIXELS = None

VIEW_W, VIEW_H, VIEW_FOV, VIEW_PITCH = 800, 500, 100.0, -6.0
GROUND_ACROSS_M, GROUND_ALONG_M, GROUND_M_PX = 16.0, 30.0, 0.02
BLUR_MIN_M, BLUR_MAX_M, BLUR_DETAIL = 1.5, 6.0, 1.6


def sample(pano, x, y, z):
    """Colours of the panorama in the directions (x right, y ahead, z up of the car), bilinear."""
    h, w = pano.shape[:2]
    lon = np.arctan2(x, y)
    lat = np.arctan2(z, np.hypot(x, y))
    u = (lon / (2 * np.pi) + 0.5) * w - 0.5
    v = np.clip((0.5 - lat / np.pi) * h - 0.5, 0, h - 1)
    u0, v0 = np.floor(u).astype(np.int64), np.floor(v).astype(np.int64)
    fu, fv = (u - u0)[..., None], (v - v0)[..., None]
    u1, v1 = (u0 + 1) % w, np.minimum(v0 + 1, h - 1)
    u0 %= w
    top = pano[v0, u0] * (1 - fu) + pano[v0, u1] * fu
    bottom = pano[v1, u0] * (1 - fu) + pano[v1, u1] * fu
    return (top * (1 - fv) + bottom * fv + 0.5).astype(np.uint8)


def view(pano, yaw_deg, pitch_deg=VIEW_PITCH, fov_deg=VIEW_FOV, w=VIEW_W, h=VIEW_H):
    """A normal photo looking yaw_deg clockwise from the panorama's centre."""
    f = (w / 2) / math.tan(math.radians(fov_deg) / 2)
    i, j = np.meshgrid(np.arange(w) - w / 2 + 0.5, np.arange(h) - h / 2 + 0.5)
    cx, cy, cz = i / f, np.ones_like(i), -j / f
    p, a = math.radians(pitch_deg), math.radians(yaw_deg)
    y1, z1 = cy * math.cos(p) - cz * math.sin(p), cy * math.sin(p) + cz * math.cos(p)
    return sample(pano, cx * math.cos(a) + y1 * math.sin(a), -cx * math.sin(a) + y1 * math.cos(a), z1)


def ground(pano, yaw_deg, cam_h):
    """The road plane from above, `yaw_deg` (the driving direction) up. RGBA."""
    w, h = round(GROUND_ACROSS_M / GROUND_M_PX), round(GROUND_ALONG_M / GROUND_M_PX)
    gx, gy = np.meshgrid((np.arange(w) - w / 2 + 0.5) * GROUND_M_PX, (h / 2 - np.arange(h) - 0.5) * GROUND_M_PX)
    a = math.radians(yaw_deg)
    rgb = sample(pano, gx * math.cos(a) + gy * math.sin(a), -gx * math.sin(a) + gy * math.cos(a), np.full_like(gx, -cam_h))
    blur = car_blur(rgb)
    return np.dstack([rgb, np.where(blur, 0, 255).astype(np.uint8)]), float(blur.sum()) * GROUND_M_PX ** 2


def car_blur(rgb):
    """Where Google blurred or painted out its own car: the featureless area around the camera
    (no fine detail within BLUR_MAX_M, connected to the middle), and always BLUR_MIN_M around it."""
    k = 4
    g = rgb.astype(np.float32).mean(axis=2)
    h, w = g.shape[0] // k * k, g.shape[1] // k * k
    small = g[:h, :w].reshape(h // k, k, w // k, k).mean(axis=(1, 3))
    fine = small - ndi.gaussian_filter(small, 2.0)
    detail = np.sqrt(ndi.uniform_filter(fine * fine, 7))
    cell = GROUND_M_PX * k
    yy, xx = np.mgrid[:small.shape[0], :small.shape[1]]
    r = np.hypot((xx - small.shape[1] / 2 + 0.5) * cell, (yy - small.shape[0] / 2 + 0.5) * cell)
    flat = (detail < BLUR_DETAIL) & (r < BLUR_MAX_M)
    labels, _ = ndi.label(flat)
    core = np.unique(labels[(r < BLUR_MIN_M) & flat])
    mask = np.isin(labels, core[core > 0]) | (r < BLUR_MIN_M)
    mask = ndi.binary_fill_holes(ndi.binary_closing(mask, iterations=3))
    mask = ndi.binary_dilation(mask, iterations=round(0.3 / cell))
    full = np.zeros(g.shape, bool)
    full[:h, :w] = np.kron(mask, np.ones((k, k), bool))
    return full


def font(size):
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def sheet(pano, p, rel):
    """Ahead | back over left | right, with a caption."""
    bar = 34
    img = Image.new("RGB", (VIEW_W * 2, VIEW_H * 2 + bar), (20, 20, 20))
    for n, (label, yaw) in enumerate((("AHEAD", 0), ("BACK", 180), ("LEFT", -90), ("RIGHT", 90))):
        tile = Image.fromarray(view(pano, rel + yaw))
        d = ImageDraw.Draw(tile)
        d.rectangle((0, 0, 86, 26), fill=(0, 0, 0))
        d.text((8, 3), label, fill=(255, 235, 60), font=font(18))
        img.paste(tile, ((n % 2) * VIEW_W, bar + (n // 2) * VIEW_H))
    text = (f"{p['at_m']:.0f} m along the route   {p.get('date') or 'date unknown'}   pano {p['pano']}   "
            f"{p['lat']:.6f}, {p['lon']:.6f}   route bearing {p['route_bearing_deg']:.0f}°   "
            f"{abs(p.get('lateral_m', 0)):.1f} m {'right' if p.get('lateral_m', 0) >= 0 else 'left'} of the route line")
    ImageDraw.Draw(img).text((10, 7), text, fill=(235, 235, 235), font=font(17))
    return img


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug", nargs="?")
    ap.add_argument("--dir", type=Path)
    ap.add_argument("--from", dest="from_m", type=float, default=-math.inf)
    ap.add_argument("--to", dest="to_m", type=float, default=math.inf)
    ap.add_argument("--no-views", action="store_true")
    ap.add_argument("--no-ground", action="store_true")
    ap.add_argument("--camera-height", type=float, default=2.0)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if not (a.slug or a.dir):
        ap.error("give a SLUG or --dir")
    root = a.dir or STUDIO / "builds" / a.slug / "streetview"
    if not (root / "index.json").exists():
        sys.exit(f"{root / 'index.json'} not found: run tools/streetview_fetch.py first")
    index = json.loads((root / "index.json").read_text())
    panos = [p for p in index["panos"] if p.get("files") and a.from_m <= p["at_m"] <= a.to_m]
    (root / "views").mkdir(exist_ok=True)
    (root / "ground").mkdir(exist_ok=True)
    gi_path = root / "ground" / "index.json"
    patches = {g["pano"]: g for g in json.loads(gi_path.read_text())["patches"]} if gi_path.exists() else {}

    made = 0
    for n, p in enumerate(panos, 1):
        name = f"{round(p['at_m']):05d}m"
        vf, gf = root / "views" / f"{name}.jpg", root / "ground" / f"{name}.png"
        need_v = not a.no_views and (a.force or not vf.exists())
        need_g = not a.no_ground and (a.force or not gf.exists() or p["pano"] not in patches)
        if not (need_v or need_g):
            continue
        if p.get("heading_deg") is None:
            print(f"\n  {name}: no heading for {p['pano']}, skipped", file=sys.stderr)
            continue
        pano = np.asarray(Image.open(root / p["files"]["stm"]).convert("RGB"), dtype=np.float32)
        rel = p["route_bearing_deg"] - p["heading_deg"]  # the driving direction, from the panorama's centre
        if need_v:
            sheet(pano, p, rel).save(vf, quality=90)
        if need_g:
            rgba, blur_m2 = ground(pano, rel, a.camera_height)
            Image.fromarray(rgba, "RGBA").save(gf)
            patches[p["pano"]] = {"file": gf.name, "pano": p["pano"], "at_m": p["at_m"], "date": p.get("date"),
                                  "center_lat": p["lat"], "center_lon": p["lon"], "up_bearing_deg": p["route_bearing_deg"],
                                  "m_per_px": GROUND_M_PX, "across_m": GROUND_ACROSS_M, "along_m": GROUND_ALONG_M,
                                  "camera_height_m": a.camera_height, "blurred_m2": round(blur_m2, 1)}
        made += 1
        print(f"\r  {n}/{len(panos)}", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    if not a.no_ground:
        gi_path.write_text(json.dumps({
            "schema": "ac-track-studio/streetview-ground@1",
            "notice": index.get("notice", ""),
            "frame": "pixel (0, 0) is the patch's top-left; up is up_bearing_deg (the route's direction at the "
                     "panorama), right is 90 degrees clockwise of it; the centre pixel is the panorama's position",
            "patches": sorted(patches.values(), key=lambda g: g["at_m"]),
        }, indent=1) + "\n")
    print(f"{root}: {made} panorama(s) processed, {len(panos)} in range")


if __name__ == "__main__":
    main()
