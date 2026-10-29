#!/usr/bin/env python3
"""Survey strips from the Street View cache (../streetview, made by the studio's
tools/streetview_fetch.py): three stops per image, each a row LEFT | AHEAD | RIGHT of the driving
direction, captioned with the distance along the route and the capture month. Compact sheets for
reading the roadside along the whole road; the full sheets are streetview/views/ (studio's
tools/streetview_views.py).

    sv_strips.py [--step 2]      every `step`-th panorama of the index (2 = about every 100 m)
"""

import argparse
import json
import sys

import numpy as np
from PIL import Image, ImageDraw

from common import ROOT

# the studio's tools/ (two levels up from a build, one from the studio's pipeline/)
sys.path.insert(0, str(next(d / "tools" for d in ROOT.parents if (d / "tools" / "streetview_views.py").exists())))
import streetview_views as sv  # noqa: E402

W, H, BAR = 520, 330, 22


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, default=2)
    a = ap.parse_args()
    root = ROOT / "streetview"
    panos = [p for p in json.loads((root / "index.json").read_text())["panos"] if p.get("files")][::a.step]
    out = root / "strips"
    out.mkdir(exist_ok=True)
    for k in range(0, len(panos), 3):
        group = panos[k:k + 3]
        img = Image.new("RGB", (W * 3, (H + BAR) * len(group)), (20, 20, 20))
        for r, p in enumerate(group):
            pano = np.asarray(Image.open(root / p["files"]["stm"]).convert("RGB"), dtype=np.float32)
            rel = p["route_bearing_deg"] - p["heading_deg"]
            for c, yaw in enumerate((-90, 0, 90)):
                img.paste(Image.fromarray(sv.view(pano, rel + yaw, -8.0, 100.0, W, H)), (c * W, r * (H + BAR) + BAR))
            ImageDraw.Draw(img).text((6, r * (H + BAR) + 3), f"{p['at_m']:.0f} m   {p.get('date')}   LEFT | AHEAD | RIGHT",
                                     fill=(255, 235, 60), font=sv.font(15))
        name = f"{round(group[0]['at_m']):05d}-{round(group[-1]['at_m']):05d}m.jpg"
        img.save(out / name, quality=85)
        print(name)


if __name__ == "__main__":
    main()
