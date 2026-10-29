#!/usr/bin/env python3
"""Side views of the CSP tree models (ac_csp_trees.py), drawn in software from exactly the meshes
that go into the .bin files: a check that wood, cards and their pictures fit together.

    ac_csp_tree_preview.py [variant ...]   -> output/ac/csp_trees/preview_<variant>.jpg (LOD 0 | 1 | 2)
"""

import json
import sys

import numpy as np
from PIL import Image, ImageDraw

import ac_csp_trees as T

PX = 420


def draw(d, meta, lod, yaw_deg):
    scale = T.NOMINAL_H / float(d["height"])
    crown = np.array([0.0, 0.0, 0.6 * float(d["height"])])
    wp, wn, wu, wt = T.wood_mesh(d, lod)
    lp, ln, lu, lt = T.leaf_mesh(d, lod, meta, crown)
    a = np.radians(yaw_deg)
    R = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
    wp, lp = wp * scale @ R.T, lp * scale @ R.T
    span = 16.0
    to_px = lambda p: np.stack([(p[:, 0] / span + 0.5) * PX, (1 - p[:, 1] / (span * 0.8)) * PX * 0.8 + PX * 0.1], 1)
    img = Image.new("RGBA", (PX, PX), (176, 200, 226, 255))
    dr = ImageDraw.Draw(img)
    w2 = to_px(wp)
    for t in wt[np.argsort(wp[wt].mean(1)[:, 2])]:
        dr.polygon([tuple(w2[i]) for i in t], fill=(92, 74, 58, 255))
    atlas = Image.fromarray(d["atlas"], "RGBA")
    l2 = to_px(lp)
    quads = lt.reshape(-1, 2, 3)[:, 0]                      # first triangle of each card: corners 0, 1, 2
    order = np.argsort(lp[quads].mean(1)[:, 2])
    for q in quads[order]:
        (x0, y0), (x1, y1), (x2, y2) = l2[q[0]], l2[q[1]], l2[q[2]]
        (u0, v0), (u1, v1), (u2, v2) = lu[q[0]] * [atlas.width, atlas.height], lu[q[1]] * [atlas.width, atlas.height], lu[q[2]] * [atlas.width, atlas.height]
        A = np.array([[x0, y0, 1], [x1, y1, 1], [x2, y2, 1]])
        if abs(np.linalg.det(A)) < 1e-3:
            continue
        cu, cv = np.linalg.solve(A, [u0, u1, u2]), np.linalg.solve(A, [v0, v1, v2])
        xs = [x0, x1, x2, x0 + x2 - x1]
        ys = [y0, y1, y2, y0 + y2 - y1]
        bx0, by0, bx1, by1 = int(max(0, min(xs))), int(max(0, min(ys))), int(min(PX, max(xs) + 1)), int(min(PX, max(ys) + 1))
        if bx1 <= bx0 or by1 <= by0:
            continue
        coef = (cu[0], cu[1], cu[2] + cu[0] * bx0 + cu[1] * by0, cv[0], cv[1], cv[2] + cv[0] * bx0 + cv[1] * by0)
        card = atlas.transform((bx1 - bx0, by1 - by0), Image.AFFINE, coef, resample=Image.BILINEAR)
        # keep to the card's own cell (the affine map runs on past it)
        m = Image.new("L", card.size, 0)
        ImageDraw.Draw(m).polygon([(x - bx0, y - by0) for x, y in zip(xs, ys)], fill=255)
        alpha = Image.composite(card.split()[3], Image.new("L", card.size, 0), m).point(lambda v: 255 if v > 115 else 0)
        img.paste(card.convert("RGB"), (bx0, by0), alpha)
    return img.convert("RGB")


def main():
    models = json.loads((T.SRC / "models.json").read_text())
    for v in (sys.argv[1:] or list(models)):
        d = np.load(T.SRC / f"{v}.npz")
        sheet = Image.new("RGB", (PX * 4, PX))
        for k, (lod, yaw) in enumerate(((0, 0), (0, 90), (1, 0), (2, 0))):
            sheet.paste(draw(d, models[v], lod, yaw), (k * PX, 0))
        sheet.save(T.SRC / f"preview_{v}.jpg", quality=88)
        print(T.SRC / f"preview_{v}.jpg")


if __name__ == "__main__":
    main()
