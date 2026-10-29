"""Assetto Corsa .kn5 writer (and a reader to check what was written).

Layout as read by Content Manager's AcTools Kn5Reader (little-endian; string = int32 length +
UTF-8 bytes; bool = 1 byte), version 5:

  "sc6969" int32 version
  int32 nTextures   { int32 active=1, string name, uint32 size, bytes (whole .dds / .png file) }
  int32 nMaterials  { string name, string shader, u8 blendMode, u8 alphaTested, int32 depthMode,
                      int32 nProps { string name, float A, float B[2], float C[3], float D[4] },
                      int32 nMaps  { string sampler, int32 slot, string texture } }
  node (depth-first, each node followed by its children):
    int32 class (1 base, 2 mesh), string name, int32 nChildren, u8 active,
    base: float[16] matrix (rows: X axis, Y axis, Z axis, translation; row-vector convention)
    mesh: u8 castShadows, u8 visible, u8 transparent, uint32 nVerts
          { float pos[3], normal[3], uv[2], tangent[3] }, uint32 nIdx, uint16 idx[],
          uint32 material, uint32 layer, float lodIn, float lodOut, float sphereCentre[3],
          float sphereRadius, u8 renderable

AC space: Y up, right-handed, metres; front faces counter-clockwise; UV v grows downward.
Mesh nodes must not have children and hold at most 65536 vertices.
"""

import struct
from dataclasses import dataclass, field

import numpy as np

COMMON = ("ksAmbient", "ksDiffuse", "ksSpecular", "ksSpecularEXP", "ksEmissive", "ksAlphaRef")


def _s(b, text):
    data = text.encode("utf-8")
    b += struct.pack("<i", len(data)) + data


@dataclass
class Material:
    name: str
    shader: str
    props: dict                      # name -> float or tuple (float2 -> B, float3 -> C, float4 -> D)
    textures: list                   # [(sampler, texture name)] in slot order
    blend: int = 0                   # 0 opaque, 1 alpha blend, 2 alpha to coverage
    alpha_tested: bool = False
    depth: int = 0                   # 0 normal, 1 no write, 2 off


@dataclass
class Mesh:
    name: str
    material: int
    pos: np.ndarray                  # (n, 3) AC space
    nrm: np.ndarray
    uv: np.ndarray
    tri: np.ndarray                  # (m, 3)
    cast_shadows: bool = True
    renderable: bool = True
    lod_out: float = 0.0             # 0 = always drawn
    tangent: np.ndarray = field(default=None)


@dataclass
class Dummy:
    name: str
    matrix: np.ndarray               # 4x4, rows X, Y, Z, translation


def tangents(pos, nrm, uv, tri):
    """Per-vertex tangent = dP/du, orthogonal to the normal."""
    p0, p1, p2 = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
    w0, w1, w2 = uv[tri[:, 0]], uv[tri[:, 1]], uv[tri[:, 2]]
    e1, e2 = p1 - p0, p2 - p0
    d1, d2 = w1 - w0, w2 - w0
    det = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    det = np.where(np.abs(det) < 1e-12, 1e-12, det)
    t = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) / det[:, None]
    acc = np.stack([sum(np.bincount(tri[:, c], t[:, k], len(pos)) for c in range(3)) for k in range(3)], 1)
    acc -= nrm * np.einsum("ij,ij->i", acc, nrm)[:, None]
    ln = np.linalg.norm(acc, axis=1)
    bad = ln < 1e-9
    if bad.any():                    # any vector perpendicular to the normal
        ref = np.where(np.abs(nrm[bad, 0:1]) < 0.9, [[1.0, 0, 0]], [[0, 0, 1.0]])
        alt = np.cross(nrm[bad], ref)
        acc[bad] = alt
        ln[bad] = np.linalg.norm(alt, axis=1)
    dead = ~(ln > 1e-9)                  # e.g. unused vertices with a zero normal
    acc[dead] = (1.0, 0.0, 0.0)
    ln[dead] = 1.0
    return acc / ln[:, None]


def _prop(name, value):
    a, b, c, d = 0.0, (0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 0.0)
    if isinstance(value, (int, float)):
        a = float(value)
        b, c, d = (a, a), (a, a, a), (a, a, a, a)
    elif len(value) == 2:
        b = tuple(map(float, value))
    elif len(value) == 3:
        c = tuple(map(float, value))
    else:
        d = tuple(map(float, value))
    return name, a, b, c, d


def write(path, textures, materials, meshes, dummies, root="root", version=5):
    """textures: {name: file bytes}; materials: [Material]; meshes: [Mesh]; dummies: [Dummy]."""
    b = bytearray(b"sc6969" + struct.pack("<i", version))
    if version > 5:
        b += struct.pack("<i", 0)
    b += struct.pack("<i", len(textures))
    for name, data in textures.items():
        b += struct.pack("<i", 1)
        _s(b, name)
        b += struct.pack("<I", len(data)) + data
    b += struct.pack("<i", len(materials))
    for m in materials:
        _s(b, m.name)
        _s(b, m.shader)
        b += struct.pack("<BBi", m.blend, int(m.alpha_tested), m.depth)
        props = [_prop(k, m.props[k]) for k in m.props]
        b += struct.pack("<i", len(props))
        for name, a, pb, pc, pd in props:
            _s(b, name)
            b += struct.pack("<f2f3f4f", a, *pb, *pc, *pd)
        b += struct.pack("<i", len(m.textures))
        for slot, (sampler, tex) in enumerate(m.textures):
            if tex not in textures:
                raise KeyError(f"material {m.name}: texture {tex} not embedded")
            _s(b, sampler)
            b += struct.pack("<i", slot)
            _s(b, tex)
    # root
    b += struct.pack("<i", 1)
    _s(b, root)
    b += struct.pack("<iB", len(meshes) + len(dummies), 1)
    b += np.eye(4, dtype="<f4").tobytes()
    for m in meshes:
        n = len(m.pos)
        if n > 65536:
            raise ValueError(f"{m.name}: {n} vertices (max 65536)")
        tan = m.tangent if m.tangent is not None else tangents(m.pos, m.nrm, m.uv, m.tri)
        b += struct.pack("<i", 2)
        _s(b, m.name)
        b += struct.pack("<iB", 0, 1)
        b += struct.pack("<BBBI", int(m.cast_shadows), 1, 0, n)
        vert = np.concatenate([m.pos, m.nrm, m.uv, tan], 1).astype("<f4")
        b += vert.tobytes()
        idx = np.asarray(m.tri, "<u2").ravel()
        b += struct.pack("<I", len(idx)) + idx.tobytes()
        lo, hi = m.pos.min(0), m.pos.max(0)
        centre = (lo + hi) / 2
        radius = float(np.linalg.norm(m.pos - centre, axis=1).max())
        b += struct.pack("<IIff3ffB", m.material, 0, 0.0, float(m.lod_out), *map(float, centre), radius,
                         int(m.renderable))
    for d in dummies:
        b += struct.pack("<i", 1)
        _s(b, d.name)
        b += struct.pack("<iB", 0, 1)
        b += np.asarray(d.matrix, "<f4").tobytes()
    with open(path, "wb") as f:
        f.write(b)
    return len(b)


def read(path):
    """Parse a .kn5 (version 5/6, base and mesh nodes) into plain dicts; checks every byte is used."""
    data = open(path, "rb").read()
    p = [0]

    def take(n):
        out = data[p[0]:p[0] + n]
        if len(out) != n:
            raise EOFError(f"truncated at {p[0]}")
        p[0] += n
        return out

    def i32():
        return struct.unpack("<i", take(4))[0]

    def string():
        return take(i32()).decode("utf-8")

    if take(6) != b"sc6969":
        raise ValueError("not a kn5")
    version = i32()
    if version > 5:
        i32()
    textures = {}
    for _ in range(i32()):
        i32()
        name = string()
        textures[name] = take(struct.unpack("<I", take(4))[0])
    materials = []
    for _ in range(i32()):
        name, shader = string(), string()
        blend, at, depth = struct.unpack("<BBi", take(6))
        props = {}
        for _ in range(i32()):
            pn = string()
            props[pn] = struct.unpack("<f2f3f4f", take(40))
        maps = [(string(), i32(), string()) for _ in range(i32())]
        materials.append(dict(name=name, shader=shader, blend=blend, alpha_tested=at, depth=depth, props=props, maps=maps))
    nodes = []

    def node(parent):
        cls = i32()
        name = string()
        nch = i32()
        active = take(1)[0]
        rec = dict(cls=cls, name=name, parent=parent, children=nch, active=active)
        if cls == 1:
            rec["matrix"] = np.frombuffer(take(64), "<f4").reshape(4, 4)
        elif cls == 2:
            rec["cast"], rec["visible"], rec["transparent"] = take(3)
            nv = struct.unpack("<I", take(4))[0]
            v = np.frombuffer(take(44 * nv), "<f4").reshape(nv, 11)
            ni = struct.unpack("<I", take(4))[0]
            rec["idx"] = np.frombuffer(take(2 * ni), "<u2")
            rec["pos"], rec["nrm"], rec["uv"], rec["tan"] = v[:, :3], v[:, 3:6], v[:, 6:8], v[:, 8:11]
            (rec["material"], rec["layer"], rec["lod_in"], rec["lod_out"], cx, cy, cz, rec["radius"],
             rec["renderable"]) = struct.unpack("<IIff3ffB", take(33))
            rec["centre"] = (cx, cy, cz)
        else:
            raise ValueError(f"unsupported node class {cls}")
        nodes.append(rec)
        for _ in range(nch):
            node(name)

    node(None)
    if p[0] != len(data):
        raise ValueError(f"{len(data) - p[0]} trailing bytes")
    return dict(version=version, textures=textures, materials=materials, nodes=nodes)
