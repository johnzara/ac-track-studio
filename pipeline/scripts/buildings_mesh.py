"""Mesh generation for building blocks (from build_buildings.py) with UV-mapped textures.

Walls: one 3 m bay of the facade texture per window column (bays fitted to each wall edge),
ground floor = bottom half of the texture, upper floors = top half.
Roofs: hipped roofs for any footprint shape, built from inward-offset rings (a discrete
straight skeleton) with a 0.45 m eave overhang and soffit; flat roofs for sheds/large blocks;
steep pyramids for bell towers.
"""

import math

import numpy as np
import shapely
from shapely.geometry import Point, Polygon, shape
from shapely.geometry.polygon import orient

BAY_M = 3.0
FLOOR_M = 3.0
OVERHANG_M = 0.45
RING_STEP_M = 1.5
PITCH_DEG = 20.0
TOWER_PITCH_DEG = 34.0
MAX_RISE_M = 4.0
ROOF_TILE_M = 2.0
TILING_WALLS = {"bld_stone_rubble", "bld_stone_basalt", "bld_stone_ashlar", "bld_brick"}
TILING_V_M = 6.0   # metres per V for tiling wall materials (our textures cover 3 x 6 m)
FULL_HEIGHT_WALLS = {"bld_campanile"}


class TexMesh:
    def __init__(self):
        self.pos, self.nrm, self.uv = [], [], []
        self.tris = {}

    def tri(self, mat, verts, uvs):
        a, b, c = (np.asarray(v, float) for v in verts)
        n = np.cross(b - a, c - a)
        ln = np.linalg.norm(n)
        if ln < 1e-8:
            return
        base = len(self.pos)
        self.pos += [a, b, c]
        self.nrm += [n / ln] * 3
        self.uv += list(uvs)
        self.tris.setdefault(mat, []).append((base, base + 1, base + 2))

    def quad(self, mat, verts, uvs):
        """Planar quad as two triangles sharing four vertices."""
        a, b, c, d = (np.asarray(v, float) for v in verts)
        n = np.cross(b - a, d - a)
        ln = np.linalg.norm(n)
        if ln < 1e-8:
            n = np.cross(c - b, a - b)
            ln = np.linalg.norm(n)
            if ln < 1e-8:
                return
        base = len(self.pos)
        self.pos += [a, b, c, d]
        self.nrm += [n / ln] * 4
        self.uv += list(uvs)
        self.tris.setdefault(mat, []).extend([(base, base + 1, base + 2), (base, base + 2, base + 3)])


def _rings_of(geom):
    for g in getattr(geom, "geoms", [geom]):
        if isinstance(g, Polygon) and not g.is_empty:
            yield g


def wall_bands(ground, eave, base):
    """(z0, z1, v0, v1) bands: below-ground skirt, ground floor, upper floors."""
    h = eave - ground
    bands = [(base, ground, 0.0, 0.0)] if ground - base > 0.01 else []
    if h < 2.6:
        bands.append((ground, eave, 0.0, 0.5 * h / FLOOR_M))
        return bands
    n = max(1, round(h / FLOOR_M))
    fh = h / n
    for k in range(n):
        z0, z1 = ground + k * fh, ground + (k + 1) * fh
        bands.append((z0, z1, 0.0, 0.5) if k == 0 else (z0, z1, 0.5, 1.0))
    return bands


def _hash(*vals):
    h = 2166136261
    for v in vals:
        for ch in str(v):
            h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return h / 0xFFFFFFFF


def add_walls(mesh, outline, members, ground, eave, base, origin, details=None):
    """Walls in 3 m bays. With `details` (a dict from add_block), residential walls get bay
    variants: a garage door on the road side of houses, French doors behind balconies; the
    chosen balcony bays are reported back in details["balconies"]."""
    member_geoms = [(shape(m["geometry"]), m["wall"]) for m in members]
    bands = wall_bands(ground, eave, base)
    for poly in _rings_of(orient(outline, 1.0)):
        for ring, reverse in [(poly.exterior, False)] + [(r, True) for r in poly.interiors]:
            pts = np.asarray(ring.coords)[:, :2]
            if reverse:  # holes: walls face into the courtyard
                pts = pts[::-1]
            for a, b in zip(pts[:-1], pts[1:]):
                length = float(np.hypot(*(b - a)))
                if length < 0.05:
                    continue
                mid = Point((a + b) / 2)
                mat = min(member_geoms, key=lambda mg: mg[0].boundary.distance(mid))[1]
                bays = max(1, round(length / BAY_M)) if length >= 1.5 else length / BAY_M
                a2, b2 = a - origin, b - origin
                if mat in FULL_HEIGHT_WALLS:       # bell tower: belfry at the top of every face
                    mesh.quad(mat, [(*a2, base), (*b2, base), (*b2, eave), (*a2, eave)],
                              [(0, -(ground - base) / max(eave - ground, 1)), (1, -(ground - base) / max(eave - ground, 1)), (1, 1), (0, 1)])
                    continue
                variants = details is not None and mat.startswith("bld_res_") and length >= 2.4
                if not variants:
                    for z0, z1, v0, v1 in bands:
                        if mat in TILING_WALLS:
                            v0, v1 = (z0 - base) / TILING_V_M, (z1 - base) / TILING_V_M
                        mesh.quad(mat, [(*a2, z0), (*b2, z0), (*b2, z1), (*a2, z1)],
                                  [(0, v0), (bays, v0), (bays, v1), (0, v1)])
                    continue
                n_b = int(bays)
                d = (b2 - a2) / n_b
                out = np.array([d[1], -d[0]]) / (np.linalg.norm(d) or 1)        # outward (CCW ring)
                front = details.get("front")
                facing = front is not None and out @ np.asarray(front) > 0.6
                long_side = length >= 0.6 * details["long"]
                upper = [k for k, (z0, z1, v0, v1) in enumerate(bands) if v0 >= 0.5]
                garage_bay = None
                if facing and details["house"] and not details.get("garage_done") and n_b >= 2:
                    garage_bay = int(_hash(details["key"], "g", ring.length) * n_b)
                    details["garage_done"] = True
                balcony = []
                for k in range(n_b):
                    pr = 0.85 if details["block"] else 0.35
                    balcony.append(bool(upper) and long_side and (facing or details["house"] is False)
                                   and _hash(details["key"], k, round(float(out[0]), 1)) < pr)
                for bi, (z0, z1, v0, v1) in enumerate(bands):
                    mats = []
                    for k in range(n_b):
                        m = mat
                        if bi in upper and balcony[k]:
                            m = mat + "_b"
                        elif v0 == 0.0 and v1 > 0 and k == garage_bay and z1 - z0 > 2.4:
                            m = mat + "_g"
                        mats.append(m)
                    k0 = 0
                    for k in range(1, n_b + 1):          # one quad per run of equal bays
                        if k == n_b or mats[k] != mats[k0]:
                            p0, p1 = a2 + d * k0, a2 + d * k
                            mesh.quad(mats[k0], [(*p0, z0), (*p1, z0), (*p1, z1), (*p0, z1)],
                                      [(0, v0), (k - k0, v0), (k - k0, v1), (0, v1)])
                            if mats[k0].endswith("_b"):      # one continuous balcony per run of bays
                                details["balconies"].append((p0 + origin, p1 + origin, out, z0))
                            k0 = k


def _roof_uv(verts):
    """Per-triangle UVs: V runs downhill, U along the contour, in ROOF_TILE_M units."""
    v = np.asarray(verts)
    n = np.cross(v[1] - v[0], v[2] - v[0])
    horiz = np.array([n[0], n[1]])
    if np.hypot(*horiz) < 1e-6:
        d = np.array([0.0, 1.0])
    else:
        d = horiz / np.hypot(*horiz)                 # downhill direction (normal's xy)
    p = np.array([[-d[1], d[0]], d])
    uv = v[:, :2] @ p.T / ROOF_TILE_M
    return [tuple(x) for x in uv]


def _cdt(geom):
    for g in _rings_of(geom):
        tri = shapely.constrained_delaunay_triangles(g)
        for t in getattr(tri, "geoms", []):
            yield np.asarray(t.exterior.coords)[:3]


def add_flat_roof(mesh, outline, z, mat, origin):
    for t in _cdt(outline):
        t = t - origin
        if (t[1][0] - t[0][0]) * (t[2][1] - t[0][1]) - (t[1][1] - t[0][1]) * (t[2][0] - t[0][0]) < 0:
            t = t[::-1]
        mesh.tri(mat, [(*p, z) for p in t], [tuple(p / ROOF_TILE_M) for p in t])


def add_hip_roof(mesh, outline, eave, mat, origin, pitch_deg=PITCH_DEG, overhang=OVERHANG_M,
                 ring_step=RING_STEP_M, fine_step=0.3, soffit=True):
    """Hip roof from inward-offset rings: coarse steps (the planes are the same planes), then
    fine steps just before the rings vanish, so the ridge stays sharp."""
    t = math.tan(math.radians(pitch_deg))
    outline = outline.buffer(0)

    def ring(r):
        return outline.buffer(-r, join_style="mitre", mitre_limit=4.0) if r != 0 else outline

    def ok(g, r):
        return not (g.is_empty or g.area < 0.05 or r * t > MAX_RISE_M)

    rings = []  # (polygon, r) with r = inward offset from the wall line
    r, step = -overhang, None
    while True:
        g = ring(r)
        if not ok(g, r):
            if step == ring_step and rings and rings[-1][1] + fine_step < r:   # refine near the top
                r, step = rings[-1][1] + fine_step, fine_step
                continue
            break
        rings.append((g, r))
        if r < 0:
            r, step = 0.0, ring_step
        else:
            r += step if step else ring_step
    if not rings:
        return
    for (outer, r0), (inner, r1) in zip(rings[:-1], rings[1:]):
        band = outer.difference(inner)
        for tri in _cdt(band):
            zs = [eave + (r1 if inner.boundary.distance(Point(p)) < 1e-3 else r0) * t for p in tri]
            verts = [(p[0] - origin[0], p[1] - origin[1], z) for p, z in zip(tri, zs)]
            n = np.cross(np.subtract(verts[1], verts[0]), np.subtract(verts[2], verts[0]))
            if n[2] < 0:
                verts = verts[::-1]
            mesh.tri(mat, verts, _roof_uv(verts))
    top, rt = rings[-1]
    add_flat_roof(mesh, top, eave + rt * t, mat, origin)
    if not soffit:
        return
    # soffit under the overhang, facing down
    outer = rings[0][0]
    z0 = eave - overhang * t
    for tri in _cdt(outer.difference(outline)):
        tri = tri - origin
        if (tri[1][0] - tri[0][0]) * (tri[2][1] - tri[0][1]) - (tri[1][1] - tri[0][1]) * (tri[2][0] - tri[0][0]) > 0:
            tri = tri[::-1]
        mesh.tri("roof_soffit", [(*p, z0) for p in tri], [(0, 0)] * 3)


RECT_ROOF_MIN = 0.93      # footprint area / its minimum rotated rectangle -> exact rectangular hip roof


def _rect_frame(outline):
    with np.errstate(all="ignore"):
        rect = outline.minimum_rotated_rectangle
    if rect.geom_type != "Polygon":
        return None
    c = np.asarray(rect.exterior.coords)[:4]
    e1, e2 = c[1] - c[0], c[2] - c[1]
    if np.linalg.norm(e1) < np.linalg.norm(e2):
        e1, e2 = e2, e1
    u = e1 / np.linalg.norm(e1)
    v = np.array([-u[1], u[0]])
    return c.mean(0), u, v, np.linalg.norm(e1) / 2, np.linalg.norm(e2) / 2, rect.area


def add_hip_rect(mesh, outline, eave, mat, origin, pitch_deg=PITCH_DEG, overhang=OVERHANG_M, soffit=True):
    """Exact hip roof over a (near-)rectangular footprint: 2 trapezoids + 2 triangles (6 triangles),
    flat-topped where the rise would exceed MAX_RISE_M; soffit strips under the overhang."""
    fr = _rect_frame(outline)
    if fr is None:
        return False
    ctr, u, v, a0, b0, _ = fr
    t = math.tan(math.radians(pitch_deg))
    a, b = a0 + overhang, b0 + overhang
    ze = eave - overhang * t
    rise = b * t
    if rise > MAX_RISE_M:
        d = MAX_RISE_M / t
        top = [(a - d, b - d), (-(a - d), b - d), (-(a - d), -(b - d)), (a - d, -(b - d))]
        zt = ze + MAX_RISE_M
    else:
        top = [(a - b, 0.0), (-(a - b), 0.0), (-(a - b), 0.0), (a - b, 0.0)]
        zt = ze + rise
    P = lambda x, y, z: (*(ctr + u * x + v * y - origin), z)  # noqa: E731
    base = [(a, b), (-a, b), (-a, -b), (a, -b)]
    for k in range(4):
        (x0, y0), (x1, y1) = base[k], base[(k + 1) % 4]
        (tx0, ty0), (tx1, ty1) = top[k], top[(k + 1) % 4]
        quad = [P(x0, y0, ze), P(tx0, ty0, zt), P(tx1, ty1, zt), P(x1, y1, ze)]
        for tri in ([quad[0], quad[3], quad[1]], [quad[1], quad[3], quad[2]]):
            nrm = np.cross(np.subtract(tri[1], tri[0]), np.subtract(tri[2], tri[0]))
            if np.linalg.norm(nrm) < 1e-6:
                continue
            if nrm[2] < 0:
                tri = tri[::-1]
            mesh.tri(mat, tri, _roof_uv(tri))
    if rise > MAX_RISE_M:
        q = [P(*top[0], zt), P(*top[1], zt), P(*top[2], zt), P(*top[3], zt)]
        mesh.quad(mat, q[::-1] if np.cross(np.subtract(q[1], q[0]), np.subtract(q[3], q[0]))[2] < 0 else q,
                  [tuple(np.asarray(x[:2]) / ROOF_TILE_M) for x in q])
    if soffit:
        inner = [(a0, b0), (-a0, b0), (-a0, -b0), (a0, -b0)]
        for k in range(4):
            q = [P(*base[k], ze), P(*base[(k + 1) % 4], ze), P(*inner[(k + 1) % 4], ze), P(*inner[k], ze)]
            if np.cross(np.subtract(q[1], q[0]), np.subtract(q[3], q[0]))[2] > 0:
                q = q[::-1]
            mesh.quad("roof_soffit", q, [(0, 0)] * 4)
    return True


def add_block(mesh, feat, origin, lod="high"):
    """lod "high": everything; "low": walls without bay variants, coarse roofs, no balconies,
    chimneys, gutters, soffits or tower crowns (seen from far away)."""
    p = feat["properties"]
    outline = shape(feat["geometry"])
    if outline.is_empty or outline.area < 2:
        return
    if p["style"] == "tower" and p["eave_z"] - p["ground_z"] >= 8:
        # bell towers are square: surveyed outlines with many short edges would repeat the
        # belfry texture on each of them
        outline = outline.minimum_rotated_rectangle
        p = dict(p, members=[dict(m, geometry=outline) for m in p["members"]])
    details = None
    if lod == "high" and p["style"] in {"residential", "rural"}:
        with np.errstate(all="ignore"):
            rect = outline.minimum_rotated_rectangle
        rc = np.asarray(rect.exterior.coords) if rect.geom_type == "Polygon" else np.zeros((3, 2))
        long_ = float(max(np.linalg.norm(rc[1] - rc[0]), np.linalg.norm(rc[2] - rc[1]))) if len(rc) >= 3 else 0
        h = p["eave_z"] - p["ground_z"]
        details = {"key": f"{outline.centroid.x:.1f},{outline.centroid.y:.1f}", "front": p.get("front"),
                   "house": h < 9.5 and outline.area < 260, "block": h >= 9.5, "long": long_, "balconies": []}
    if lod == "high":
        add_walls(mesh, outline, p["members"], p["ground_z"], p["eave_z"], p["base_z"], origin, details)
    else:
        _walls_simple(mesh, outline, p, origin)
    if details:
        for a_, b_, out, z in details["balconies"]:
            add_balcony(mesh, a_ - origin, b_ - origin, out, z)
    pitched = p["roof"] in {"hip", "gable"}
    if p["roof"] == "gable" and p.get("ridge"):
        wall_mat = p["members"][0]["wall"]
        add_gable_roof(mesh, outline, p["eave_z"], np.asarray(p["ridge"]), p["roof_mat"], origin,
                       p.get("gable_mat") or _plain(wall_mat))
    elif p["roof"] == "flat":
        add_flat_roof(mesh, outline, p["eave_z"], p["roof_mat"], origin)
        pitched = False
    elif p["roof"] == "pyramid":
        pitch = p.get("roof_pitch", TOWER_PITCH_DEG)
        add_hip_roof(mesh, outline, p["eave_z"], p["roof_mat"], origin, pitch, 0.35,
                     soffit=lod == "high")
        if lod == "high" and p["style"] == "tower" and p["eave_z"] - p["ground_z"] >= 8:
            add_tower_crown(mesh, outline, p["eave_z"], origin, pitch)
    else:
        fr = _rect_frame(outline)
        if fr is not None and outline.area / max(fr[5], 1e-6) >= RECT_ROOF_MIN:
            add_hip_rect(mesh, outline, p["eave_z"], p["roof_mat"], origin, soffit=lod == "high")
        elif lod == "high":
            add_hip_roof(mesh, outline, p["eave_z"], p["roof_mat"], origin)
        else:
            add_hip_roof(mesh, outline, p["eave_z"], p["roof_mat"], origin, overhang=0.0,
                         ring_step=4.0, fine_step=2.0, soffit=False)
    if lod != "high":
        return
    if pitched and p["style"] in {"residential", "rural"} and outline.area >= 40:
        add_chimneys(mesh, outline, p, origin)
    if pitched:
        add_gutter(mesh, outline, p["eave_z"], origin)


def _walls_simple(mesh, outline, p, origin):
    """Distant walls: one quad per edge, the facade texture repeating every two floors."""
    member_geoms = [(shape(m["geometry"]), m["wall"]) for m in p["members"]]
    z0, zg, z1 = p["base_z"], p["ground_z"], p["eave_z"]
    for poly in _rings_of(orient(outline, 1.0)):
        for ring_, rev in [(poly.exterior, False)] + [(r, True) for r in poly.interiors]:
            pts = np.asarray(ring_.coords)[:, :2]
            if rev:
                pts = pts[::-1]
            for a, b in zip(pts[:-1], pts[1:]):
                length = float(np.hypot(*(b - a)))
                if length < 0.05:
                    continue
                mat = min(member_geoms, key=lambda mg: mg[0].boundary.distance(Point((a + b) / 2)))[1]
                bays = max(1, round(length / BAY_M)) if length >= 1.5 else length / BAY_M
                a2, b2 = a - origin, b - origin
                if mat in FULL_HEIGHT_WALLS:
                    v0, v1 = -(zg - z0) / max(z1 - zg, 1), 1.0
                elif mat in TILING_WALLS:
                    v0, v1 = 0.0, (z1 - z0) / TILING_V_M
                else:
                    v0, v1 = -(zg - z0) / (2 * FLOOR_M), (z1 - zg) / (2 * FLOOR_M)
                mesh.quad(mat, [(*a2, z0), (*b2, z0), (*b2, z1), (*a2, z1)], [(0, v0), (bays, v0), (bays, v1), (0, v1)])


def collision_block(mesh, feat, origin):
    """Simple collision geometry: one quad per wall from the foundation to the eave (the game
    collides with this, not with the visible detail)."""
    p = feat["properties"]
    outline = shape(feat["geometry"])
    if outline.is_empty or outline.area < 2:
        return
    z0, z1 = p["base_z"], p["eave_z"]
    for poly in _rings_of(orient(outline, 1.0)):
        for ring_, rev in [(poly.exterior, False)] + [(r, True) for r in poly.interiors]:
            pts = np.asarray(ring_.coords)[:, :2]
            if rev:
                pts = pts[::-1]
            for a, b in zip(pts[:-1], pts[1:]):
                if np.hypot(*(b - a)) < 0.05:
                    continue
                a2, b2 = a - origin, b - origin
                mesh.quad("collision", [(*a2, z0), (*b2, z0), (*b2, z1), (*a2, z1)], [(0, 0), (1, 0), (1, 1), (0, 1)])


GABLE_PITCH_DEG = 24.0
GABLE_MAX_RISE_M = 4.5


def _plain(wall_mat):
    """Plain plaster of the same colour for gable ends (no windows)."""
    if wall_mat.startswith("bld_res_") or wall_mat.startswith("bld_shop_"):
        return "bld_plain_" + wall_mat.split("_")[2]
    return wall_mat


def _gable_geometry(outline, ridge):
    u = ridge / np.linalg.norm(ridge)
    v = np.array([-u[1], u[0]])
    pts = np.asarray(outline.exterior.coords)[:, :2]
    tv = pts @ v
    c = (tv.max() + tv.min()) / 2            # ridge line: t = c (along v)
    hw = (tv.max() - tv.min()) / 2
    tanp = min(math.tan(math.radians(GABLE_PITCH_DEG)), GABLE_MAX_RISE_M / max(hw, 0.1))
    return u, v, c, hw, tanp


def add_gable_roof(mesh, outline, eave, ridge, mat, origin, gable_mat):
    u, v, c, hw, tanp = _gable_geometry(outline, np.asarray(ridge, float))
    zf = lambda p: eave + (hw - abs(np.asarray(p)[..., :2] @ v - c)) * tanp   # noqa: E731
    # roof planes: outline + overhang, split along the ridge
    ext = outline.buffer(OVERHANG_M, join_style="mitre", mitre_limit=3.0)
    tu = np.asarray(ext.exterior.coords)[:, :2] @ u
    big = 1e3
    for side in (1, -1):
        lo, hi = (c, c + side * big) if side > 0 else (c + side * big, c)
        p0 = u * (tu.min() - 5) + v * lo
        p1 = u * (tu.max() + 5) + v * lo
        p2 = u * (tu.max() + 5) + v * hi
        p3 = u * (tu.min() - 5) + v * hi
        half = ext.intersection(Polygon([p0, p1, p2, p3]))
        for tri in _cdt(half):
            verts = [(q[0] - origin[0], q[1] - origin[1], float(zf(q))) for q in tri]
            nrm = np.cross(np.subtract(verts[1], verts[0]), np.subtract(verts[2], verts[0]))
            if nrm[2] < 0:
                verts = verts[::-1]
            mesh.tri(mat, verts, _roof_uv(verts))
            under = [(x, y, z - 0.12) for x, y, z in verts[::-1]]          # underside of the overhang
            mesh.tri("roof_soffit", under, [(0, 0)] * 3)
    # ridge cap (coppi colmo)
    a_ = u * (tu.min()) + v * c
    b_ = u * (tu.max()) + v * c
    zr = eave + hw * tanp
    for s_ in (1, -1):
        q = [(*(a_ - origin), zr + 0.02), (*(b_ - origin), zr + 0.02), (*(b_ + v * 0.16 * s_ - origin), zr - 0.04),
             (*(a_ + v * 0.16 * s_ - origin), zr - 0.04)]
        n = np.cross(np.subtract(q[1], q[0]), np.subtract(q[3], q[0]))
        if n[2] < 0:
            q = q[::-1]
        mesh.quad(mat, q, [(0, 0), (np.linalg.norm(b_ - a_) / ROOF_TILE_M, 0), (np.linalg.norm(b_ - a_) / ROOF_TILE_M, 0.1), (0, 0.1)])
    # gable ends: walls above the eave up to the roof line
    for poly in _rings_of(orient(outline, 1.0)):
        pts = np.asarray(poly.exterior.coords)[:, :2]
        for a, b in zip(pts[:-1], pts[1:]):
            za, zb = float(zf(a)), float(zf(b))
            ta, tb = a @ v - c, b @ v - c
            ring = [(a, eave), (b, eave), (b, zb)]
            if ta * tb < 0:                                  # edge crosses the ridge
                k = ta / (ta - tb)
                m = a + (b - a) * k
                ring.append((m, float(zf(m))))
            ring.append((a, za))
            if max(z for _, z in ring) - eave < 0.05:
                continue
            P = [(*(q - origin), z) for q, z in ring]
            for i in range(1, len(P) - 1):
                tri = [P[0], P[i], P[i + 1]]
                uv = [(np.linalg.norm(np.subtract(t[:2], P[0][:2])) / BAY_M, 0.5 + (t[2] - eave) / (2 * FLOOR_M)) for t in tri]
                n = np.cross(np.subtract(tri[1], tri[0]), np.subtract(tri[2], tri[0]))
                outw = np.array([b[1] - a[1], -(b[0] - a[0]), 0.0])
                if n @ outw < 0:
                    tri, uv = tri[::-1], uv[::-1]
                mesh.tri(gable_mat, tri, uv)


def _box(mesh, mat, x0, y0, z0, x1, y1, z1, top=None):
    q = [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]
    for a, b in q:
        mesh.quad(mat, [(*a, z0), (*b, z0), (*b, z1), (*a, z1)], [(0, 0), (1, 0), (1, 1), (0, 1)])
    mesh.quad(top or mat, [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)], [(0, 0), (1, 0), (1, 1), (0, 1)])


def add_chimneys(mesh, outline, p, origin):
    """1-2 plastered chimneys with a tiled cap, rising ~1 m above the ridge line."""
    rng = _hash(p.get("ground_z"), outline.centroid.x)
    n = 2 if outline.area > 150 else 1
    wall = _plain(p["members"][0]["wall"])
    eave = p["eave_z"]
    with np.errstate(all="ignore"):
        rect = outline.minimum_rotated_rectangle
    rc = np.asarray(rect.exterior.coords)[:4]
    e1, e2 = rc[1] - rc[0], rc[2] - rc[1]
    long_, short = (e1, e2) if np.linalg.norm(e1) >= np.linalg.norm(e2) else (e2, e1)
    ctr = rc.mean(0)
    rise = min(np.linalg.norm(short) / 2 * math.tan(math.radians(GABLE_PITCH_DEG)), GABLE_MAX_RISE_M)
    for k in range(n):
        f = (rng * 0.5 + 0.15 + 0.35 * k) % 0.7 - 0.35
        q = ctr + long_ * f + short * 0.12 - origin
        top = eave + rise + 0.9
        _box(mesh, wall, q[0] - 0.3, q[1] - 0.3, eave, q[0] + 0.3, q[1] + 0.3, top, top="concrete")
        _box(mesh, p["roof_mat"], q[0] - 0.42, q[1] - 0.42, top + 0.12, q[0] + 0.42, q[1] + 0.42, top + 0.2)


def add_gutter(mesh, outline, eave, origin):
    """Dark gutter strip along the eaves."""
    ext = outline.buffer(OVERHANG_M, join_style="mitre", mitre_limit=3.0)
    z = eave - OVERHANG_M * math.tan(math.radians(PITCH_DEG))
    pts = np.asarray(ext.exterior.coords)[:, :2] - origin
    for a, b in zip(pts[:-1], pts[1:]):
        if np.linalg.norm(b - a) < 0.3:
            continue
        mesh.quad("metal_dark", [(*a, z - 0.14), (*b, z - 0.14), (*b, z + 0.02), (*a, z + 0.02)], [(0, 0), (1, 0), (1, 1), (0, 1)])


def add_balcony(mesh, a, b, out, z):
    """Concrete slab 1.1 m deep with a wrought-iron railing on three sides."""
    d = b - a
    L = np.linalg.norm(d)
    if L < 0.5:
        return
    t = d / L
    a, b = a + t * 0.15, b - t * 0.15
    o = np.asarray(out) * 1.1
    c = [a, b, b + o, a + o]
    zt, zb = z + 0.02, z - 0.16
    mesh.quad("concrete", [(*c[0], zt), (*c[1], zt), (*c[2], zt), (*c[3], zt)], [(0, 0), (1, 0), (1, 0.4), (0, 0.4)])
    mesh.quad("concrete", [(*c[3], zb), (*c[2], zb), (*c[1], zb), (*c[0], zb)], [(0, 0), (1, 0), (1, 0.4), (0, 0.4)])
    for p, q in ((c[1], c[2]), (c[2], c[3]), (c[3], c[0])):
        mesh.quad("concrete", [(*p, zb), (*q, zb), (*q, zt), (*p, zt)], [(0, 0), (1, 0), (1, 0.1), (0, 0.1)])
    for p, q in ((c[1], c[2]), (c[2], c[3]), (c[3], c[0])):
        w = np.linalg.norm(q - p)
        mesh.quad("fence_iron_alpha", [(*p, zt), (*q, zt), (*q, zt + 1.0), (*p, zt + 1.0)], [(0, 0), (w / 2, 0), (w / 2, 1), (0, 1)])


def _pyramid(mesh, mat, centre, half, z0, height):
    cx, cy = centre
    c = [(cx - half, cy - half), (cx + half, cy - half), (cx + half, cy + half), (cx - half, cy + half)]
    apex = (cx, cy, z0 + height)
    for k in range(4):
        a, b = c[k], c[(k + 1) % 4]
        mesh.tri(mat, [(*a, z0), (*b, z0), apex], [(0, 0), (1, 0), (0.5, 1)])


def add_tower_crown(mesh, outline, eave, origin, pitch=TOWER_PITCH_DEG):
    """Bell-tower details seen on the reference church's campanile: stone pinnacles on the four corners
    and an iron cross on the roof's apex."""
    rect = outline.minimum_rotated_rectangle
    pts = np.asarray(rect.exterior.coords)[:4] - origin
    ctr = pts.mean(0)
    for p in pts:
        q = p + (ctr - p) * min(0.35 / max(np.linalg.norm(ctr - p), 1e-6), 0.3)
        _box(mesh, "bld_stone_ashlar", q[0] - 0.22, q[1] - 0.22, eave, q[0] + 0.22, q[1] + 0.22, eave + 0.35)
        _pyramid(mesh, "bld_stone_ashlar", q, 0.2, eave + 0.35, 1.1)
    half = 0.5 * min(np.linalg.norm(pts[1] - pts[0]), np.linalg.norm(pts[2] - pts[1]))
    top = eave + half * np.tan(np.radians(pitch))
    cx, cy = ctr
    _box(mesh, "metal_dark", cx - 0.04, cy - 0.04, top - 0.2, cx + 0.04, cy + 0.04, top + 1.6)
    _box(mesh, "metal_dark", cx - 0.4, cy - 0.04, top + 1.0, cx + 0.4, cy + 0.04, top + 1.08)


def collada(mesh, name, texture_dir_rel):
    """Collada 1.4.1 with positions, normals and UVs; materials reference PNG textures."""
    pos = np.asarray(mesh.pos).reshape(-1)
    nrm = np.asarray(mesh.nrm).reshape(-1)
    uv = np.asarray(mesh.uv, float).reshape(-1)
    f = lambda a, d=3: " ".join(f"{x:.{d}f}" for x in a)  # noqa: E731
    images, effects, mats, prims, binds = [], [], [], [], []
    for m in sorted(mesh.tris):
        images.append(f'<image id="{m}-img" name="{m}"><init_from>{texture_dir_rel}/{m}.png</init_from></image>')
        effects.append(
            f'<effect id="{m}-fx"><profile_COMMON><newparam sid="{m}-surf"><surface type="2D">'
            f'<init_from>{m}-img</init_from></surface></newparam><newparam sid="{m}-samp"><sampler2D>'
            f'<source>{m}-surf</source></sampler2D></newparam><technique sid="c"><lambert><diffuse>'
            f'<texture texture="{m}-samp" texcoord="UVMap"/></diffuse></lambert></technique>'
            f'</profile_COMMON></effect>')
        mats.append(f'<material id="{m}" name="{m}"><instance_effect url="#{m}-fx"/></material>')
        idx = " ".join(str(i) for t in mesh.tris[m] for i in t)
        prims.append(f'<triangles material="{m}" count="{len(mesh.tris[m])}">'
                     f'<input semantic="VERTEX" source="#{name}-v" offset="0"/>'
                     f'<input semantic="TEXCOORD" source="#{name}-uv" offset="0" set="0"/><p>{idx}</p></triangles>')
        binds.append(f'<instance_material symbol="{m}" target="#{m}"><bind_vertex_input semantic="UVMap" '
                     f'input_semantic="TEXCOORD" input_set="0"/></instance_material>')
    nv = len(mesh.pos)
    acc = lambda src, cnt, params: (f'<technique_common><accessor source="#{src}" count="{cnt}" '  # noqa: E731
                                    f'stride="{len(params)}">' + "".join(
                                        f'<param name="{p}" type="float"/>' for p in params)
                                    + '</accessor></technique_common>')
    return f'''<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
<asset><unit name="meter" meter="1"/><up_axis>Z_UP</up_axis></asset>
<library_images>{"".join(images)}</library_images>
<library_effects>{"".join(effects)}</library_effects>
<library_materials>{"".join(mats)}</library_materials>
<library_geometries><geometry id="{name}-mesh" name="{name}"><mesh>
<source id="{name}-p"><float_array id="{name}-pa" count="{nv * 3}">{f(pos)}</float_array>{acc(name + "-pa", nv, "XYZ")}</source>
<source id="{name}-n"><float_array id="{name}-na" count="{nv * 3}">{f(nrm)}</float_array>{acc(name + "-na", nv, "XYZ")}</source>
<source id="{name}-uv"><float_array id="{name}-uva" count="{nv * 2}">{f(uv, 4)}</float_array>{acc(name + "-uva", nv, "ST")}</source>
<vertices id="{name}-v"><input semantic="POSITION" source="#{name}-p"/><input semantic="NORMAL" source="#{name}-n"/></vertices>
{"".join(prims)}
</mesh></geometry></library_geometries>
<library_visual_scenes><visual_scene id="scene" name="scene">
<node id="{name}2" name="{name}2"><instance_geometry url="#{name}-mesh"><bind_material><technique_common>
{"".join(binds)}</technique_common></bind_material></instance_geometry></node>
</visual_scene></library_visual_scenes>
<scene><instance_visual_scene url="#scene"/></scene>
</COLLADA>
'''


def _compact(mesh):
    """Unique vertices (positions to 1 cm, normals to 0.01, UVs to 0.001) and re-indexed triangles."""
    pos = np.round(np.asarray(mesh.pos, float), 2)
    nrm = np.round(np.asarray(mesh.nrm, float), 2)
    uv = np.round(np.asarray(mesh.uv, float), 3)
    key = np.column_stack([pos, nrm, uv])
    uniq, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    return uniq[:, :3], uniq[:, 3:6], uniq[:, 6:8], {m: inv[np.asarray(t)] for m, t in mesh.tris.items() if len(t)}


def _num(a, d):
    s = " ".join(f"{x:.{d}f}" for x in np.asarray(a).reshape(-1))
    return s.replace("-0.00 ", "0.00 ").replace("-0.000 ", "0.000 ")


def collada_lods(name, lods, collision, texture_dir_rel, ext="png"):
    """Collada with detail levels and a collision mesh, structured like the stock Italy shapes:
    base00 -> collision-1 (marker), start01 -> <name>_a<size> for each detail level (the size is
    the on-screen pixel size above which that level is drawn) + Colmesh-1 (collision only)."""
    parts = [(f"{name}_a{size}", m) for size, m in lods if m.pos]
    if collision is not None and collision.pos:
        parts.append(("Colmesh-1", collision))
    mats = sorted({m for _, mesh in parts for m in mesh.tris})
    images, effects, materials, geoms, nodes = [], [], [], [], []
    for m in mats:
        if m != "collision":
            images.append(f'<image id="{m}-img" name="{m}"><init_from>{texture_dir_rel}/{m}.{ext}</init_from></image>')
            effects.append(
                f'<effect id="{m}-fx"><profile_COMMON><newparam sid="{m}-surf"><surface type="2D">'
                f'<init_from>{m}-img</init_from></surface></newparam><newparam sid="{m}-samp"><sampler2D>'
                f'<source>{m}-surf</source></sampler2D></newparam><technique sid="c"><lambert><diffuse>'
                f'<texture texture="{m}-samp" texcoord="UVMap"/></diffuse></lambert></technique>'
                f'</profile_COMMON></effect>')
        else:
            effects.append(f'<effect id="{m}-fx"><profile_COMMON><technique sid="c"><lambert><diffuse>'
                           f'<color>0.5 0.5 0.5 1</color></diffuse></lambert></technique></profile_COMMON></effect>')
        materials.append(f'<material id="{m}" name="{m}"><instance_effect url="#{m}-fx"/></material>')
    for gname, mesh in parts:
        pos, nrm, uv, tris = _compact(mesh)
        gid = gname.replace("-", "_")
        prims, binds = [], []
        for m in sorted(tris):
            prims.append(f'<triangles material="{m}" count="{len(tris[m])}">'
                         f'<input semantic="VERTEX" source="#{gid}-v" offset="0"/>'
                         f'<input semantic="TEXCOORD" source="#{gid}-uv" offset="0" set="0"/>'
                         f'<p>{" ".join(map(str, tris[m].reshape(-1)))}</p></triangles>')
            binds.append(f'<instance_material symbol="{m}" target="#{m}"><bind_vertex_input semantic="UVMap" '
                         f'input_semantic="TEXCOORD" input_set="0"/></instance_material>')
        nv = len(pos)
        acc = lambda src, cnt, params: (f'<technique_common><accessor source="#{src}" count="{cnt}" '  # noqa: E731
                                        f'stride="{len(params)}">' + "".join(
                                            f'<param name="{q}" type="float"/>' for q in params)
                                        + '</accessor></technique_common>')
        geoms.append(
            f'<geometry id="{gid}-mesh" name="{gname}"><mesh>'
            f'<source id="{gid}-p"><float_array id="{gid}-pa" count="{nv * 3}">{_num(pos, 2)}</float_array>{acc(gid + "-pa", nv, "XYZ")}</source>'
            f'<source id="{gid}-n"><float_array id="{gid}-na" count="{nv * 3}">{_num(nrm, 2)}</float_array>{acc(gid + "-na", nv, "XYZ")}</source>'
            f'<source id="{gid}-uv"><float_array id="{gid}-uva" count="{nv * 2}">{_num(uv, 3)}</float_array>{acc(gid + "-uva", nv, "ST")}</source>'
            f'<vertices id="{gid}-v"><input semantic="POSITION" source="#{gid}-p"/><input semantic="NORMAL" source="#{gid}-n"/></vertices>'
            + "".join(prims) + '</mesh></geometry>')
        nodes.append(f'<node id="node-{gid}" name="{gname}"><instance_geometry url="#{gid}-mesh"><bind_material>'
                     f'<technique_common>{"".join(binds)}</technique_common></bind_material></instance_geometry></node>')
    return f'''<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
<asset><unit name="meter" meter="1"/><up_axis>Z_UP</up_axis></asset>
<library_images>{"".join(images)}</library_images>
<library_effects>{"".join(effects)}</library_effects>
<library_materials>{"".join(materials)}</library_materials>
<library_geometries>{"".join(geoms)}</library_geometries>
<library_visual_scenes><visual_scene id="scene" name="scene">
<node id="node-base00" name="base00"><node id="node-collision-1" name="collision-1"/>
<node id="node-start01" name="start01">{"".join(nodes)}</node></node>
</visual_scene></library_visual_scenes>
<scene><instance_visual_scene url="#scene"/></scene>
</COLLADA>
'''
