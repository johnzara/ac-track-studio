#!/usr/bin/env python3
"""Particular places described in config.json "sites".

* military_base: an abandoned military site at the top of a climb.
  Its concrete aprons and yards are traced from the orthophoto (light, unsaturated pixels
  around the base) -> output/paved_areas.geojson, which build_terrain.py paints with the
  asphalt terrain layer. sites.build() makes (for ac_scenery.py): a chain-link perimeter fence
  with outward-leaning barbed wire around the buildings and aprons (gaps where roads pass), and
  red/white boom barriers at the gates. build_buildings.py gives the base's buildings the
  "military" style (weathered cream render, flat roofs).
* crosses: summit/wayside crosses (painted steel or timber) on a plinth, optionally with
  rows of benches facing them (a summit cross where concerts are held).

    sites.py        trace the paved areas (run after build_osm.py, before build_terrain.py)
"""

import json
import math

import numpy as np
import rasterio
import shapely
from rasterio.features import shapes
from scipy import ndimage
from shapely.geometry import LineString, Point, Polygon, shape
from shapely.ops import unary_union

from buildings_mesh import TexMesh
from common import DATA, OUTPUT, Config, Heightfield, road_widths


def _site(cfg, key):
    return cfg.raw.get("sites", {}).get(key)


# ------------------------------------------------------------------ plan: paved areas

def paved_areas(cfg=None):
    cfg = cfg or Config()
    base = _site(cfg, "military_base")
    feats = []
    if base and (DATA / "ortho" / f"{base['ortho']}.tif").exists():
        cx, cy = base["centre"]
        r = base["radius"]
        with rasterio.open(DATA / "ortho" / f"{base['ortho']}.tif") as src:
            win = rasterio.windows.from_bounds(cx - r + cfg.center_e, cy - r + cfg.center_n,
                                               cx + r + cfg.center_e, cy + r + cfg.center_n, src.transform)
            img = src.read(window=win).astype(np.float32).transpose(1, 2, 0)
            tr = src.window_transform(win)
        mx, mn = img.max(-1), img.min(-1)
        sat = (mx - mn) / np.maximum(mx, 1)
        val = img.mean(-1)
        green = (img[..., 1] > img[..., 0] + 6) & (img[..., 1] > img[..., 2] + 6)
        mask = (sat < 0.16) & (val > 125) & ~green
        res = abs(tr.a)
        mask = ndimage.binary_opening(mask, iterations=int(round(0.6 / res)))
        mask = ndimage.binary_closing(mask, iterations=int(round(1.0 / res)))
        mask = ndimage.binary_fill_holes(mask)
        lab, n = ndimage.label(mask)
        sizes = ndimage.sum(mask, lab, range(1, n + 1)) * res * res
        keep = np.isin(lab, np.where(sizes >= 40)[0] + 1)
        polys = [shape(g) for g, v in shapes(keep.astype(np.uint8), mask=keep, transform=tr) if v == 1]
        polys = [shapely.affinity.translate(p, -cfg.center_e, -cfg.center_n).simplify(0.5) for p in polys]
        circle = Point(cx, cy).buffer(r)
        area = unary_union(polys).intersection(circle)
        for p in getattr(area, "geoms", [area]):
            if p.area >= 40:
                feats.append({"type": "Feature", "geometry": shapely.geometry.mapping(p),
                              "properties": {"class": "concrete", "site": "military_base"}})
        print(f"Military base: {len(feats)} paved areas, {sum(shape(f['geometry']).area for f in feats):.0f} m2 "
              f"traced from the orthophoto")
    (OUTPUT / "paved_areas.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    return feats


def base_polygon(cfg=None):
    """The base's fenced area: buildings, aprons and internal roads, padded."""
    cfg = cfg or Config()
    base = _site(cfg, "military_base")
    if not base:
        return None
    cx, cy = base["centre"]
    circle = Point(cx, cy).buffer(base["radius"])
    parts = []
    path = OUTPUT / "paved_areas.geojson"
    if path.exists():
        parts += [shape(f["geometry"]) for f in json.loads(path.read_text())["features"]]
    raw = json.loads((DATA / "osm" / "raw.json").read_text())["elements"]
    for e in raw:
        t = e.get("tags", {})
        if e["type"] == "way" and t.get("building") and "geometry" in e:
            x, y = cfg.lonlat_to_local([p["lon"] for p in e["geometry"]], [p["lat"] for p in e["geometry"]])
            poly = Polygon(np.column_stack([x, y]))
            if poly.is_valid and poly.intersects(circle):
                parts.append(poly)
    if not parts:
        return circle
    u = unary_union(parts).intersection(circle)
    hull = shapely.concave_hull(u, ratio=0.35)
    u = hull.buffer(6.0).buffer(-1.0)
    if u.geom_type == "MultiPolygon":
        u = max(u.geoms, key=lambda g: g.area)
    return Polygon(u.exterior).simplify(1.5)


# ------------------------------------------------------------------ meshes

def _quad(mesh, mat, v, uv, want=None):
    if want is not None:
        n = np.cross(np.subtract(v[1], v[0]), np.subtract(v[3], v[0]))
        if np.dot(n, want) < 0:
            v, uv = v[::-1], uv[::-1]
    mesh.quad(mat, v, uv)


def _box(mesh, mat, c, size, yaw=0.0, uv_scale=1.0):
    """Oriented box: centre c (x, y, z), size (along yaw, across, height)."""
    ca, sa = math.cos(yaw), math.sin(yaw)
    ax, ay = np.array([ca, sa, 0.0]), np.array([-sa, ca, 0.0])
    az = np.array([0, 0, 1.0])
    c = np.asarray(c, float)
    hx, hy, hz = (s / 2 for s in size)
    P = lambda i, j, k: c + ax * i * hx + ay * j * hy + az * k * hz  # noqa: E731
    faces = [(ax, [P(1, -1, -1), P(1, 1, -1), P(1, 1, 1), P(1, -1, 1)], size[1]),
             (-ax, [P(-1, 1, -1), P(-1, -1, -1), P(-1, -1, 1), P(-1, 1, 1)], size[1]),
             (ay, [P(1, 1, -1), P(-1, 1, -1), P(-1, 1, 1), P(1, 1, 1)], size[0]),
             (-ay, [P(-1, -1, -1), P(1, -1, -1), P(1, -1, 1), P(-1, -1, 1)], size[0]),
             (az, [P(-1, -1, 1), P(1, -1, 1), P(1, 1, 1), P(-1, 1, 1)], size[0])]
    for n, q, w in faces:
        h = size[2] if n[2] == 0 else size[1]
        _quad(mesh, mat, [tuple(p) for p in q], [(0, 0), (w * uv_scale, 0), (w * uv_scale, h * uv_scale), (0, h * uv_scale)], n)


def _fence(mesh, cfg, hf, roads):
    poly = base_polygon(cfg)
    if poly is None:
        return 0
    ring = LineString(poly.exterior.coords)
    # gaps where roads cross the perimeter
    gaps = []
    for f in roads:
        line = LineString(np.asarray(f["geometry"]["coordinates"])[:, :2])
        inter = line.intersection(ring)
        for p in getattr(inter, "geoms", [inter]):
            if p.geom_type == "Point":
                gaps.append((ring.project(p), road_widths(f).max() / 2 + 1.5))
    L = ring.length
    s = np.arange(0, L, 3.0)
    keep = np.array([all(abs(((si - g + L / 2) % L) - L / 2) > hw for g, hw in gaps) for si in s])
    pts = np.array([[ring.interpolate(si).x, ring.interpolate(si).y] for si in s])
    z = hf.sample(pts[:, 0], pts[:, 1])
    centroid = np.array(poly.centroid.coords[0])
    posts = 0
    for i in range(len(s)):
        j = (i + 1) % len(s)
        if not keep[i]:
            continue
        p, zp = pts[i], z[i]
        out = p - centroid
        out /= np.linalg.norm(out) or 1
        _box(mesh, "concrete", (p[0], p[1], zp + 1.05), (0.14, 0.14, 2.7))
        tip = p + out * 0.4
        mesh.quad("metal_dark", [(p[0], p[1], zp + 2.3), (p[0], p[1], zp + 2.36), (tip[0], tip[1], zp + 2.76), (tip[0], tip[1], zp + 2.7)],
                  [(0, 0), (1, 0), (1, 1), (0, 1)])
        posts += 1
        if keep[j] and (j != 0 or s[-1] + 3.0 >= L - 0.01):
            q, zq = pts[j], z[j]
            outq = q - centroid
            outq /= np.linalg.norm(outq) or 1
            mesh.quad("fence_chainlink_alpha", [(p[0], p[1], zp - 0.05), (q[0], q[1], zq - 0.05), (q[0], q[1], zq + 2.2), (p[0], p[1], zp + 2.2)],
                      [(0, 0), (3.0 / 2.2, 0), (3.0 / 2.2, 1), (0, 1)])
            a2, b2 = p + out * 0.4, q + outq * 0.4
            mesh.quad("fence_barbed_alpha", [(p[0], p[1], zp + 2.25), (q[0], q[1], zq + 2.25), (b2[0], b2[1], zq + 2.72), (a2[0], a2[1], zp + 2.72)],
                      [(0, 0), (3.0 / 2.0, 0), (3.0 / 2.0, 1), (0, 1)])
    return posts


def _barrier(mesh, cfg, hf, roads, gate):
    p = Point(*gate)
    f = min(roads, key=lambda f_: LineString(np.asarray(f_["geometry"]["coordinates"])[:, :2]).distance(p))
    line = LineString(np.asarray(f["geometry"]["coordinates"])[:, :2])
    s = line.project(p)
    a, b = line.interpolate(max(s - 1, 0)), line.interpolate(min(s + 1, line.length))
    t = np.array([b.x - a.x, b.y - a.y])
    t /= np.linalg.norm(t)
    n = np.array([-t[1], t[0]])
    c = np.array([line.interpolate(s).x, line.interpolate(s).y])
    w = road_widths(f).max()
    post = c + n * (w / 2 + 0.6)
    z = float(hf.sample(np.array([post[0]]), np.array([post[1]]))[0])
    _box(mesh, "concrete", (post[0], post[1], z + 0.5), (0.4, 0.4, 1.2))
    arm_c = post - n * (w / 2 + 0.6) / 2 - n * 0.3
    yaw = math.atan2(-n[1], -n[0])
    _box(mesh, "barrier_stripes", (arm_c[0], arm_c[1], z + 1.0), (w + 1.2, 0.09, 0.09), yaw, uv_scale=1 / 1.0)
    end = post - n * (w + 0.9)
    _box(mesh, "metal_dark", (end[0], end[1], z + 0.48), (0.06, 0.06, 0.96))


def _cross(mesh, hf, c):
    x, y = c["x"], c["y"]
    z = float(hf.sample(np.array([x]), np.array([y]))[0])
    fx, fy = c.get("face_towards", [x, y - 1])
    face = math.atan2(fy - y, fx - x)                     # direction the cross faces
    yaw = face + math.pi / 2                              # the arm runs across it
    steel = c["kind"] == "steel"
    mat = "cross_white" if steel else "timber_dark"
    sec = 0.28 if steel else 0.2
    plinth_mat, plinth = ("concrete", (1.4, 1.4, 0.7)) if steel else ("bld_stone_rubble", (1.0, 1.0, 0.8))
    _box(mesh, plinth_mat, (x, y, z + plinth[2] / 2 - 0.25), plinth, yaw)
    if steel:
        _box(mesh, "concrete", (x, y, z + 0.5), (0.8, 0.8, 0.3), yaw)
    top = z + plinth[2] - 0.25
    h, arm = c["height"], c["arm"]
    _box(mesh, mat, (x, y, top + h / 2), (sec, sec, h), yaw)
    _box(mesh, mat, (x, y, top + h * 0.72), (arm, sec, sec), yaw)
    b = c.get("benches")
    if b:
        d = np.array([math.cos(face), math.sin(face)])
        across = np.array([-d[1], d[0]])
        for r in range(b["rows"]):
            for k in range(b["per_row"]):
                off = (k - (b["per_row"] - 1) / 2) * (b["length"] + 0.8)
                p = np.array([x, y]) + d * (b["first"] + r * b["spacing"]) + across * off
                zb = float(hf.sample(np.array([p[0]]), np.array([p[1]]))[0])
                _box(mesh, "bench_wood", (p[0], p[1], zb + 0.45), (b["length"], 0.38, 0.06), yaw)
                # backrest on the far side: people sit facing the cross
                _box(mesh, "bench_wood", (p[0] + d[0] * 0.2, p[1] + d[1] * 0.2, zb + 0.75), (b["length"], 0.05, 0.25), yaw)
                for e in (-1, 1):
                    q = p + across * e * (b["length"] / 2 - 0.2)
                    _box(mesh, "concrete", (q[0], q[1], zb + 0.2), (0.08, 0.36, 0.5), yaw)


def build(cfg=None):
    """TexMesh with the base fence and barriers and the crosses (absolute coordinates)."""
    cfg = cfg or Config()
    hf = Heightfield(cfg, OUTPUT / "dem_final.tif")
    roads = json.loads((OUTPUT / "roads_local.geojson").read_text())["features"]
    mesh = TexMesh()
    base = _site(cfg, "military_base")
    if base:
        posts = _fence(mesh, cfg, hf, roads)
        for g in base.get("barrier_gates", []):
            _barrier(mesh, cfg, hf, roads, g)
        print(f"Military base: perimeter fence with {posts} posts, {len(base.get('barrier_gates', []))} barrier(s)")
    for c in cfg.raw.get("sites", {}).get("crosses", []):
        _cross(mesh, hf, c)
    return mesh


def clear_polygons(cfg=None):
    """Areas kept free of generated vegetation: around crosses and their benches."""
    cfg = cfg or Config()
    out = []
    for c in cfg.raw.get("sites", {}).get("crosses", []):
        r = 4.0 + (c["benches"]["first"] + c["benches"]["rows"] * c["benches"]["spacing"] if c.get("benches") else 0)
        out.append(Point(c["x"], c["y"]).buffer(r))
    return out


def military_area(cfg=None):
    cfg = cfg or Config()
    base = _site(cfg, "military_base")
    return Point(*base["centre"]).buffer(base["radius"]) if base else None


if __name__ == "__main__":
    paved_areas()
