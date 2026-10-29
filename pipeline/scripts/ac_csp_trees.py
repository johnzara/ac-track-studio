#!/usr/bin/env python3
"""Real 3D trees near the road, for Custom Shaders Patch (CSP >= 0.1.79 "[TREES]": instanced
models with levels of detail and automatic billboards far away).

Models: output/ac/csp_trees/<variant>.npz from ac_csp_tree_models.py (Blender): decimated wood
and foliage clusters of the CC0 Poly Haven trees. Each becomes a CSP tree file <variant>.bin, in
the layout Content Manager's own converter writes (AcTools.ExtraKn5Utils AcTreeModelConverter.cs):
a zip of tree.ini, one <variant>_lod<n>.mesh per level of detail and the textures.

Trees: output/ac/csp_trees/instances.json from ac_trees.py: the trees within the brief's 3D
radius of the road whose variant has a model and whose crown, narrowed if need be, stays
TREE_CLEAR off the asphalt. ac_trees.py puts the cards of exactly these trees into meshes named
trees_cspfb_* / treetops_cspfb_*, which the CSP config hides: vanilla AC shows the cards, CSP the
3D trees.

Output: output/ac/csp_trees/extension/trees/*.bin, trees.txt and ext_trees.ini (appended to the
track's ext_config.ini by export_ac.py).

Not checked in the game: the file format follows the converter's source, but no tree made here
has been loaded by CSP yet. If the trees near the road are missing in game, delete the [TREES]
and [MESH_ADJUSTMENT_0] sections from extension/ext_config.ini: the cards come back.
"""

import io
import json
import struct
import zipfile

import numpy as np
from PIL import Image

import dds
from common import OUTPUT

SRC = OUTPUT / "ac" / "csp_trees"
EXT = SRC / "extension" / "trees"
NOMINAL_H = 10.0          # every model is stored 10 m tall; the list scales each tree
rng = np.random.default_rng(11)


def xxh32(data, seed=0):
    P1, P2, P3, P4, P5, M = 2654435761, 2246822519, 3266489917, 668265263, 374761393, 0xFFFFFFFF
    rotl = lambda x, r: ((x << r) | (x >> (32 - r))) & M
    n, i = len(data), 0
    if n >= 16:
        v = [(seed + P1 + P2) & M, (seed + P2) & M, seed, (seed - P1) & M]
        while i <= n - 16:
            for k in range(4):
                v[k] = (rotl((v[k] + struct.unpack_from("<I", data, i)[0] * P2) & M, 13) * P1) & M
                i += 4
        h = (rotl(v[0], 1) + rotl(v[1], 7) + rotl(v[2], 12) + rotl(v[3], 18)) & M
    else:
        h = (seed + P5) & M
    h = (h + n) & M
    while i <= n - 4:
        h = (rotl((h + struct.unpack_from("<I", data, i)[0] * P3) & M, 17) * P4) & M
        i += 4
    while i < n:
        h = (rotl((h + data[i] * P5) & M, 11) * P1) & M
        i += 1
    h ^= h >> 15
    h = (h * P2) & M
    h ^= h >> 13
    h = (h * P3) & M
    h ^= h >> 16
    return h or 1


def to_tree(p):
    """Blender (Z up) -> the tree file's frame (Y up), a rotation: (x, y, z) -> (x, z, -y)."""
    p = np.asarray(p, np.float64)
    return np.stack([p[..., 0], p[..., 2], -p[..., 1]], -1)


def weld(pos, nrm, uv):
    """Per-corner arrays -> unique vertices + indices."""
    key = np.round(np.concatenate([pos, nrm, uv], 1), 5)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    return pos[first], nrm[first], uv[first], inv.reshape(-1, 3)


def wood_mesh(d, lod):
    tri = d[f"wood{lod}_tri"]
    pos = to_tree(d[f"wood{lod}_pos"][tri].reshape(-1, 3))
    nrm = to_tree(d[f"wood{lod}_nrm"][tri].reshape(-1, 3))
    uv = d[f"wood{lod}_uv"].reshape(-1, 2).astype(np.float64)
    uv = np.stack([uv[:, 0], 1 - uv[:, 1]], 1)
    return weld(pos, nrm, uv)


def leaf_mesh(d, lod, meta, crown):
    """Two crossed square cards per foliage cluster, each with a random cluster picture."""
    cen, axes, std = d[f"leaf{lod}_cen"], d[f"leaf{lod}_axes"], d[f"leaf{lod}_std"]
    half = meta["card_std"] * std[:, 0]
    cols = 4
    rows = meta["templates"] // cols
    P, N, U, T = [], [], [], []
    for k in range(len(cen)):
        cell = rng.integers(meta["templates"])
        r, c = divmod(int(cell), cols)
        u0, u1, v0, v1 = c / cols, (c + 1) / cols, r / rows, (r + 1) / rows
        inset = 0.004
        u0, u1, v0, v1 = u0 + inset, u1 - inset, v0 + inset, v1 - inset
        out = cen[k] - crown
        out = out / max(np.linalg.norm(out), 1e-6)
        n = out * 0.75 + np.array([0, 0, 0.66])          # canopy-like shading: outwards and up
        n /= np.linalg.norm(n)
        for up in (axes[k, 1], axes[k, 2]):
            a0 = axes[k, 0]
            b = len(P)
            for sx, sy, uu, vv in ((-1, 1, u0, v0), (1, 1, u1, v0), (1, -1, u1, v1), (-1, -1, u0, v1)):
                P.append(cen[k] + half[k] * (sx * a0 + sy * up))
                N.append(n)
                U.append((uu, vv))
            T += [(b, b + 1, b + 2), (b, b + 2, b + 3)]
    return to_tree(np.array(P)), to_tree(np.array(N)), np.array(U), np.array(T)


def tangents(pos, uv, tri, nrm):
    t = np.zeros_like(pos)
    p0, p1, p2 = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
    d1, d2 = uv[tri[:, 1]] - uv[tri[:, 0]], uv[tri[:, 2]] - uv[tri[:, 0]]
    det = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    det = np.where(np.abs(det) < 1e-12, 1e-12, det)
    tt = ((p1 - p0) * d2[:, 1:2] - (p2 - p0) * d1[:, 1:2]) / det[:, None]
    for k in range(3):
        np.add.at(t, tri[:, k], tt)
    t -= nrm * (t * nrm).sum(1, keepdims=True)
    ln = np.linalg.norm(t, axis=1, keepdims=True)
    return np.where(ln > 1e-9, t / np.maximum(ln, 1e-9), np.array([1.0, 0, 0]))


def submesh(tex_d, tex_n, pos, nrm, uv, tri):
    """One mesh of a .mesh file. Both windings of every triangle: seen whichever way CSP culls."""
    assert len(pos) <= 65535, len(pos)
    tan = tangents(pos, uv, tri, nrm)
    idx = np.concatenate([tri, tri[:, ::-1]]).astype("<u2")
    out = [struct.pack("<ii", tex_d, tex_n), struct.pack("<i", idx.size), idx.tobytes(), struct.pack("<i", len(pos))]
    v = np.zeros(len(pos), dtype=[("p", "<f4", 3), ("n", "<f4", 3), ("uv", "<f4", 2), ("t", "u1", 3), ("el", "u1"),
                                  ("uv2", "<f2", 2), ("ao0", "u1"), ("ao1", "u1"), ("wet", "u1"), ("extra", "u1")])
    v["p"], v["n"], v["uv"] = pos, nrm, uv
    v["t"] = np.clip(tan * 127.5 + 127.5, 0, 255).astype(np.uint8)
    v["ao0"] = v["ao1"] = v["wet"] = 255
    assert v.dtype.itemsize == 44
    out.append(v.tobytes())
    return b"".join(out)


def string(s):
    b = s.encode("utf-8")
    return struct.pack("<i", len(b)) + b


def dds_bytes(img, fmt):
    tmp = SRC / "_tmp.dds"
    dds.write(img, tmp, fmt)
    data = tmp.read_bytes()
    tmp.unlink()
    return data


def build_model(variant, meta):
    d = np.load(SRC / f"{variant}.npz")
    scale = NOMINAL_H / float(d["height"])
    wood_v = d["wood0_pos"]
    crown = np.array([0.0, 0.0, 0.6 * float(d["height"])])
    lods = []
    for lod in range(3):
        wp, wn, wu, wt = wood_mesh(d, lod)
        lp, ln, lu, lt = leaf_mesh(d, lod, meta, crown)
        lods.append(((wp * scale, wn, wu, wt), (lp * scale, ln, lu, lt)))
    allp = np.concatenate([np.concatenate([w[0], l[0]]) for w, l in lods])
    maxdim = float(max(np.abs(allp[:, 0]).max(), np.abs(allp[:, 2]).max()))
    height = float(allp[:, 1].max())
    norm = lambda p: np.stack([p[:, 0] / maxdim, p[:, 1] / height, p[:, 2] / maxdim], 1)

    bark = Image.open(meta["bark_diff"]).convert("RGB").resize((512, 512), Image.LANCZOS)
    bark_n = Image.open(meta["bark_nor"]).convert("RGB").resize((512, 512), Image.LANCZOS)
    atlas = Image.fromarray(d["atlas"], "RGBA")
    # colour under the transparent pixels: the mean leaf colour, so mipmaps do not get dark fringes
    a = np.asarray(atlas).astype(np.float32)
    mean = a[a[..., 3] > 128][:, :3].mean(0)
    a[..., :3] = np.where(a[..., 3:4] > 8, a[..., :3], mean)
    atlas = Image.fromarray(a.astype(np.uint8), "RGBA")
    flat = Image.new("RGBA", (16, 16), (128, 128, 255, 255))
    textures = [(f"{variant}_bark.dds", dds_bytes(bark, "DXT1")),
                (f"{variant}_bark_nm.dds", dds_bytes(Image.merge("RGBA", (*bark_n.split(), Image.new("L", bark_n.size, 255))), "DXT5")),
                (f"{variant}_leaves.dds", dds_bytes(atlas, "DXT5")),
                (f"{variant}_leaves_nm.dds", dds_bytes(flat, "DXT5"))]
    sums = [xxh32(t[1]) for t in textures]

    files, tris = {}, []
    for lod, (w, l) in enumerate(lods):
        buf = [struct.pack("<iBi", 1, 0, len(textures))]
        for (name, _), s in zip(textures, sums):
            buf.append(struct.pack("<I", s) + string(name))
        wpn = norm(w[0])
        buf.append(submesh(0, 1, wpn, w[1], w[2], w[3]))
        buf.append(submesh(2, 3, norm(l[0]), l[1], l[2], l[3]))
        base = wpn[(wpn[:, 1] < 0.002) & (np.abs(wpn[:, 0]) < 0.2) & (np.abs(wpn[:, 2]) < 0.2)]
        if len(base):
            trunk = float(min(base[:, 0].max(), base[:, 2].max(), -base[:, 0].min(), -base[:, 2].min()))
            if np.isfinite(trunk) and trunk > 0:
                buf.append(struct.pack("<iif", -1, 4, trunk))
        files[f"{variant}_lod{lod}.mesh"] = b"".join(buf)
        tris.append(2 * (len(w[3]) + len(l[3])))
    max_d = height * 10
    ini = [f"[BASIC]\nHEIGHT={height:.4f}\nWIDTH={2 * maxdim:.4f}\n",
           "[SHADING]\nSPECULAR=1.0\nSUBSCATTERING=1.0\nREFLECTIVITY=1.0\nBRIGHTNESS=1.0\nUSE_AO_CHANNEL=0\n",
           "[NORMALS]\nBOOST=0.9\nSPHERE=1.0\n", "[LEAVES]\nMULT=20.0\nOFFSET=0.0\n", "[FAKE_SHADOW]\nSIZE=1.0\nOPACITY=1.0\n"]
    for lod in range(3):
        ini.append(f"[LOD_{lod}]\nMODEL={variant}_lod{lod}.mesh\nDISTANCE={max_d * (lod + 1) / 3:.2f}\n")
    EXT.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(EXT / f"{variant}.bin", "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
        z.writestr("tree.ini", "\n".join(ini))
        for name, data in textures:
            z.writestr(name, data)
    size = (EXT / f"{variant}.bin").stat().st_size
    print(f"  {variant}.bin: {tris[0]:,} / {tris[1]:,} / {tris[2]:,} triangles, crown {2 * maxdim:.1f} m wide at {height:.0f} m, {size / 1e6:.2f} MB")
    return {"radius_at_nominal": maxdim, "height": height, "tris": tris}


def read_back(path):
    """Parse a .bin the way the format is written (a self-check of the writer)."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        for n in [n for n in names if n.endswith(".mesh")]:
            b = io.BytesIO(z.read(n))
            ver, flag, ntex = struct.unpack("<iBi", b.read(9))
            assert (ver, flag) == (1, 0)
            for _ in range(ntex):
                b.read(4)
                ln, = struct.unpack("<i", b.read(4))
                assert b.read(ln).decode() in names
            meshes = 0
            while True:
                head = b.read(8)
                if len(head) < 8:
                    break
                td, tn = struct.unpack("<ii", head)
                if td == -1 and tn == 4:
                    b.read(4)
                    break
                ni, = struct.unpack("<i", b.read(4))
                idx = np.frombuffer(b.read(ni * 2), "<u2")
                nv, = struct.unpack("<i", b.read(4))
                b.read(nv * 44)
                assert idx.max() < nv and ni % 3 == 0
                meshes += 1
            assert meshes == 2 and b.read() == b"", n
    return True


def main():
    models = json.loads((SRC / "models.json").read_text())
    print("CSP tree models:")
    info = {v: build_model(v, m) for v, m in models.items() if (SRC / f"{v}.npz").exists()}
    for v in info:
        read_back(EXT / f"{v}.bin")
    inst_path = SRC / "instances.json"
    if not inst_path.exists():
        print("no instances.json yet: run ac_trees.py with assetto_corsa.csp_trees on")
        return
    inst = json.loads(inst_path.read_text())
    lines = ["; 3D trees near the road (ac_csp_trees.py). Positions are AC coordinates (x, y up, z).",
             "configure: size variance = 1, 1", "configure: width variance = 1, 1",
             "configure: angle variance = 0, 0", ""]
    n = 0
    for t in inst["trees"]:
        if t["variant"] not in info:
            continue
        size = t["height_m"] / info[t["variant"]]["height"]
        lines.append(f"tree: {t['variant']}.bin; pos = {t['x']:.2f}, {t['z']:.2f}, {-t['y']:.2f}; "
                     f"angle = {t['angle_deg']:.0f}; size = {size:.3f}; width = {t['width']:.2f}")
        n += 1
    (EXT / "trees.txt").write_text("\n".join(lines) + "\n")
    (SRC / "ext_trees.ini").write_text(
        "\n; 3D trees near the road (CSP >= 0.1.79). The kn5 holds cards for the same trees in meshes\n"
        "; named trees_cspfb_* / treetops_cspfb_*: hidden here, shown by vanilla AC. If no trees appear near the road,\n"
        "; delete the [TREES] and [MESH_ADJUSTMENT_0] sections below and the cards come back.\n"
        "[TREES]\nLIST_0 = trees/trees.txt\n\n"
        "[MESH_ADJUSTMENT_0]\nMESHES = trees_cspfb_?, treetops_cspfb_?\nIS_RENDERABLE = 0\n")
    total = sum(f.stat().st_size for f in EXT.glob("*.bin"))
    print(f"ac_csp_trees: {n:,} trees in trees.txt, {len(info)} models, {total / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
