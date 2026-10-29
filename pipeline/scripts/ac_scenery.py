#!/usr/bin/env python3
"""Buildings, landmarks and signs for the Assetto Corsa track (local frame, Z up).

Buildings
  Every footprint of output/buildings_local.geojson through the pipeline's building generator
  (buildings_mesh.add_block): full detail within NEAR_M of the climb, the simple version beyond.
  Textures: output/textures/<material>.png (ours, full size) with the
  generator's normal maps (<material>_n.png, ksPerPixelNM). One mesh per material per TILE_M tile. Within COLLIDE_M of the climb each block also gets an invisible
  collision shell (WALL).
Landmarks and signs
  The extras of landmarks_climb.py (the finish gate of the ex base, the telecom mast, wayside
  shrines) and the name signs from output/scenery/ (scenery_inputs.py: climb_landmarks.dae,
  name_signs.dae) with their own DDS textures; the hand-built landmark models
  (output/landmarks/<id>.dae from landmarks.py: churches, houses)
  at their placements, with a collision prism on their footprint, replacing the
  generated blocks under them; the base's perimeter fence (sites.py).
UVs: the generator and the .dae files use v up; AC wants v down (v -> 1 - v).
Output: output/ac/scenery.npz + scenery.json.
"""

import json
import math
from collections import defaultdict

import numpy as np
from PIL import Image
import shapely
import shapely.affinity
import shapely.geometry
from shapely.geometry import shape

import dae
from ac_track import CROWN, Road, save_part
from ac_trees import terrain_height
from audit_ac import BARY as BARY_S
from buildings_mesh import TexMesh, add_block, collision_block
from common import OUTPUT, Config, landmark_footprint_blocks

NEAR_M = 400.0
ROAD_GAP, ROAD_GAP_MAX = 0.3, 2.0   # m kept between the AC asphalt and a building; larger overlaps are left (and audited)
COLLIDE_M = 120.0
TILE_M = 2048.0
END_KEEP = 2.0               # m before the end of the road: nothing is cleared (the base gate)
SHAPES = OUTPUT / "scenery"           # extras and signs (scenery_inputs.py: landmarks_climb.py, signs.py)
BUILDING_SPEC = {"ksAmbient": 0.5, "ksDiffuse": 0.75, "ksSpecular": 0.05, "ksSpecularEXP": 10.0}


def _tile(xy):
    return tuple(np.floor(np.asarray(xy) / TILE_M).astype(int))


def texmesh_to_meshes(tm, prefix, material_of=lambda m: m, surface=None, renderable=True):
    """One AC mesh per material; positions world-space, v flipped."""
    P = np.asarray(tm.pos, float)
    N = np.asarray(tm.nrm, float)
    U = np.asarray(tm.uv, float)
    out = []
    for mat, tris in tm.tris.items():
        t = np.asarray(tris, np.int64)
        keep, inv = np.unique(t.ravel(), return_inverse=True)
        uv = U[keep].copy()
        uv[:, 1] = 1.0 - uv[:, 1]
        m = dict(name=f"{prefix}_{material_of(mat)}", material=material_of(mat), surface=surface,
                 pos=P[keep], nrm=N[keep], uv=uv, tri=inv.reshape(-1, 3))
        if not renderable:
            m["renderable"] = False
            m["cast_shadows"] = False
        out.append(m)
    return out


def buildings(road):
    feats = json.loads((OUTPUT / "buildings_local.geojson").read_text())["features"]
    tiles = defaultdict(lambda: (TexMesh(), TexMesh()))           # tile -> (visual, collision)
    origin = np.zeros(2)
    n_near = n_far = n_col = 0
    n_seated = n_moved = 0
    road_line = shapely.geometry.LineString(road.xy)
    road_reach = float(road.hw.max()) + ROAD_GAP + 0.5
    skip = landmark_footprint_blocks(feats)
    for k, f in enumerate(feats):
        if k in skip:
            continue
        g = shape(f["geometry"])
        if not g.is_empty and g.distance(road_line) < road_reach:
            # a block standing on the AC asphalt (the measured road is wider there than the topo
            # database's gap between the houses): moved away from the road, ROAD_GAP clear of it
            hull = g.minimum_rotated_rectangle if f["properties"].get("style") == "tower" else g   # towers are built squared off
            ring = np.vstack([np.asarray(q.exterior.segmentize(0.5).coords)[:, :2]
                              for q in (hull.geoms if hull.geom_type != "Polygon" else [hull])])
            lat, st = road.locate(ring)
            over = np.where(st >= 0, road.hw[np.maximum(st, 0)] + ROAD_GAP - np.abs(lat), -1.0)
            j = int(np.argmax(over))
            if 0 < over[j] <= ROAD_GAP_MAX:
                move = road.n[st[j]] * np.sign(lat[j]) * over[j]
                shift = lambda q: shapely.geometry.mapping(shapely.affinity.translate(shape(q), float(move[0]), float(move[1])))
                g = shapely.affinity.translate(g, float(move[0]), float(move[1]))
                props = dict(f["properties"])
                if props.get("members"):
                    props["members"] = [dict(m_, geometry=shift(m_["geometry"])) for m_ in props["members"]]
                f = dict(f, geometry=shapely.geometry.mapping(g), properties=props)
                n_moved += 1
        if not g.is_empty:                     # foundations down to the AC terrain (coarser than the DEM)
            xy = np.vstack([np.asarray(q.exterior.coords)[:, :2] for q in (g.geoms if g.geom_type != "Polygon" else [g])])
            low = float(terrain_height(xy[:, 0], xy[:, 1]).min()) - 0.3
            if low < f["properties"]["base_z"]:
                f = dict(f, properties=dict(f["properties"], base_z=low))
                n_seated += 1
        c = g.centroid
        d, _ = road.tree.query([c.x, c.y])
        vis, col = tiles[_tile([c.x, c.y])]
        if d <= NEAR_M:
            add_block(vis, f, origin)
            n_near += 1
        else:
            add_block(vis, f, origin, lod="low")
            n_far += 1
        if d <= COLLIDE_M:
            collision_block(col, f, origin)
            n_col += 1
    meshes, used = [], set()
    for (i, j), (vis, col) in sorted(tiles.items()):
        meshes += texmesh_to_meshes(vis, f"bld_{i}_{j}")
        used.update(vis.tris)
        if col.pos:
            meshes += texmesh_to_meshes(col, f"1WALL_bldcol_{i}_{j}", lambda m: "collision", "WALL", renderable=False)
    materials = {}
    nm_dir = OUTPUT / "ac" / "textures" / "bld_nm"
    nm_dir.mkdir(parents=True, exist_ok=True)
    for m in sorted(used):
        alpha = m.endswith("_alpha")
        spec = {"shader": "ksPerPixelAT" if alpha else "ksPerPixel", "alpha_tested": alpha,
                "textures": [["txDiffuse", str(OUTPUT / "textures" / f"{m}.png"), "DXT5" if alpha else "DXT1"]],
                "props": BUILDING_SPEC | ({"ksAlphaRef": 0.4} if alpha else {}), "max_px": 2048}
        src = OUTPUT / "textures" / f"{m}_n.png"
        if src.exists() and not alpha:
            # the generator's normal maps are OpenGL (green up); facades run u to the viewer's right with
            # v up, so in AC's encoding (B = N x T = up, green = 0.5 - 0.5 * up) green is flipped
            dst = nm_dir / f"{m}_nm.png"
            if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
                a = np.asarray(Image.open(src).convert("RGB")).copy()
                a[..., 1] = 255 - a[..., 1]
                Image.fromarray(a).save(dst)
            spec["shader"] = "ksPerPixelNM"
            spec["textures"].append({"sampler": "txNormal", "file": str(dst), "fmt": "DXT1", "max_px": 1024})
        materials[m] = spec
    materials["collision"] = {"shader": "ksPerPixel", "textures": [["txDiffuse", "steel_dark.png", "DXT1"]], "props": BUILDING_SPEC}
    print(f"buildings: {n_near} detailed, {n_far} simple, {n_col} with collision; {len(used)} materials; "
          f"{n_seated} foundations extended down to the terrain; {len(skip)} under hand-built landmarks; "
          f"{n_moved} moved off the asphalt edge")
    return meshes, materials


def hand_landmarks():
    """The hand-built landmark models (landmarks.py): rotated by yaw about their origin and moved
    to their position (output/landmarks/placements.json); a collision prism on each footprint."""
    path = OUTPUT / "landmarks" / "placements.json"
    if not path.exists():
        return [], {}
    meshes, materials = [], {}
    for lm in json.loads(path.read_text()):
        c, s_ = math.cos(lm["yaw"]), math.sin(lm["yaw"])
        R = np.array([[c, -s_, 0], [s_, c, 0], [0, 0, 1.0]])
        parts = dae.load(str(OUTPUT / "landmarks" / lm["shape"])).lod()
        for mat, (tris, uvs) in parts.items():
            P = tris.reshape(-1, 3) @ R.T + np.asarray(lm["position"])
            uv = uvs.reshape(-1, 2).copy()
            uv[:, 1] = 1.0 - uv[:, 1]
            tri = np.arange(len(P)).reshape(-1, 3)
            fn = np.cross(P[tri[:, 1]] - P[tri[:, 0]], P[tri[:, 2]] - P[tri[:, 0]])
            ok = np.linalg.norm(fn, axis=1) > 1e-10
            tri = tri[ok]
            fn = fn[ok] / np.linalg.norm(fn[ok], axis=1, keepdims=True)
            nrm = np.zeros_like(P)
            nrm[tri.ravel()] = np.repeat(fn, 3, 0)
            name = f"lm_{lm['id']}_{mat}"
            meshes.append(dict(name=name, material=name, surface=None, pos=P, nrm=nrm, uv=uv, tri=tri))
            tex = OUTPUT / "landmarks" / "textures" / f"{mat}.png"
            if not tex.exists():
                tex = next((t for t in (OUTPUT / "landmarks" / "textures").glob(f"{mat}*.png")), None)
            spec = BUILDING_SPEC | ({"ksSpecular": 0.4, "ksSpecularEXP": 60.0} if "glass" in mat else {})
            materials[name] = {"shader": "ksPerPixel", "textures": [["txDiffuse", str(tex), "DXT1"]], "props": spec}
        foot = np.asarray(lm["footprint"], float)[:, :2]
        z0 = float(lm["position"][2]) - 1.0
        tm = TexMesh()
        ring = foot if np.allclose(foot[0], foot[-1]) else np.vstack([foot, foot[:1]])
        for a, b in zip(ring[:-1], ring[1:]):
            tm.quad("collision", [[*a, z0], [*b, z0], [*b, z0 + 9.0], [*a, z0 + 9.0]], [(0, 0), (1, 0), (1, 1), (0, 1)])
            tm.quad("collision", [[*b, z0], [*a, z0], [*a, z0 + 9.0], [*b, z0 + 9.0]], [(0, 0), (1, 0), (1, 1), (0, 1)])
        meshes += texmesh_to_meshes(tm, f"1WALL_lmcol_{lm['id']}", lambda m: "collision", "WALL", renderable=False)
    print(f"hand-built landmarks: {len(meshes)} meshes")
    return meshes, materials


def site_meshes(road):
    """sites.py: the perimeter fence of the ex base (chain-link, barbed wire), crosses; ours textures."""
    import sites
    tm = sites.build(Config())
    if not tm.pos:
        return [], {}
    meshes = texmesh_to_meshes(tm, "site")
    materials = {}
    for m in tm.tris:
        alpha = m.endswith("_alpha")
        materials[m] = {"shader": "ksPerPixelAT" if alpha else "ksPerPixel", "alpha_tested": alpha,
                        "textures": [["txDiffuse", str(OUTPUT / "textures" / f"{m}.png"), "DXT5" if alpha else "DXT1"]],
                        "props": BUILDING_SPEC | ({"ksAlphaRef": 0.4} if alpha else {}), "max_px": 1024}
    print(f"sites: {len(meshes)} meshes ({', '.join(sorted(tm.tris))})")
    return meshes, materials


def landmarks():
    """climb_landmarks.dae and name_signs.dae (world coordinates, placed at the origin)."""
    meshes, materials = [], {}
    for shape_name in ("climb_landmarks", "name_signs"):
        folder = SHAPES / shape_name
        if not (folder / "main.materials.json").exists():      # a track without name signs / extras
            continue
        defs = json.loads((folder / "main.materials.json").read_text())
        parts = dae.load(str(folder / f"{shape_name}.dae")).lod()
        for mat, (tris, uvs) in parts.items():
            P = tris.reshape(-1, 3)
            uv = uvs.reshape(-1, 2).copy()
            uv[:, 1] = 1.0 - uv[:, 1]
            tri = np.arange(len(P)).reshape(-1, 3)
            fn = np.cross(P[tri[:, 1]] - P[tri[:, 0]], P[tri[:, 2]] - P[tri[:, 0]])
            ok = np.linalg.norm(fn, axis=1) > 1e-10
            tri = tri[ok]
            fn = fn[ok] / np.linalg.norm(fn[ok], axis=1, keepdims=True)
            nrm = np.zeros_like(P)
            nrm[tri.ravel()] = np.repeat(fn, 3, 0)
            name = f"{shape_name}_{mat}"
            meshes.append(dict(name=name, material=name, surface=None, pos=P, nrm=nrm, uv=uv, tri=tri))
            colour = (defs.get(mat, {}).get("Stages") or [{}])[0].get("colorMap", "")
            tex = folder / "textures" / colour.split("/")[-1] if colour else None
            if tex is not None and tex.exists():
                spec = ["txDiffuse", str(tex), "DDS"]
            elif (OUTPUT / "textures" / f"{mat}.png").exists():   # one of our building textures (metal_dark…)
                spec = ["txDiffuse", str(OUTPUT / "textures" / f"{mat}.png"), "DXT1"]
            else:
                raise FileNotFoundError(f"{shape_name}: no colour map for {mat}")
            materials[name] = {"shader": "ksPerPixel", "textures": [spec], "props": BUILDING_SPEC}
    col = OUTPUT / "climb_landmarks_collision.npz"
    if col.exists():                              # the gate, booth, walls, mast: solid (WALL)
        c = np.load(col)
        meshes.append(dict(name="1WALL_lmcol", material="collision", surface="WALL", pos=c["pos"],
                           nrm=np.tile([0.0, 0.0, 1.0], (len(c["pos"]), 1)), uv=np.zeros((len(c["pos"]), 2)),
                           tri=c["tri"], renderable=False, cast_shadows=False))
        materials["collision"] = {"shader": "ksPerPixel", "textures": [["txDiffuse", "steel_dark.png", "DXT1"]],
                                  "props": BUILDING_SPEC}
    print(f"landmarks + signs: {len(meshes)} meshes")
    return meshes, materials


def clear_road(meshes, road, reach=15.0, clear=0.3, small=5.0):
    """Nothing visible over the asphalt (|lateral| < half-width + 0.2 m, 4 cm .. 3 m above it):
    small objects (signs, posts: under `small` m across) are moved whole onto the verge, `clear` m
    past the edge, and re-seated on the terrain; of larger ones (landmark fences, building
    details) the triangles reaching over the road are removed."""
    tri_ref, P_all = [], []
    for mi, m in enumerate(meshes):
        if m.get("renderable") is False:
            continue
        d, _ = road.tree.query(m["pos"][:, :2])
        sel = np.flatnonzero(d[m["tri"]].min(1) < reach)
        if len(sel):
            tri_ref.append(np.stack([np.full(len(sel), mi), sel], 1))
    if not tri_ref:
        return 0, 0
    tri_ref = np.concatenate(tri_ref)
    corners = np.stack([meshes[mi]["pos"][meshes[mi]["tri"][ti]] for mi, ti in tri_ref])      # (n, 3, 3)
    S = np.einsum("sk,nkd->nsd", BARY_S, corners)
    lat, st = road.locate(S.reshape(-1, 3)[:, :2])
    # the closed gate at the top stands across the end of the road on purpose
    st = np.where((S.reshape(-1, 3)[:, :2] - road.xy[-1]) @ road.t[-1] > -END_KEEP, -1, st)
    j = np.where(st >= 0, st, 0)
    h = S.reshape(-1, 3)[:, 2] - (road.z[j] - CROWN * np.abs(lat))
    # hand-built landmarks (gates, fences surveyed to the centimetre) only where they are really over
    # the asphalt; everything else with a 0.2 m margin
    buf = np.repeat(np.array([0.0 if meshes[mi]["name"].startswith("lm_") else 0.2 for mi in tri_ref[:, 0]]), len(BARY_S))
    bad_s = ((st >= 0) & (np.abs(lat) < road.hw[j] + buf) & (h > 0.04) & (h < 3.0)).reshape(len(tri_ref), -1)
    bad = bad_s.any(1)
    if not bad.any():
        return 0, 0
    # objects: triangles whose (0.1 m grown) boxes share a 0.5 m cell
    lo, hi = corners.min(1)[:, :2] - 0.1, corners.max(1)[:, :2] + 0.1
    parent = np.arange(len(tri_ref))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    cells = {}
    for k in range(len(tri_ref)):
        for cx in range(int(np.floor(lo[k, 0] / 0.5)), int(np.floor(hi[k, 0] / 0.5)) + 1):
            for cy in range(int(np.floor(lo[k, 1] / 0.5)), int(np.floor(hi[k, 1] / 0.5)) + 1):
                o = cells.setdefault((cx, cy), k)
                ra, rb = find(o), find(k)
                if ra != rb:
                    parent[rb] = ra
    root = np.array([find(k) for k in range(len(tri_ref))])
    moved = cut = 0
    drop = defaultdict(set)
    for r_ in np.unique(root[bad]):
        members = np.flatnonzero(root == r_)
        pts = corners[members].reshape(-1, 3)
        extent = float(np.linalg.norm(pts[:, :2].max(0) - pts[:, :2].min(0)))
        if extent < small:
            ib = bad_s[members].reshape(-1)
            Sm = S[members].reshape(-1, 3)
            l_, s_ = road.locate(Sm[ib][:, :2])
            jj = np.where(s_ >= 0, s_, 0)
            c = pts[:, :2].mean(0)
            lc, sc = road.locate(c[None])
            side = 1.0 if lc[0] >= 0 else -1.0
            k0 = int(sc[0]) if sc[0] >= 0 else int(road.tree.query(c)[1])
            shift = float(np.max(road.hw[jj] + 0.2 + clear - np.abs(l_)))
            move = road.n[k0] * side * shift
            dz = float(terrain_height(c[0] + move[0], c[1] + move[1]) - terrain_height(c[0], c[1]))
            done = set()
            for mi, ti in tri_ref[members]:
                for vi in meshes[mi]["tri"][ti]:
                    if (mi, vi) not in done:
                        meshes[mi]["pos"][vi] += [move[0], move[1], dz]
                        done.add((mi, vi))
            moved += 1
        else:
            for mi, ti in tri_ref[members[bad[members]]]:
                drop[mi].add(int(ti))
                cut += 1
    for mi, tis in drop.items():
        keep = np.ones(len(meshes[mi]["tri"]), bool)
        keep[list(tis)] = False
        meshes[mi]["tri"] = meshes[mi]["tri"][keep]
    return moved, cut


def main():
    road = Road()
    m1, mat1 = buildings(road)
    m2, mat2 = landmarks()
    m3, mat3 = hand_landmarks()
    m4, mat4 = site_meshes(road)
    moved, cut = clear_road(m1 + m2 + m3 + m4, road)
    print(f"road cleared: {moved} small objects moved onto the verge, {cut} triangles of larger ones cut back")
    meta = save_part("scenery", [m for m in m1 + m2 + m3 + m4 if len(m["tri"])], mat1 | mat2 | mat3 | mat4)
    print(f"ac_scenery: {len(meta)} meshes, {sum(m['verts'] for m in meta):,} vertices, "
          f"{sum(m['tris'] for m in meta):,} triangles")


if __name__ == "__main__":
    main()
