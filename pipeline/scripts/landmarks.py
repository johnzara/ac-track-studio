#!/usr/bin/env python3
"""Turn hand-built landmark models (landmarks/glb/<id>.glb, exported from their three.js pages)
into Collada shapes (output/landmarks/, read by ac_scenery.py) and place them on their real
footprint.

  landmarks.py            convert + place everything in landmarks/landmarks.json

For each landmark:
  * Y-up glTF -> Z-up metres; the model's facade (+Z in three.js) becomes local -Y
  * materials: glTF base colour / texture -> PNG; registry overrides (e.g. stone stripes
    banded by height) are baked into a texture with height-based UVs
  * placement: centred on the OSM footprint, facade turned to `facade_azimuth` (snapped to
    the footprint's own axes if within 25 deg), standing on the lowest ground of its footprint;
    or, with "anchor": {"origin": [x, y, z]}, a model authored directly in map metres
    (three.js x = east - x, z = -(north - y), y = height - z) is placed exactly there
  * "footprint_exclude": materials ignored when measuring the footprint (fences, gates...)
Output: output/landmarks/<id>.dae + textures/, output/landmarks/placements.json
"""

import json
import math

import numpy as np
import trimesh
from PIL import Image
from shapely.geometry import MultiPoint, Polygon

from buildings_mesh import TexMesh, collada
from common import DATA, OUTPUT, ROOT, Config, Heightfield

REG = ROOT / "landmarks" / "landmarks.json"
OUT = OUTPUT / "landmarks"


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def stripe_texture(colours, path):
    a, b = (np.array(hex_rgb(c), np.uint8) for c in colours)
    im = np.zeros((256, 16, 3), np.uint8)
    im[:128], im[128:] = b, a          # v in [0, 0.5) -> colour a (bottom half of the image)
    rng = np.random.default_rng(1)
    im = np.clip(im.astype(int) + rng.integers(-6, 7, im.shape), 0, 255).astype(np.uint8)
    Image.fromarray(im).save(path)


def osm_footprint(cfg, way_id):
    raw = json.loads((DATA / "osm" / "raw.json").read_text())["elements"]
    el = next((e for e in raw if e["type"] == "way" and e["id"] == way_id), None)
    if el is None:
        raise SystemExit(f"OSM way {way_id} not in data/osm/raw.json")
    lon = [g["lon"] for g in el["geometry"]]
    lat = [g["lat"] for g in el["geometry"]]
    x, y = cfg.lonlat_to_local(lon, lat)
    return Polygon(np.column_stack([x, y]))


def load_glb(path):
    """{material: (triangles (n,3,3) Z-up, uvs (n,3,2), colour, image)}"""
    scene = trimesh.load(path, force="scene")
    parts = {}
    for node in scene.graph.nodes_geometry:
        tf, gname = scene.graph[node]
        g = scene.geometry[gname]
        if not isinstance(g, trimesh.Trimesh) or len(g.faces) == 0:
            continue
        v = trimesh.transform_points(g.vertices, tf)
        v = np.column_stack([v[:, 0], -v[:, 2], v[:, 1]])            # Y-up -> Z-up
        tris = v[g.faces]
        mat = getattr(g.visual, "material", None)
        name = getattr(mat, "name", None) or "default"
        uv = getattr(g.visual, "uv", None)
        uvs = uv[g.faces] if uv is not None and len(uv) == len(g.vertices) else np.zeros(tris.shape[:2] + (2,))
        colour = np.array(getattr(mat, "baseColorFactor", None) if getattr(mat, "baseColorFactor", None) is not None
                          else [255, 255, 255, 255], float)[:3]
        colour = colour / 255.0 if colour.max() > 1.0 else colour
        image = getattr(mat, "baseColorTexture", None)
        t, u, c, im = parts.get(name, (np.zeros((0, 3, 3)), np.zeros((0, 3, 2)), colour, image))
        parts[name] = (np.vstack([t, tris]), np.vstack([u, uvs]), c, im if im is not None else image)
    return parts


def build(lid, spec, cfg, hf):
    parts = load_glb(ROOT / "landmarks" / "glb" / f"{lid}.glb")
    for name in spec.get("exclude_materials", []):
        parts.pop(name, None)
    # drop flat ground-level sheets (forecourts, lawns): the real terrain replaces them
    zall = np.vstack([t.reshape(-1, 3) for t, _, _, _ in parts.values()])[:, 2].min()
    for name, (t, u, c, im) in list(parts.items()):
        flat = np.all(t[..., 2] < zall + 0.05, axis=1)
        if flat.any():
            parts[name] = (t[~flat], u[~flat], c, im)
    tex_dir = OUT / "textures"
    tex_dir.mkdir(parents=True, exist_ok=True)
    mesh = TexMesh()
    allpts = np.vstack([t.reshape(-1, 3) for t, _, _, _ in parts.values()])
    zmin = allpts[:, 2].min()
    for name, (tris, uvs, colour, image) in parts.items():
        mat = f"lm_{lid}_{name}"
        over = spec.get("materials", {}).get(name, {})
        if "stripes" in over:
            stripe_texture(over["stripes"], tex_dir / f"{mat}.png")
            per, off = over.get("period", 1.0), over.get("offset", 0.0)
            # world-height bands, horizontal coordinate along the wall (x or y, whichever varies)
            uvs = np.stack([(tris[..., 0] + tris[..., 1]) / 2.0, (tris[..., 2] - zmin - off) / per], -1)
        elif image is not None:
            image.convert("RGB").save(tex_dir / f"{mat}.png")
        else:
            c = (np.clip(colour, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8) if colour.max() <= 1 else colour
            Image.new("RGB", (8, 8), tuple(int(v) for v in c)).save(tex_dir / f"{mat}.png")
        for t, u in zip(tris, uvs):
            mesh.tri(mat, t, [tuple(x) for x in u])

    # placement
    skip = set(spec.get("footprint_exclude", []))
    body = np.vstack([t.reshape(-1, 3) for n_, (t, _, _, _) in parts.items() if n_ not in skip])
    if "anchor" in spec:
        if "utm" in spec["anchor"]:
            e, n_, oz = spec["anchor"]["utm"]
            ox, oy = e - cfg.center_e, n_ - cfg.center_n
        else:
            ox, oy, oz = spec["anchor"]["origin"]
        walls = body[(body[:, 2] > 0.3) & (body[:, 2] < 4.0)]
        placed = MultiPoint(walls[:, :2] + [ox, oy]).convex_hull
        ring = np.asarray(placed.exterior.coords)
        (OUT / f"{lid}.dae").write_text(collada(mesh, lid, "textures"))
        info = {"id": lid, "name": spec["name"], "shape": f"{lid}.dae", "position": [ox, oy, oz], "yaw": 0.0,
                "facade_azimuth": spec.get("facade_azimuth"), "footprint": [list(map(float, p)) for p in ring],
                "model_area": round(placed.area, 1), "materials": sorted(mesh.tris),
                "triangles": sum(len(t) for t in mesh.tris.values())}
        print(f"{lid}: {info['triangles']} tris, anchored at ({ox}, {oy}, {oz}), footprint {info['model_area']} m2")
        return info
    foot = osm_footprint(cfg, spec["osm_way"])
    walls = body[(body[:, 2] > zmin + 1.0) & (body[:, 2] < zmin + 4.0)]
    model_foot = MultiPoint(walls[:, :2]).convex_hull
    az = spec["facade_azimuth"]
    with np.errstate(all="ignore"):
        rect = np.asarray(foot.minimum_rotated_rectangle.exterior.coords)[:4]
    e = rect[1] - rect[0]
    for k in range(4):   # snap to footprint axes when close
        cand = (math.degrees(math.atan2(e[0], e[1])) + 90 * k) % 360
        if abs((cand - az + 180) % 360 - 180) < 25:
            az = cand
            break
    # local facade normal is -Y; rotate so it points to bearing az
    yaw = math.atan2(math.cos(math.radians(az)), math.sin(math.radians(az))) - math.atan2(-1, 0)
    c, s = math.cos(yaw), math.sin(yaw)
    R = np.array([[c, -s], [s, c]])
    mc = np.array(model_foot.centroid.coords[0])
    fc = np.array(foot.centroid.coords[0])
    pos_xy = fc - R @ mc
    placed = Polygon((R @ np.asarray(model_foot.exterior.coords).T).T + pos_xy)
    ring = np.asarray(placed.exterior.coords)
    ground = float(hf.sample(ring[:, 0], ring[:, 1]).min())
    (OUT / f"{lid}.dae").write_text(collada(mesh, lid, "textures"))
    info = {"id": lid, "name": spec["name"], "shape": f"{lid}.dae", "position": [float(pos_xy[0]), float(pos_xy[1]), ground - zmin - 0.1],
            "yaw": yaw, "facade_azimuth": round(az, 1), "footprint": [list(map(float, p)) for p in ring],
            "model_area": round(model_foot.area, 1), "osm_area": round(foot.area, 1),
            "overlap": round(placed.intersection(foot).area / foot.area, 2),
            "materials": sorted(mesh.tris), "triangles": sum(len(t) for t in mesh.tris.values())}
    print(f"{lid}: {info['triangles']} tris, facade -> {az:.0f} deg, model {info['model_area']} m2 vs "
          f"OSM {info['osm_area']} m2, overlap {info['overlap']:.0%}")
    return info


def main():
    cfg = Config()
    hf = Heightfield(cfg, OUTPUT / "dem_final.tif")
    reg = {k: v for k, v in json.loads(REG.read_text()).items() if not k.startswith("_")}
    OUT.mkdir(parents=True, exist_ok=True)
    placed = []
    for lid, spec in reg.items():
        if not (ROOT / "landmarks" / "glb" / f"{lid}.glb").exists():
            print(f"{lid}: no landmarks/glb/{lid}.glb yet, skipped")
            continue
        placed.append(build(lid, spec, cfg, hf))
    (OUT / "placements.json").write_text(json.dumps(placed, indent=1))


if __name__ == "__main__":
    main()
