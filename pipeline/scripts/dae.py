"""Minimal Collada (.dae) reader for the pipeline's shapes (landmarks, signs, extras): world-space
triangles with UVs per material, honouring node matrices, up axis and unit. LOD conventions: geometry under nodes
named like "...LOD<n>" / with a trailing detail size ("name_a400") is grouped by detail size, and
collision / helper nodes ("Colmesh", "Collision", "bounds", "nulldetail", "start01") are skipped.
"""

import re
import xml.etree.ElementTree as ET
from functools import lru_cache

import numpy as np

NS = "{http://www.collada.org/2005/11/COLLADASchema}"
SKIP = re.compile(r"(colmesh|collision|^col-|^bounds|nulldetail|shadow|occluder|^los|^raycast)", re.I)
DETAIL = re.compile(r"(?:LOD|_a|_)(\d+)$", re.I)


def _floats(el):
    return np.array(el.text.split(), dtype=np.float64) if el is not None and el.text else np.zeros(0)


class Mesh:
    def __init__(self):
        self.parts = {}   # (material, detail) -> (tris (n,3,3), uvs (n,3,2))

    def add(self, mat, detail, tris, uvs):
        key = (mat, detail)
        if key in self.parts:
            t, u = self.parts[key]
            self.parts[key] = (np.vstack([t, tris]), np.vstack([u, uvs]))
        else:
            self.parts[key] = (tris, uvs)

    def details(self):
        return sorted({d for _, d in self.parts}, reverse=True)

    def lod(self, detail=None):
        """{material: (tris, uvs)} for the highest (or the given) detail level."""
        ds = self.details()
        if not ds:
            return {}
        d = ds[0] if detail is None else detail
        return {m: v for (m, dd), v in self.parts.items() if dd == d}

    def bounds(self):
        tris = [t for (t, _) in self.lod().values()]
        if not tris:
            return None
        p = np.vstack(tris).reshape(-1, 3)
        return p.min(0), p.max(0)


def _sources(mesh_el):
    out = {}
    for src in mesh_el.findall(f"{NS}source"):
        fa = src.find(f"{NS}float_array")
        acc = src.find(f".//{NS}accessor")
        stride = int(acc.get("stride", 1)) if acc is not None else 1
        arr = _floats(fa)
        if stride and len(arr) % stride == 0:
            out["#" + src.get("id")] = arr.reshape(-1, stride)
    verts = mesh_el.find(f"{NS}vertices")
    if verts is not None:
        for inp in verts.findall(f"{NS}input"):
            if inp.get("semantic") == "POSITION":
                out["#" + verts.get("id")] = out.get(inp.get("source"))
    return out


def _prims(mesh_el, sources):
    for prim in list(mesh_el):
        tag = prim.tag.replace(NS, "")
        if tag not in ("triangles", "polylist", "polygons"):
            continue
        inputs = prim.findall(f"{NS}input")
        stride = max(int(i.get("offset", 0)) for i in inputs) + 1
        pos_off = uv_off = None
        pos_src = uv_src = None
        for i in inputs:
            sem = i.get("semantic")
            if sem == "VERTEX":
                pos_off, pos_src = int(i.get("offset", 0)), sources.get(i.get("source"))
            elif sem == "TEXCOORD" and (uv_off is None or i.get("set", "0") == "0"):
                uv_off, uv_src = int(i.get("offset", 0)), sources.get(i.get("source"))
        if pos_src is None:
            continue
        if tag == "polygons":
            idx_lists = [np.array(p.text.split(), int) for p in prim.findall(f"{NS}p") if p.text]
            counts = [len(p) // stride for p in idx_lists]
            idx = np.concatenate(idx_lists) if idx_lists else np.zeros(0, int)
        else:
            p = prim.find(f"{NS}p")
            if p is None or not p.text:
                continue
            idx = np.array(p.text.split(), dtype=np.int64)
            if tag == "polylist":
                counts = [int(c) for c in prim.find(f"{NS}vcount").text.split()]
            else:
                counts = [3] * (len(idx) // (3 * stride))
        idx = idx.reshape(-1, stride)
        tris_p, tris_t = [], []
        start = 0
        for c in counts:
            poly = idx[start:start + c]
            start += c
            for k in range(1, c - 1):  # fan triangulation
                tris_p.append(poly[[0, k, k + 1], pos_off])
                if uv_off is not None:
                    tris_t.append(poly[[0, k, k + 1], uv_off])
        if not tris_p:
            continue
        P = pos_src[np.array(tris_p)][..., :3]
        if uv_src is not None and tris_t:
            U = uv_src[np.array(tris_t)][..., :2]
        else:
            U = np.zeros(P.shape[:2] + (2,))
        yield prim.get("material", ""), P, U


def _node_matrix(node):
    m = np.eye(4)
    for el in list(node):
        tag = el.tag.replace(NS, "")
        v = _floats(el)
        if tag == "matrix" and len(v) == 16:
            m = m @ v.reshape(4, 4)
        elif tag == "translate" and len(v) == 3:
            t = np.eye(4); t[:3, 3] = v; m = m @ t
        elif tag == "scale" and len(v) == 3:
            m = m @ np.diag([*v, 1])
        elif tag == "rotate" and len(v) == 4:
            ax = v[:3] / (np.linalg.norm(v[:3]) or 1); a = np.radians(v[3])
            c, s = np.cos(a), np.sin(a); x, y, z = ax
            r = np.array([[c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
                          [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
                          [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)]])
            R = np.eye(4); R[:3, :3] = r; m = m @ R
    return m


@lru_cache(maxsize=256)
def load(path):
    root = ET.parse(path).getroot()
    asset = root.find(f"{NS}asset")
    unit = 1.0
    up = "Z_UP"
    if asset is not None:
        u = asset.find(f"{NS}unit")
        if u is not None:
            unit = float(u.get("meter", 1))
        ua = asset.find(f"{NS}up_axis")
        if ua is not None and ua.text:
            up = ua.text.strip()
    fix = np.eye(4)
    if up == "Y_UP":
        fix = np.array([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], float)
    fix = np.diag([unit, unit, unit, 1]) @ fix

    geoms = {}
    for g in root.iter(f"{NS}geometry"):
        me = g.find(f"{NS}mesh")
        if me is not None:
            src = _sources(me)
            geoms["#" + g.get("id")] = list(_prims(me, src))
    matbind = {}
    mesh = Mesh()

    def walk(node, parent, detail, skip):
        name = node.get("name") or node.get("id") or ""
        m = parent @ _node_matrix(node)
        if SKIP.search(name):
            skip = True
        dm = DETAIL.search(name)
        if dm:
            detail = int(dm.group(1))
        for ig in node.findall(f"{NS}instance_geometry"):
            if skip:
                continue
            binds = {im.get("symbol"): im.get("target", "").lstrip("#")
                     for im in ig.iter(f"{NS}instance_material")}
            for mat, P, U in geoms.get(ig.get("url"), []):
                Ph = np.concatenate([P, np.ones(P.shape[:2] + (1,))], axis=-1)
                W = (Ph @ m.T)[..., :3]
                mesh.add(binds.get(mat, mat), detail, W, U)
        for child in node.findall(f"{NS}node"):
            walk(child, m, detail, skip)

    for vs in root.iter(f"{NS}visual_scene"):
        for n in vs.findall(f"{NS}node"):
            walk(n, fix, 0, False)
    # material id -> name
    names = {m.get("id"): m.get("name") or m.get("id") for m in root.iter(f"{NS}material")}
    renamed = Mesh()
    for (mat, d), (t, u) in mesh.parts.items():
        renamed.add(names.get(mat, mat), d, t, u)
    return renamed
