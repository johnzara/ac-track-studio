#!/usr/bin/env python3
"""Assetto Corsa geometry for the climb, in the local frame (+X east, +Y north, +Z up, metres).

Road
  The climb's centreline (output/roads_local.geojson, climb parts in route order) through a
  centripetal Catmull-Rom spline, resampled every 0.5 m. Heights and widths are smoothed along
  the road (Gaussian, sigma 4 m / 3 m) so the grade has no kink at the profile's 2-4 m nodes or at
  the ends of the bridge decks. The surveyed width plus config assetto_corsa.widen_m (less where
  another leg of the climb or a house is close); centreline corners too tight for that width are
  rounded locally. Five vertices across with a 1.5 % crown. Surface: one 4096 px texture set of
  eight 45 m strips (ac_textures.make_road_textures), one strip per 45 m segment, patched strips
  where the survey saw a patched road. The four bridges get a slab and parapets.
Terrain
  One Delaunay triangulation of dem_final.tif samples: every 1 m within 40 m of the centreline,
  2 m to 150 m, then 4 / 8 / 16 m to the map edge; plus the road's edge vertices (shared with the
  road, so the seam is closed) and two verge rows (gravel at +0.3 m, ground at +1.5 m, narrower
  where another leg of the climb is close). Samples on the road or verge are dropped, and so are
  triangles whose centroid lies on the carriageway. Under bridges the ground passes beneath.
  Materials (all ksMultilayer but the rock):
    within CORR_M of the climb   128 m tiles with 13 cm masks (gravel shoulder, broken-up edges),
                                 packed 4 x 4 into atlases (terrain_c<k>)
    elsewhere                    1024 m tiles (terrain_<i><j>)
    steep faces                  limestone (rock_face, box-mapped photo + normal map)
Walls and guardrails
  The surveyed walls (boundaries.json) minus the stretches within HAIRPIN_CLEAR m of a hairpin
  apex (turn >= 120 deg, audit_climb.json), as dry-stone, mortared stone or board-formed
  concrete; the guardrails (climb_furniture.json) as W-beam profiles on posts, with an invisible
  box for the physics.
Output: output/ac/geometry.npz + geometry.json (meshes, markers), output/ac/textures/*.png, and
output/ac/terrain_points.npy (the terrain's vertices, for placing things on its surface).
"""

import json

import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt, gaussian_filter1d, minimum_filter1d
from scipy.spatial import Delaunay, cKDTree

import ac_textures as tx
from common import OUTPUT, Config, Heightfield

AC_DIR = OUTPUT / "ac"
TEX_DIR = AC_DIR / "textures"
STEP = 0.5                    # road stations (m)
CROWN = 0.015                 # crossfall each side of the centreline
VERGE = 1.5                   # ground verge beyond the asphalt edge (m)
GRAVEL = 0.3                  # gravel strip at the asphalt edge (m), 3 cm lower
BANDS = [(40, 1), (150, 2), (300, 4), (600, 8), (1e9, 16)]   # (distance to centreline, grid step)
TILE = 1024                   # far terrain texture tile (m)
TILE_MARGIN = 32              # m of neighbouring ground in each tile texture (no wrap at edges)
TILE_PX, TILE_MASK_PX = 1024, 512
CORR_M = 60.0                 # terrain within this of the centreline gets the corridor tiles
CORR_TILE, CORR_MARGIN = 128, 4
CORR_PX, CORR_BASE_PX = 1024, 512    # per tile: mask (13 cm), base colour (27 cm)
ATLAS = 4                     # corridor tiles per atlas side
ROCK_SLOPE, ROCK_SLOPE_LAYER = 50.0, 38.0   # deg: rock faces (the second where the rock layer says so);
                                            # config.json assetto_corsa.rock_slope / rock_slope_layer override
ROCK_RELIEF_M = 0.55          # rock faces: vertical relief (fractal noise) on their vertices
GROUND_RELIEF_M = (0.22, 0.08)   # other ground: relief at 7 m and 2 m wavelengths (the DEM is smoothed)
GROUND_RELIEF_CLEAR = 4.0     # ... none within this of the verge, full from twice that
HOUSE_RELIEF_CLEAR = 6.0      # ... and none within this of a building
ROCK_RELIEF_CLEAR = 3.0       # ... none within this of the verge, full from twice that
ROCK_BLOCK = 512.0            # m: rock meshes per block (culling)
MAX_VERTS = 60000             # per mesh (kn5 indices are 16-bit)
HAIRPIN_TURN = 120.0          # deg: bends this sharp count as hairpins
HAIRPIN_CLEAR = 25.0          # m either side of the apex: no walls
WALL_T = 0.35                 # wall thickness (m)
WALL_CLEAR = 0.5              # walls at least this far off the asphalt
RAIL_CLEAR = 0.35             # guardrail face at least this far off the asphalt
VERGE_RISE, VERGE_DROP = 0.35, 0.6   # the verge's outer edge within this of the asphalt edge's height
CUT_CLEAR, CUT_RISE = 2.5, 1.0       # cuttings rising > CUT_RISE within CUT_CLEAR past the verge pushed back
LEG_GAP = 1.0                 # m kept between the asphalt of two legs of the climb when widening
HOUSE_GAP = 0.6               # m kept between the asphalt and a building when widening
SEG_STATIONS = int(round(tx.ROAD_SEG_M / STEP))   # 90 stations = one road texture strip
W_BEAM = np.array([(0.000, 0.310), (0.040, 0.300), (0.083, 0.255), (0.083, 0.205), (0.035, 0.160),
                   (0.035, 0.150), (0.083, 0.105), (0.083, 0.055), (0.040, 0.010), (0.000, 0.000)])  # (towards the road, up) m


def widen_m():
    return float(Config().raw.get("assetto_corsa", {}).get("widen_m", 1.0))


# ----------------------------------------------------------------------------- centreline
def _catmull_rom(p, per_seg=16):
    """Centripetal Catmull-Rom through the xy of the rows of p. Returns dense samples and, per
    sample, the fractional node index (to interpolate per-node values)."""
    xy = np.vstack([2 * p[0, :2] - p[1, :2], p[:, :2], 2 * p[-1, :2] - p[-2, :2]])
    d = np.linalg.norm(np.diff(xy, axis=0), axis=1)
    knots = np.concatenate([[0], np.cumsum(np.sqrt(np.maximum(d, 1e-6)))])
    tt = np.linspace(0, 1, per_seg, endpoint=False)
    out, frac = [], []
    for i in range(1, len(xy) - 2):
        p0, p1, p2, p3 = xy[i - 1:i + 3]
        t0, t1, t2, t3 = knots[i - 1:i + 3]
        t = (t1 + (t2 - t1) * tt)[:, None]
        a1 = (t1 - t) / (t1 - t0) * p0 + (t - t0) / (t1 - t0) * p1
        a2 = (t2 - t) / (t2 - t1) * p1 + (t - t1) / (t2 - t1) * p2
        a3 = (t3 - t) / (t3 - t2) * p2 + (t - t2) / (t3 - t2) * p3
        b1 = (t2 - t) / (t2 - t0) * a1 + (t - t0) / (t2 - t0) * a2
        b2 = (t3 - t) / (t3 - t1) * a2 + (t - t1) / (t3 - t1) * a3
        out.append((t2 - t) / (t2 - t1) * b1 + (t - t1) / (t2 - t1) * b2)
        frac.append(i - 1 + tt)
    out.append(xy[-2][None])
    frac.append([len(p) - 1.0])
    return np.vstack(out), np.concatenate(frac)


def load_centreline():
    feats = [f for f in json.loads((OUTPUT / "roads_local.geojson").read_text())["features"]
             if str(f["properties"].get("climb")) == "True"]
    feats.sort(key=lambda f: float(f["properties"]["route_m0"]))
    nodes, widths, bridge = [], [], []
    for f in feats:
        c = np.asarray(f["geometry"]["coordinates"], float)
        w = f["properties"]["widths"]
        w = np.asarray(json.loads(w) if isinstance(w, str) else w, float)
        b = str(f["properties"]["bridge"]) == "True"
        if nodes and np.linalg.norm(c[0, :2] - nodes[-1][:2]) < 0.05:
            c, w = c[1:], w[1:]
            bridge[-1] = bridge[-1] or b
        nodes += list(c)
        widths += list(w)
        bridge += [b] * len(c)
    return np.asarray(nodes), np.asarray(widths), np.asarray(bridge)


def landmark_points(below=3.0):
    """Points of the hand-built landmark models (landmarks.py: house walls, gates, fences) up to
    `below` m above their ground, every 0.25 m at most: the road is not widened into them."""
    import math
    import shapely
    path = OUTPUT / "landmarks" / "placements.json"
    if not path.exists():
        return []
    import dae
    pts = []
    for lm in json.loads(path.read_text()):
        c, s_ = math.cos(lm["yaw"]), math.sin(lm["yaw"])
        R = np.array([[c, -s_, 0], [s_, c, 0], [0, 0, 1.0]])
        for tris, _ in dae.load(str(OUTPUT / "landmarks" / lm["shape"])).lod().values():
            T = tris @ R.T + np.asarray(lm["position"])                  # (n, 3, 3)
            T = T[T[:, :, 2].min(1) < lm["position"][2] + below]
            if not len(T):
                continue
            for a, b in ((0, 1), (1, 2), (2, 0)):                       # along every edge, 0.25 m apart
                L = np.linalg.norm(T[:, b, :2] - T[:, a, :2], axis=1)
                k = np.maximum(np.ceil(L / 0.25).astype(int), 1)
                f = np.concatenate([np.arange(n + 1) / n for n in k])
                A, B = np.repeat(T[:, a, :2], k + 1, 0), np.repeat(T[:, b, :2], k + 1, 0)
                pts.append(np.unique(np.round((A + (B - A) * f[:, None]) * 4) / 4, axis=0))
    return list(shapely.points(np.concatenate(pts))) if pts else []


class Road:
    """Stations every STEP m: position, frame, smoothed height and width, route metres."""

    def __init__(self):
        nodes, widths, bridge = load_centreline()
        node_s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(nodes[:, :2], axis=0), axis=1))])
        dense, frac = _catmull_rom(nodes)
        ds = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(dense, axis=0), axis=1))])
        self.s = np.arange(0, ds[-1], STEP)
        self.xy = np.stack([np.interp(self.s, ds, dense[:, 0]), np.interp(self.s, ds, dense[:, 1])], 1)
        f = np.interp(self.s, ds, frac)
        idx = np.arange(len(nodes))
        lo, hi = np.floor(f).astype(int), np.ceil(f).astype(int).clip(0, len(nodes) - 1)
        self.bridge = bridge[lo] & bridge[hi]
        self.route_m = np.interp(f, idx, node_s)
        z_lin = np.interp(f, idx, nodes[:, 2])
        self.z = gaussian_filter1d(z_lin, 4.0 / STEP, mode="nearest")    # bridge decks included: no kink at their ends
        self.dev = float(np.abs(self.z - z_lin).max())
        self.hw0 = gaussian_filter1d(np.interp(f, idx, widths), 3.0 / STEP, mode="nearest") / 2
        self.widening = self._widening(self.hw0)
        self.hw = self.hw0 + self.widening
        self._frames()
        self._round_kinks()
        self.tree = cKDTree(self.xy)
        self.verge = {side: self._verge(side) for side in (1, -1)}

    def _widening(self, hw):
        """Extra half-width: widen_m / 2, less where another leg of the climb would come within
        LEG_GAP of this one's asphalt or a building (or a hand-built landmark's wall, gate or
        fence) within HOUSE_GAP; smoothed along the road."""
        import shapely
        from shapely.geometry import shape
        add = np.full(len(self.s), widen_m() / 2)
        tree = cKDTree(self.xy)
        for i, js in enumerate(tree.query_ball_point(self.xy, 2 * hw.max() + 2 * add.max() + LEG_GAP)):
            js = np.asarray(js, int)
            js = js[np.abs(self.s[js] - self.s[i]) > 40]
            if len(js):
                room = (np.linalg.norm(self.xy[js] - self.xy[i], axis=1) - hw[i] - hw[js] - LEG_GAP).min() / 2
                add[i] = min(add[i], room)
        feats = json.loads((OUTPUT / "buildings_local.geojson").read_text())["features"]
        geoms = [shape(f["geometry"]) for f in feats] + landmark_points()
        st = shapely.STRtree(geoms)
        pts = shapely.points(self.xy)
        (pi, gi), dist = st.query_nearest(pts, max_distance=hw.max() + add.max() + HOUSE_GAP + 1,
                                          return_distance=True, all_matches=False)
        np.minimum.at(add, pi, dist - hw[pi] - HOUSE_GAP)
        add = np.maximum(add, 0.0)
        add = minimum_filter1d(add, int(16 / STEP), mode="nearest")
        return gaussian_filter1d(add, 3.0 / STEP, mode="nearest")

    def _frames(self):
        t = np.gradient(self.xy, STEP, axis=0)
        self.t = t / np.linalg.norm(t, axis=1, keepdims=True)
        self.n = np.stack([-self.t[:, 1], self.t[:, 0]], 1)            # left
        dt = np.gradient(self.t, STEP, axis=0)
        self.kappa = self.t[:, 0] * dt[:, 1] - self.t[:, 1] * dt[:, 0]  # > 0: turning left

    def _round_kinks(self, iters=300):
        """Where a node of the centreline is a corner too tight for the road's own width (the inner
        edge would fold), round it locally: smooth the stations within 4 m of it until the radius
        is at least (half-width + 0.3 m) / 0.9. Elsewhere the centreline is untouched."""
        need = (self.hw + 0.3) / 0.9
        reach = int(4.0 / STEP)
        for _ in range(iters):
            bad = np.abs(self.kappa) * need > 1.0
            if not bad.any():
                break
            near = np.convolve(bad.astype(float), np.ones(2 * reach + 1), "same") > 0
            smooth = gaussian_filter1d(self.xy, 1.0 / STEP, axis=0, mode="nearest")
            self.xy[near] = smooth[near]
            self._frames()

    def _verge(self, side):
        """Verge width on `side` (+1 left, -1 right): VERGE, less on the inside of tight bends and
        where another leg of the climb is close (then half the gap)."""
        k = self.kappa * side
        lim = np.full(len(self.s), np.inf)
        lim[k > 1e-6] = 0.9 / k[k > 1e-6]
        self.hw = np.minimum(self.hw, lim - 0.2)
        vw = np.minimum(VERGE, lim - self.hw - 0.1)
        edge = self.xy + side * self.n * self.hw[:, None]
        for i, js in enumerate(self.tree.query_ball_point(edge, 4.0 + VERGE)):
            js = np.asarray(js, int)
            js = js[np.abs(self.s[js] - self.s[i]) > 40]
            js = js[(self.xy[js] - edge[i]) @ (side * self.n[i]) > 0]
            if len(js):
                gap = np.min(np.linalg.norm(self.xy[js] - edge[i], axis=1) - self.hw[js])
                vw[i] = min(vw[i], gap / 2 - 0.05)
        return np.maximum(vw, 0.0)

    def locate(self, pts, k=8):
        """For points (n, 2): (lateral, station) from the closest point of the centreline polyline
        (searched on the segments next to the k nearest stations). lateral: signed distance, > 0 on
        the left; station: the station nearest to that closest point; -1 for points past either
        end of the road. (A cross-section test misses points on the outside of tight bends, where
        the 0.5 m sections fan apart.)"""
        pts = np.asarray(pts, float)
        n = len(self.s)
        _, js = self.tree.query(pts, k=k)
        best = np.full(len(pts), np.inf)
        lat = np.full(len(pts), np.inf)
        st = np.full(len(pts), -1)
        for c in range(k):
            for off in (-1, 0):
                j0 = np.clip(js[:, c] + off, 0, n - 2)
                a, b = self.xy[j0], self.xy[j0 + 1]
                d = b - a
                raw = np.einsum("ij,ij->i", pts - a, d) / np.maximum(np.einsum("ij,ij->i", d, d), 1e-12)
                t = np.clip(raw, 0.0, 1.0)
                v = pts - (a + t[:, None] * d)
                dist = np.linalg.norm(v, axis=1)
                past = ((j0 == 0) & (raw < 0) & (-raw * STEP > 0.3)) | ((j0 == n - 2) & (raw > 1) & ((raw - 1) * STEP > 0.3))
                side = np.sign(d[:, 0] * v[:, 1] - d[:, 1] * v[:, 0])
                better = ~past & (dist < best)
                best[better] = dist[better]
                lat[better] = np.where(side[better] == 0, 1.0, side[better]) * dist[better]
                st[better] = np.where(t[better] < 0.5, j0[better], j0[better] + 1)
        return lat, st

    def side_of(self, pts):
        """+1 / -1: which side of the road each point is on, and the nearest station."""
        lat, st = self.locate(pts, k=16)
        j = np.where(st >= 0, st, self.tree.query(pts)[1])
        lat = np.where(st >= 0, lat, np.einsum("ij,ij->i", pts - self.xy[j], self.n[j]))
        return np.where(lat >= 0, 1.0, -1.0), j

    def asphalt_quads(self):
        """The carriageway as quads between consecutive stations (n - 1, 4, 2)."""
        L = self.xy + self.n * self.hw[:, None]
        R = self.xy - self.n * self.hw[:, None]
        return np.stack([L[:-1], L[1:], R[1:], R[:-1]], 1)


# ----------------------------------------------------------------------------- mesh helpers
def vertex_normals(pos, tri):
    fn = np.cross(pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]])
    n = np.stack([sum(np.bincount(tri[:, c], fn[:, k], len(pos)) for c in range(3)) for k in range(3)], 1)
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    return n / np.where(ln > 0, ln, 1)


def _mesh(name, material, surface, pos, tri, uv, nrm=None):
    pos = np.asarray(pos, np.float64)
    tri = np.asarray(tri, np.int64)
    return dict(name=name, material=material, surface=surface, pos=pos, tri=tri,
                uv=np.asarray(uv, np.float64), nrm=vertex_normals(pos, tri) if nrm is None else nrm)


def _flat(m):
    """Unshared vertices per triangle (hard edges) with face normals."""
    t = m["tri"]
    P = m["pos"][t].reshape(-1, 3)
    fn = np.cross(P[1::3] - P[0::3], P[2::3] - P[0::3])
    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
    return dict(m, pos=P, uv=m["uv"][t].reshape(-1, 2), nrm=np.repeat(fn, 3, 0),
                tri=np.arange(len(P)).reshape(-1, 3))


def _upright_uv(m):
    """For a flat mesh: mirror u on the triangles whose normal-map 'up' (B = N x dP/du) would point
    down, so photo textures with normal maps light the right way on every face."""
    P, U, t = m["pos"], m["uv"], m["tri"]
    p0, p1, p2 = P[t[:, 0]], P[t[:, 1]], P[t[:, 2]]
    w0, w1, w2 = U[t[:, 0]], U[t[:, 1]], U[t[:, 2]]
    e1, e2, d1, d2 = p1 - p0, p2 - p0, w1 - w0, w2 - w0
    det = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    T = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) / np.where(np.abs(det) < 1e-12, 1e-12, det)[:, None]
    N = np.cross(e1, e2)
    B = np.cross(N, T)
    flip = (B[:, 2] < 0) & (np.abs(N[:, 2]) < 0.7 * np.linalg.norm(N, axis=1))
    U = U.copy()
    U[t[flip].ravel(), 0] *= -1
    return dict(m, uv=U)


def _outward(pos, tri, centre):
    """Flip triangles whose normal points towards their `centre` (one point per triangle)."""
    a, b, c = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
    fn = np.cross(b - a, c - a)
    inward = np.einsum("ij,ij->i", fn, (a + b + c) / 3 - centre) < 0
    tri[inward] = tri[inward][:, [0, 2, 1]]
    return tri


def strip_box(name, material, surface, a, b, z0, z1, along, tile=2.0):
    """Closed slab between polylines a and b (n, 2), from z0 to z1 (n,), end caps included. UVs in
    metres / tile: u along, v = -height (the top of the image up)."""
    n = len(a)
    ring = [(a, z0), (a, z1), (b, z1), (b, z0)]
    P = np.concatenate([np.concatenate([xy, z[:, None]], 1) for xy, z in ring])
    k = np.arange(n - 1)
    tri = []
    for f in range(4):
        i0, i1 = f * n, ((f + 1) % 4) * n
        tri += [np.stack([i0 + k, i1 + k, i1 + k + 1], 1), np.stack([i0 + k, i1 + k + 1, i0 + k + 1], 1)]
    tri.append(np.array([[0, n, 2 * n], [0, 2 * n, 3 * n]]))                       # start cap
    tri.append(np.array([[n - 1, 2 * n - 1, 3 * n - 1], [n - 1, 3 * n - 1, 4 * n - 1]]))   # end cap
    tri = np.concatenate(tri)
    axis = np.concatenate([(a + b) / 2, ((z0 + z1) / 2)[:, None]], 1)
    seg = np.concatenate([np.tile(k, 8), [0, 0, n - 2, n - 2]])
    centre = (axis[seg] + axis[seg + 1]) / 2
    centre[-4:-2] = axis[0] + np.r_[a[1] - a[0], 0] * 0.01      # caps face away from the strip
    centre[-2:] = axis[-1] - np.r_[a[-1] - a[-2], 0] * 0.01
    tri = _outward(P, tri, centre)
    h = np.concatenate([z0, z1, z1, z0])
    uv = np.stack([np.tile(along, 4) / tile, -h / tile], 1)
    m = _flat(_mesh(name, material, surface, P, tri, uv))
    # end caps: u across the slab (along is constant there)
    for c, (p0, p1) in enumerate(((a[0], b[0]), (a[-1], b[-1]))):
        d = (p1 - p0) / max(np.linalg.norm(p1 - p0), 1e-6)
        rows = np.arange((len(tri) - 4 + 2 * c) * 3, (len(tri) - 2 + 2 * c) * 3)
        m["uv"][rows, 0] = (m["pos"][rows, :2] - p0) @ d / tile
    return _upright_uv(m)


def box(c, d, hx, hy, z0, z1):
    """Vertical box centred on c (2,), long axis d (2,), half sizes hx (along d), hy."""
    d = np.asarray(d, float) / np.linalg.norm(d)
    e = np.array([-d[1], d[0]])
    corners = [c + d * hx + e * hy, c - d * hx + e * hy, c - d * hx - e * hy, c + d * hx - e * hy]
    P = np.array([[*q, z] for z in (z0, z1) for q in corners])
    tri = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
                    [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
    tri = _outward(P, tri, np.tile([*c, (z0 + z1) / 2], (len(tri), 1)))
    return P, tri


def merge(meshes, name, material, surface, **extra):
    if not meshes:
        return []
    off, tri = 0, []
    for m in meshes:
        tri.append(m["tri"] + off)
        off += len(m["pos"])
    m = dict(name=name, material=material, surface=surface, tri=np.concatenate(tri),
             **{k: np.concatenate([q[k] for q in meshes]) for k in ("pos", "uv", "nrm")}, **extra)
    return split(m)


def split(m, max_verts=MAX_VERTS):
    """Pieces of <= max_verts vertices, cutting the triangle list in order."""
    if len(m["pos"]) <= max_verts:
        return [m]
    out, start, tri = [], 0, m["tri"]
    while start < len(tri):
        lo, hi = start + 1, len(tri)
        while lo < hi:                                   # largest run that fits
            mid = (lo + hi + 1) // 2
            if len(np.unique(tri[start:mid])) <= max_verts:
                lo = mid
            else:
                hi = mid - 1
        keep, inv = np.unique(tri[start:lo].ravel(), return_inverse=True)
        out.append(dict(m, name=f"{m['name']}_{len(out)}", pos=m["pos"][keep], uv=m["uv"][keep],
                        nrm=m["nrm"][keep], tri=inv.reshape(-1, 3)))
        start = lo
    return out


def save_part(stem, meshes, materials, **extra):
    """output/ac/<stem>.npz (arrays) + <stem>.json (mesh list, material specs, extras) for export_ac.
    Meshes over MAX_VERTS are split. A material spec: {"shader", "textures": [[sampler, file in
    output/ac/textures or absolute, "DXT1" | "DXT5" | "PNG" | "DDS"]], "props": {...}, optional
    "alpha_tested", "blend", "depth"}."""
    out = []
    for m in meshes:
        out += split(m) if len(m["pos"]) > MAX_VERTS else [m]
    arrays, meta = {}, []
    for k, m in enumerate(out):
        for key in ("pos", "tri", "uv", "nrm"):
            arrays[f"m{k}_{key}"] = m[key]
        meta.append({"name": m["name"], "material": m["material"], "surface": m.get("surface"),
                     "verts": len(m["pos"]), "tris": len(m["tri"]),
                     **{k: m[k] for k in ("cast_shadows", "renderable", "lod_out", "transparent") if k in m}})
    missing = {m["material"] for m in meta} - set(materials)
    if missing:
        raise KeyError(f"{stem}: meshes use undefined materials {sorted(missing)[:5]}")
    AC_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(AC_DIR / f"{stem}.npz", **arrays)
    (AC_DIR / f"{stem}.json").write_text(json.dumps({"meshes": meta, "materials": materials, **extra}, indent=1))
    return meta


# ----------------------------------------------------------------------------- road, bridges
def survey_at(route_m):
    """Street View survey segment (streetview_segments.json) at each route metre (None outside)."""
    segs = json.loads((OUTPUT.parent / "streetview_segments.json").read_text())
    out = []
    for m in np.atleast_1d(route_m):
        out.append(next((s for s in segs if s["from_m"] <= m < s["to_m"]), None))
    return out


def strip_choice(road):
    """Texture strip per 45 m segment: a patched one where the survey saw a patched road, a worn one
    elsewhere (one in seven the other way), never the same strip twice in a row."""
    starts = np.arange(0, len(road.s) - 1, SEG_STATIONS)
    mids = road.route_m[np.minimum(starts + SEG_STATIONS // 2, len(road.s) - 1)]
    rng = np.random.default_rng(42)
    out, prev = [], -1
    for seg in survey_at(mids):
        patched = seg is not None and seg["surface"] == "patched"
        if rng.random() < 1 / 7:
            patched = not patched
        pool = [k for k in (tx.PATCHED_STRIPS if patched else tx.WORN_STRIPS) if k != prev]
        prev = int(rng.choice(pool))
        out.append(prev)
    return out


def road_meshes(road):
    """Carriageway, 5 vertices across per station (left to right), one texture strip per 45 m
    segment: u = (strip + q) / strips with q 0 at the left edge, 1 at the right; v = metres into
    the segment / 45. Normals from the whole road (no seam at segment ends). Meshes of ~1 km, all
    surface ROAD."""
    lat_f = np.array([1.0, 0.5, 0.0, -0.5, -1.0])
    lat = road.hw[:, None] * lat_f[None, :]
    xy = road.xy[:, None, :] + lat[..., None] * road.n[:, None, :]
    pos = np.concatenate([xy, (road.z[:, None] - CROWN * np.abs(lat))[..., None]], -1)
    n, w = len(road.s), len(lat_f)
    i = np.arange(n - 1)[:, None] * w + np.arange(w - 1)[None, :]
    grid_tri = np.concatenate([np.stack([i, i + 1, i + w + 1], -1).reshape(-1, 3),
                               np.stack([i, i + w + 1, i + w], -1).reshape(-1, 3)])   # counter-clockwise from above
    grid_nrm = vertex_normals(pos.reshape(-1, 3), grid_tri).reshape(n, w, 3)
    q = (1 - lat_f) / 2
    strips = strip_choice(road)
    meshes, per_mesh = [], 22
    starts = list(range(0, n - 1, SEG_STATIONS))
    for mk, m0 in enumerate(range(0, len(starts), per_mesh)):
        P, N, U, T, off = [], [], [], [], 0
        for si in starts[m0:m0 + per_mesh]:
            rows = np.arange(si, min(si + SEG_STATIONS, n - 1) + 1)
            k = strips[si // SEG_STATIONS]
            v = (road.s[rows] - road.s[si]) / tx.ROAD_SEG_M
            uv = np.stack([np.broadcast_to((k + q[None, :]) / tx.ROAD_STRIPS, (len(rows), w)),
                           np.broadcast_to(v[:, None], (len(rows), w))], -1)
            j = np.arange(len(rows) - 1)[:, None] * w + np.arange(w - 1)[None, :]
            T.append(np.concatenate([np.stack([j, j + 1, j + w + 1], -1).reshape(-1, 3),
                                     np.stack([j, j + w + 1, j + w], -1).reshape(-1, 3)]) + off)
            P.append(pos[rows].reshape(-1, 3))
            N.append(grid_nrm[rows].reshape(-1, 3))
            U.append(uv.reshape(-1, 2))
            off += len(rows) * w
        meshes.append(_mesh(f"1ROAD_{mk:02d}", "road", "ROAD", np.concatenate(P), np.concatenate(T),
                            np.concatenate(U), np.concatenate(N)))
    return meshes, pos


def bridge_meshes(road, pos):
    """Parapets (0.3 m thick, 0.9 m high, down to the 0.8 m slab) and the deck underside."""
    out = []
    idx = np.flatnonzero(road.bridge)
    for r in np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1) if len(idx) else []:
        r = np.arange(max(r[0] - 4, 0), min(r[-1] + 5, len(road.s)))
        L, R = pos[r, 0], pos[r, -1]
        for side, edge in ((1, L), (-1, R)):
            outer = edge[:, :2] + side * road.n[r] * 0.3
            out.append(strip_box(f"1WALL_bridge_{len(out)}", "wall_concrete", "WALL", edge[:, :2], outer,
                                 edge[:, 2] - 0.8, edge[:, 2] + 0.9, road.s[r], tx.WALLS["wall_concrete"][1]))
        n = len(r)
        P = np.concatenate([L - [0, 0, 0.8], R - [0, 0, 0.8]])
        k = np.arange(n - 1)
        tri = np.concatenate([np.stack([k, k + 1, n + k + 1], 1), np.stack([k, n + k + 1, n + k], 1)])  # facing down
        wdt = np.linalg.norm(L[:, :2] - R[:, :2], axis=1)
        uv = np.concatenate([np.stack([np.zeros(n), road.s[r] / 2], 1), np.stack([wdt / 2, road.s[r] / 2], 1)])
        out.append(_mesh(f"bridge_under_{len(out)}", "wall_concrete", None, P, tri, uv))
    return out


# ----------------------------------------------------------------------------- road markings
LINE_W = 0.12                 # m, white paint (Codice della strada: 12 cm on minor roads)
EDGE_INSET = 0.3              # m from the asphalt edge to the middle of an edge line
LINE_LIFT = 0.012             # m above the road surface (visual only; the car drives on the road mesh)
DASH, GAP = 4.5, 7.5          # m, centre line outside built-up areas


def marking_meshes(road):
    """White lines where the Street View survey saw them (edge_lines none / left / right / both,
    centre_line none / dashed / solid; a centre line only where the road is two lanes wide):
    quads following the road's crown, LINE_LIFT above it. u across the line, v along it
    (metres / 4), the paint texture is worn at random."""
    segs = survey_at(road.route_m)
    n = len(road.s)
    edge = {1: np.zeros(n, bool), -1: np.zeros(n, bool)}
    centre = np.full(n, "none", dtype=object)
    for i, sg in enumerate(segs):
        if sg is None:
            continue
        e = sg.get("edge_lines", "none")
        edge[1][i] = e in ("both", "left")
        edge[-1][i] = e in ("both", "right")
        if 2 * road.hw0[i] >= 5.2:
            centre[i] = sg.get("centre_line", "none")
    dash_on = (road.s % (DASH + GAP)) < DASH
    lines = []
    for side in (1, -1):
        lines.append((side * (road.hw - EDGE_INSET), edge[side]))
    lines.append((np.zeros(n), (centre == "solid") | ((centre == "dashed") & dash_on)))
    P, T, U, off = [], [], [], 0
    for lat, on in lines:
        on = on & (road.hw > 1.6)
        idx = np.flatnonzero(on)
        for run in np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1) if len(idx) else []:
            if len(run) < 2:
                continue
            r = np.r_[run, min(run[-1] + 1, n - 1)]
            a = lat[r] + LINE_W / 2
            b = lat[r] - LINE_W / 2
            pa = road.xy[r] + road.n[r] * a[:, None]
            pb = road.xy[r] + road.n[r] * b[:, None]
            za = road.z[r] - CROWN * np.abs(a) + LINE_LIFT
            zb = road.z[r] - CROWN * np.abs(b) + LINE_LIFT
            m = len(r)
            P.append(np.concatenate([np.c_[pa, za], np.c_[pb, zb]]))
            v = road.s[r] / 4.0
            U.append(np.concatenate([np.c_[np.zeros(m), v], np.c_[np.ones(m), v]]))
            k = np.arange(m - 1)
            T.append(np.concatenate([np.stack([k, k + m, k + m + 1], 1), np.stack([k, k + m + 1, k + 1], 1)]) + off)
            off += 2 * m
    if not P:
        return []
    P, T, U = np.concatenate(P), np.concatenate(T), np.concatenate(U)
    fn = np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]])
    flip = fn[:, 2] < 0
    T[flip] = T[flip][:, [0, 2, 1]]                                      # counter-clockwise from above
    m = _mesh("road_lines", "road_lines", None, P, T, U)
    m["cast_shadows"] = False
    print(f"markings: {np.sum(np.linalg.norm(np.diff(road.xy, axis=0), axis=1)[edge[1][:-1]]) / 1000:.1f} km left edge, "
          f"{np.sum(np.linalg.norm(np.diff(road.xy, axis=0), axis=1)[edge[-1][:-1]]) / 1000:.1f} km right edge, "
          f"centre line on {np.mean(centre != 'none'):.0%} of the road")
    return split(m)


# ----------------------------------------------------------------------------- terrain
def terrain_mesh(cfg, hf, road, road_pos):
    N, half = hf.n, cfg.half
    mask = np.zeros((N, N), bool)
    r, c = hf.to_grid(road.xy[:, 0], road.xy[:, 1])
    mask[np.clip(np.round(r).astype(int), 0, N - 1), np.clip(np.round(c).astype(int), 0, N - 1)] = True
    dist = distance_transform_edt(~mask).astype(np.float32) * cfg.mpp
    del mask
    rows = np.arange(N)[:, None]
    cols = np.arange(N)[None, :]
    keep = np.zeros((N, N), bool)
    lo = 0.0
    for hi, step in BANDS:
        keep |= (dist >= lo) & (dist < hi) & (rows % step == 0) & (cols % step == 0)
        lo = hi
    border = np.zeros((N, N), bool)
    border[:, [0, N - 1]] = True
    border[[0, N - 1], :] = True
    keep |= border & ((rows % 16 == 0) | (rows == N - 1)) & ((cols % 16 == 0) | (cols == N - 1))
    rr, cc = np.nonzero(keep)
    near = dist[rr, cc] < 12
    del dist, keep, border
    x = cc * cfg.mpp - half
    y = (N - 1 - rr) * cfg.mpp - half
    z = hf.z[rr, cc].astype(np.float64)

    # drop samples on the road + verge; under bridges only those close below the deck
    lat, st = road.locate(np.stack([x[near], y[near]], 1))
    j = np.where(st >= 0, st, 0)
    vw = np.where(lat >= 0, road.verge[1][j], road.verge[-1][j])
    reach = road.hw[j] + vw + 0.4
    edge_z = road.z[j] - CROWN * road.hw[j]
    # cuttings: ground rising more than CUT_RISE above the road just past the verge is pushed back
    cut = (np.abs(lat) < reach + CUT_CLEAR) & (z[near] > edge_z + CUT_RISE) & ~road.bridge[j]
    drop = (st >= 0) & ((np.abs(lat) < reach) | cut)
    brg = drop & road.bridge[j]
    drop[brg] = z[near][brg] > road.z[j[brg]] - 1.0
    ok = np.ones(len(x), bool)
    ok[np.flatnonzero(near)[drop]] = False
    pts = [np.stack([x[ok], y[ok], z[ok]], 1)]

    # road edges (shared with the carriageway) and verge rows; not on bridges
    nb = ~road.bridge
    for side, col in ((1, 0), (-1, -1)):
        edge = road_pos[nb, col]
        vw = road.verge[side][nb]
        nrm = side * road.n[nb]
        pts.append(edge)
        g = vw > GRAVEL + 0.05
        pts.append(np.concatenate([edge[g, :2] + nrm[g] * GRAVEL, edge[g, 2:] - 0.03], 1))
        g = vw > 0.05
        p = edge[g, :2] + nrm[g] * vw[g, None]
        zv = np.clip(hf.sample(p[:, 0], p[:, 1]), edge[g, 2] - VERGE_DROP, edge[g, 2] + VERGE_RISE)
        pts.append(np.concatenate([p, zv[:, None]], 1))
    P = np.concatenate(pts)
    print(f"terrain: {len(P):,} vertices, triangulating ...", flush=True)
    dt = Delaunay(P[:, :2])
    tri = dt.simplices.astype(np.int64)
    a, b, c3 = P[tri[:, 0], :2], P[tri[:, 1], :2], P[tri[:, 2], :2]
    cw = ((b[:, 0] - a[:, 0]) * (c3[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c3[:, 0] - a[:, 0])) < 0
    tri[cw] = tri[cw][:, [0, 2, 1]]                                   # counter-clockwise from above
    cen = P[tri].mean(1)
    lat, st = road.locate(cen[:, :2])
    j = np.where(st >= 0, st, 0)
    on = (st >= 0) & (np.abs(lat) < road.hw[j] + 0.05)
    on &= ~road.bridge[j] | (cen[:, 2] > road.z[j] - 0.5)
    vl, vs = road.locate(P[:, :2])
    vj = np.where(vs >= 0, vs, 0)
    v_on = (vs >= 0) & (np.abs(vl) < road.hw[vj] - 0.02) & ~road.bridge[vj]
    on |= v_on[tri].sum(1) >= 2
    tri = tri[~on]
    used = np.zeros(len(P), bool)
    used[tri] = True
    print(f"terrain: {len(tri):,} triangles ({(~used).sum()} unused vertices)", flush=True)

    def height(x, y):
        """Height of this terrain at (x, y), interpolated in its triangles."""
        q = np.stack([np.asarray(x, float), np.asarray(y, float)], -1).reshape(-1, 2)
        si = dt.find_simplex(q)
        T = dt.transform[si]
        b = np.einsum("nij,nj->ni", T[:, :2], q - T[:, 2])
        return (P[dt.simplices[si], 2] * np.c_[b, 1 - b.sum(1)]).sum(1)
    return P, tri, height


def classify_terrain(cfg, P, tri, road):
    """Per triangle: 0 far tile, 1 corridor tile, 2 rock face."""
    a, b, c = P[tri[:, 0]], P[tri[:, 1]], P[tri[:, 2]]
    fn = np.cross(b - a, c - a)
    slope = np.degrees(np.arccos(np.clip(np.abs(fn[:, 2]) / np.maximum(np.linalg.norm(fn, axis=1), 1e-12), 0, 1)))
    cen = (a + b + c) / 3
    rock_layer = np.asarray(Image.open(OUTPUT / "terrain_layers" / "rock.png").convert("L"))
    rr = np.clip(((cfg.half - cen[:, 1]) / cfg.mpp).astype(int), 0, rock_layer.shape[0] - 1)
    cc = np.clip(((cen[:, 0] + cfg.half) / cfg.mpp).astype(int), 0, rock_layer.shape[1] - 1)
    acfg = cfg.raw.get("assetto_corsa", {})
    rs, rsl = float(acfg.get("rock_slope", ROCK_SLOPE)), float(acfg.get("rock_slope_layer", ROCK_SLOPE_LAYER))
    rock = (slope >= rs) | ((slope >= rsl) & (rock_layer[rr, cc] > 100))
    d, _ = road.tree.query(cen[:, :2])
    kind = np.where(rock, 2, np.where(d < CORR_M, 1, 0))
    print(f"terrain: {np.mean(kind == 1):.1%} corridor, {np.mean(kind == 2):.2%} rock faces "
          f"({(kind == 2).sum():,} triangles)")
    return kind


def roughen_rock(P, tri, kind, road):
    """Relief on the rock faces (the DEM's 1 m triangles are smooth planes there): fractal noise in
    height only (the triangulation's x, y stay: ac_trees rebuilds it from the points), faded in
    from ROCK_RELIEF_CLEAR past the verge. Changes P in place; returns the number of vertices moved."""
    v = np.unique(tri[kind == 2].ravel())
    if not len(v):
        return 0
    lat, st = road.locate(P[v, :2])
    j = np.where(st >= 0, st, road.tree.query(P[v, :2])[1])
    edge = road.hw[j] + np.maximum(road.verge[1][j], road.verge[-1][j])
    gap = np.where(st >= 0, np.abs(lat), np.linalg.norm(P[v, :2] - road.xy[j], axis=1)) - edge
    w = np.clip((gap - ROCK_RELIEF_CLEAR) / ROCK_RELIEF_CLEAR, 0, 1)
    x, y = P[v, 0], P[v, 1]
    noise = 0.6 * tx.fbm(x, y, 4.0, 3, 701) + 0.4 * tx.fbm(x, y, 1.6, 2, 702)
    P[v, 2] += ROCK_RELIEF_M * noise * w
    return int((w > 0).sum())


def roughen_ground(P, tri, kind, road):
    """Natural relief on the rest of the ground: the 5 m LiDAR, Gaussian-smoothed, leaves fields and
    banks as smooth blankets. Height only (as roughen_rock), faded in from GROUND_RELIEF_CLEAR past
    the verge, none near buildings (their floors and the hand-built landmarks)."""
    import shapely
    from shapely.geometry import shape
    v = np.unique(tri[kind != 2].ravel())
    rock_v = np.zeros(len(P), bool)
    rock_v[tri[kind == 2].ravel()] = True
    v = v[~rock_v[v]]
    d, j = road.tree.query(P[v, :2])
    edge = road.hw[j] + np.maximum(road.verge[1][j], road.verge[-1][j])
    w = np.clip((d - edge - GROUND_RELIEF_CLEAR) / GROUND_RELIEF_CLEAR, 0, 1)
    feats = json.loads((OUTPUT / "buildings_local.geojson").read_text())["features"]
    geoms = [shape(f["geometry"]) for f in feats] + landmark_points()
    st = shapely.STRtree(geoms)
    pi, dist = st.query_nearest(shapely.points(P[v, :2]), max_distance=2 * HOUSE_RELIEF_CLEAR,
                                return_distance=True, all_matches=False)
    near = np.full(len(v), 2 * HOUSE_RELIEF_CLEAR)
    np.minimum.at(near, pi[0], dist)
    w *= np.clip((near - HOUSE_RELIEF_CLEAR) / HOUSE_RELIEF_CLEAR, 0, 1)
    x, y = P[v, 0], P[v, 1]
    a, b = GROUND_RELIEF_M
    P[v, 2] += (a * tx.fbm(x, y, 7.0, 2, 711) + b * tx.fbm(x, y, 2.0, 2, 712)) * w
    return int((w > 0).sum())


def far_chunks(cfg, P, tri, nrm):
    """Chunks of <= MAX_VERTS inside each TILE; UVs into that tile's texture (with margin)."""
    half = cfg.half
    n_t = int(2 * half // TILE)
    cen = P[tri].mean(1)
    ti = np.clip(((cen[:, 0] + half) // TILE).astype(int), 0, n_t - 1)
    tj = np.clip(((half - cen[:, 1]) // TILE).astype(int), 0, n_t - 1)       # rows from the north
    span = TILE + 2 * TILE_MARGIN
    out = []

    def emit(sel, i, j):
        keep, inv = np.unique(tri[sel].ravel(), return_inverse=True)
        p = P[keep]
        u = (p[:, 0] + half - (i * TILE - TILE_MARGIN)) / span
        v = (half - p[:, 1] - (j * TILE - TILE_MARGIN)) / span
        out.append(dict(name=f"1GRASS_{i}{j}_{len(out):03d}", material=f"terrain_{i}{j}", surface="GRASS",
                        pos=p, tri=inv.reshape(-1, 3), uv=np.stack([u, v], 1), nrm=nrm[keep]))

    def rec(sel, x0, y0, size, i, j):
        if not len(sel):
            return
        if size <= 64 or len(np.unique(tri[sel])) <= MAX_VERTS:
            emit(sel, i, j)
            return
        hs = size / 2
        east, north = cen[sel, 0] >= x0 + hs, cen[sel, 1] >= y0 + hs
        for qx in (False, True):
            for qy in (False, True):
                rec(sel[(east == qx) & (north == qy)], x0 + qx * hs, y0 + qy * hs, hs, i, j)

    for i in range(n_t):
        for j in range(n_t):
            rec(np.flatnonzero((ti == i) & (tj == j)), i * TILE - half, half - (j + 1) * TILE, TILE, i, j)
    return out


def corridor_chunks(cfg, P, tri, nrm):
    """One mesh per CORR_TILE tile; tiles packed ATLAS x ATLAS per material (terrain_c<k>).
    Returns meshes and the tile list [(ci, cj, atlas, sx, sy)]."""
    half = cfg.half
    cen = P[tri].mean(1)
    ci = ((cen[:, 0] + half) // CORR_TILE).astype(int)
    cj = ((half - cen[:, 1]) // CORR_TILE).astype(int)
    tiles = sorted(set(zip(ci.tolist(), cj.tolist())), key=lambda t: (t[1], t[0]))
    span = CORR_TILE + 2 * CORR_MARGIN
    out, table = [], []
    for k, (i, j) in enumerate(tiles):
        atlas, slot = divmod(k, ATLAS * ATLAS)
        sx, sy = slot % ATLAS, slot // ATLAS
        table.append((i, j, atlas, sx, sy))
        sel = np.flatnonzero((ci == i) & (cj == j))
        keep, inv = np.unique(tri[sel].ravel(), return_inverse=True)
        p = P[keep]
        u = ((p[:, 0] + half - (i * CORR_TILE - CORR_MARGIN)) / span + sx) / ATLAS
        v = ((half - p[:, 1] - (j * CORR_TILE - CORR_MARGIN)) / span + sy) / ATLAS
        out.append(dict(name=f"1GRASS_c{atlas:02d}_{k:04d}", material=f"terrain_c{atlas:02d}", surface="GRASS",
                        pos=p, tri=inv.reshape(-1, 3), uv=np.stack([u, v], 1), nrm=nrm[keep]))
    return out, table


def rock_chunks(P, tri, nrm):
    """Steep faces with the limestone photo box-mapped (per triangle the axis its normal is closest
    to; u signed so that the normal map's up points up), one mesh per block and axis."""
    a, b, c = P[tri[:, 0]], P[tri[:, 1]], P[tri[:, 2]]
    fn = np.cross(b - a, c - a)
    ax = np.argmax(np.abs(fn), 1)
    sg = np.sign(fn[np.arange(len(fn)), ax])
    group = ax * 2 + (sg < 0)
    cen = (a + b + c) / 3
    blk = np.floor(cen[:, :2] / ROCK_BLOCK).astype(int)
    T = tx.ROCK_TILE_M
    proj = {0: lambda p: (p[:, 1], -p[:, 2]), 1: lambda p: (-p[:, 1], -p[:, 2]),    # +x, -x
            2: lambda p: (-p[:, 0], -p[:, 2]), 3: lambda p: (p[:, 0], -p[:, 2]),    # +y, -y
            4: lambda p: (p[:, 0], -p[:, 1]), 5: lambda p: (p[:, 0], p[:, 1])}      # +z, -z
    out = []
    keys = np.unique(np.c_[blk, group], axis=0)
    for bx, by, g in keys:
        sel = np.flatnonzero((blk[:, 0] == bx) & (blk[:, 1] == by) & (group == g))
        keep, inv = np.unique(tri[sel].ravel(), return_inverse=True)
        p = P[keep]
        u, v = proj[int(g)](p)
        out.append(dict(name=f"1GRASS_rock_{len(out):04d}", material="rock_face", surface="GRASS", pos=p,
                        tri=inv.reshape(-1, 3), uv=np.stack([u / T, v / T], 1), nrm=nrm[keep]))
    return out


# ----------------------------------------------------------------------------- walls, guardrails
def hairpins():
    a = json.loads((OUTPUT / "audit_climb.json").read_text())
    return np.array([b["route_m"] for b in a["bends"] if b["turn_deg"] >= HAIRPIN_TURN], float)


def wall_material(kind, route_m):
    """Board-formed concrete, mortared stone (where the survey saw a 'stone_wall') or dry-stone."""
    if "concrete" in kind:
        return "wall_concrete"
    seg = survey_at([route_m])[0]
    sides = (seg["left"], seg["right"]) if seg else ()
    return "wall_mortared" if "stone_wall" in sides and "drystone_wall" not in sides else "wall_drystone"


def wall_meshes(road, height):
    hp = hairpins()
    out, removed, moved = [], 0.0, 0.0
    for it in json.loads((OUTPUT / "boundaries.json").read_text()):
        if not it["kind"].startswith(("wall_", "retain_")):
            continue
        p = np.asarray(it["pts"], float)
        if len(p) < 2:
            continue
        seg = np.linalg.norm(np.diff(p[:, :2], axis=0), axis=1)
        sp = np.concatenate([[0], np.cumsum(seg)])
        a, b = it["route_m"]
        rm = a + (b - a) * sp / max(sp[-1], 1e-6)
        ok = np.min(np.abs(rm[:, None] - hp[None, :]), axis=1) > HAIRPIN_CLEAR
        removed += float(seg[~(ok[:-1] & ok[1:])].sum())
        idx = np.flatnonzero(ok)
        mat = wall_material(it["kind"], (a + b) / 2)
        for run in np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1) if len(idx) else []:
            if len(run) < 2:
                continue
            q = p[run].copy()
            sgn, j = road.side_of(q[:, :2])
            away = road.n[j] * sgn[:, None]
            lat, st = road.locate(q[:, :2])
            short = (st >= 0) & (np.abs(lat) < road.hw[j] + WALL_CLEAR)       # too close to the asphalt
            q[short, :2] += away[short] * (road.hw[j[short]] + WALL_CLEAR - np.abs(lat[short]))[:, None]
            moved = max(moved, float(np.max(road.hw[j] + WALL_CLEAR - np.abs(lat), where=short, initial=0)))
            base = np.minimum(q[:, 2], height(q[:, 0], q[:, 1])) - 0.4
            along = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(q[:, :2], axis=0), axis=1))])
            out.append(strip_box(f"1WALL_{len(out):03d}", mat, "WALL", q[:, :2], q[:, :2] + away * WALL_T, base,
                                 np.maximum(q[:, 3], base + 0.6), along, tx.WALLS[mat][1]))
    print(f"walls: {len(out)} runs kept, {removed:.0f} m removed within {HAIRPIN_CLEAR:.0f} m of {len(hp)} hairpins; "
          f"pushed back up to {moved:.2f} m to stay {WALL_CLEAR} m off the asphalt")
    return out + fence_kerb_meshes(road, height)


FENCE_POST_M = 2.2            # timber fence: posts this far apart, two rails
KERB_H = 0.22


def fence_kerb_meshes(road, height):
    """The surveyed fences (timber posts and two rails, boundaries.json kind "fence") and low
    concrete kerbs ("concrete_kerb"), kept WALL_CLEAR off the asphalt and seated on the AC
    ground, both physics (WALL)."""
    fences, kerbs, total = [], [], {"fence": 0.0, "concrete_kerb": 0.0}
    for it in json.loads((OUTPUT / "boundaries.json").read_text()):
        if it["kind"] not in total:
            continue
        q = np.asarray(it["pts"], float)[:, :3]
        if len(q) < 2:
            continue
        sgn, j = road.side_of(q[:, :2])
        away = road.n[j] * sgn[:, None]
        lat, st = road.locate(q[:, :2])
        short = (st >= 0) & (np.abs(lat) < road.hw[j] + WALL_CLEAR)
        q[short, :2] += away[short] * (road.hw[j[short]] + WALL_CLEAR - np.abs(lat[short]))[:, None]
        q[:, 2] = height(q[:, 0], q[:, 1])
        along = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(q[:, :2], axis=0), axis=1))])
        total[it["kind"]] += float(along[-1])
        if it["kind"] == "concrete_kerb":
            kerbs.append(strip_box("kerb", "wall_concrete", "WALL", q[:, :2], q[:, :2] + away * 0.15,
                                   q[:, 2] - 0.15, q[:, 2] + KERB_H, along, tx.WALLS["wall_concrete"][1]))
            continue
        for zr in (0.45, 0.95):                                       # rails
            a = q[:, :2] + away * 0.02
            fences.append(strip_box("rail", "timber", "WALL", a, a + away * 0.05, q[:, 2] + zr - 0.04,
                                    q[:, 2] + zr + 0.04, along, 1.0))
        for u in np.arange(0.0, along[-1] + 1e-6, FENCE_POST_M):       # posts
            k = int(np.clip(np.searchsorted(along, u), 1, len(q) - 1))
            f = (u - along[k - 1]) / max(along[k] - along[k - 1], 1e-6)
            p = q[k - 1] + (q[k] - q[k - 1]) * f
            d = q[k, :2] - q[k - 1, :2]
            P, tri = box(p[:2] + away[k] * 0.07, d, 0.05, 0.05, p[2] - 0.3, p[2] + 1.05)
            m = _mesh("post", "timber", "WALL", P, tri, np.zeros((len(P), 2)))
            m = _flat(m)
            m["uv"] = np.c_[(m["pos"][:, 0] + m["pos"][:, 1]) * 2.0, m["pos"][:, 2]]     # grain up the post
            fences.append(m)
    print(f"fences {total['fence']:.0f} m, concrete kerbs {total['concrete_kerb']:.0f} m")
    # (a physics mesh name may hold one surface key only: "1WALL_kerb" read as KERB and WALL)
    return merge(fences, "1WALL_fence", "timber", "WALL") + merge(kerbs, "1WALL_lowedge", "wall_concrete", "WALL")


def w_beam(face, toward, z, along):
    """Visual W-beam along `face` (n, 2) (the rail's back plane), bulging `toward` (n, 2 unit) the road,
    bottom at z + 0.45. Front and back sheets; u along the rail, v down the profile."""
    prof = W_BEAM
    k = len(prof)
    n = len(face)
    t2 = np.zeros((n, 3))
    t2[:, :2] = toward
    up = np.array([0.0, 0.0, 1.0])
    base = np.concatenate([face, (z + 0.45)[:, None]], 1)
    P = base[:, None, :] + prof[None, :, 0:1] * t2[:, None, :] + prof[None, :, 1:2] * up
    # profile normals (in the cross-section: perpendicular to the profile, towards the road side)
    d = np.gradient(prof, axis=0)
    pn = np.stack([d[:, 1], -d[:, 0]], 1)
    pn /= np.linalg.norm(pn, axis=1, keepdims=True)
    pn *= np.where(pn[:, 0:1] < 0, -1.0, 1.0)
    N = pn[None, :, 0:1] * t2[:, None, :] + pn[None, :, 1:2] * up
    v = (prof[0, 1] - prof[:, 1]) / (prof[0, 1] - prof[-1, 1])
    U = np.stack([np.broadcast_to(along[:, None] / tx.GUARDRAIL_TILE_M, (n, k)), np.broadcast_to(v[None, :], (n, k))], -1)
    i = np.arange(n - 1)[:, None] * k + np.arange(k - 1)[None, :]
    tri = np.concatenate([np.stack([i, i + k, i + k + 1], -1).reshape(-1, 3), np.stack([i, i + k + 1, i + 1], -1).reshape(-1, 3)])
    P, N, U = P.reshape(-1, 3), N.reshape(-1, 3), U.reshape(-1, 2)
    fn = np.cross(P[tri[:, 1]] - P[tri[:, 0]], P[tri[:, 2]] - P[tri[:, 0]])
    if np.einsum("ij,ij->i", fn, N[tri[:, 0]]).mean() < 0:
        tri = tri[:, [0, 2, 1]]
    # u so that the image's up is up on the front sheet: B = N x T must point up
    T = np.zeros((n, 3))
    T[:, :2] = np.gradient(face, axis=0)
    if np.einsum("ij,j->i", np.cross(t2, T), up).mean() < 0:
        U[:, 0] *= -1
    back_P = P - np.repeat(t2, k, 0) * 0.003
    front = dict(pos=P, nrm=N, uv=U, tri=tri)
    back = dict(pos=back_P, nrm=-N, uv=U * [-1, 1], tri=tri[:, [0, 2, 1]])
    return front, back


def guardrail_meshes(road, height):
    p = np.array([i[1:4] for i in json.loads((OUTPUT / "climb_furniture.json").read_text())["items"]
                  if i[0] == "italy_guardrails_basic"], float)
    gaps = np.flatnonzero(np.linalg.norm(np.diff(p[:, :2], axis=0), axis=1) > 6.0) + 1
    rails, sheets, posts = [], [], []

    def clear(q):
        """Push points to RAIL_CLEAR off the asphalt, seat them on the AC ground; the road side."""
        sgn, j = road.side_of(q[:, :2])
        toward = -road.n[j] * sgn[:, None]                         # towards the road
        lat, st = road.locate(q[:, :2])
        short = (st >= 0) & (np.abs(lat) < road.hw[j] + RAIL_CLEAR)
        q[short, :2] -= toward[short] * (road.hw[j[short]] + RAIL_CLEAR - np.abs(lat[short]))[:, None]
        q[:, 2] = height(q[:, 0], q[:, 1])                          # on the AC ground
        return q, toward

    for run in np.split(np.arange(len(p)), gaps):
        if len(run) < 2:
            continue
        q, toward_post = clear(p[run].copy())
        # the rail itself every 1.5 m (straight chords between posts 4 m apart cut inside the bends)
        sp = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(q[:, :2], axis=0), axis=1))])
        si = np.linspace(0, sp[-1], max(int(np.ceil(sp[-1] / 1.5)) + 1, 2))
        r_ = np.stack([np.interp(si, sp, q[:, k]) for k in range(3)], 1)
        r_, toward = clear(r_)
        posts_q = q
        q = r_
        face = q[:, :2] + toward * 0.15
        along = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(face, axis=0), axis=1))])
        rail = strip_box("rail", "guardrail", "WALL", face - toward * 0.02, face - toward * 0.08,
                         q[:, 2] + 0.45, q[:, 2] + 0.76, along)
        rail["renderable"] = False
        rail["cast_shadows"] = False
        rails.append(rail)
        front, back = w_beam(face - toward * 0.08, toward, q[:, 2], along)
        sheets += [front, back]
        for k in range(len(posts_q)):                               # posts where the survey placed them
            pq, tw = posts_q[k], toward_post[k]
            P, tri = box(pq[:2] - tw * 0.12, tw, 0.05, 0.03, pq[2] - 0.3, pq[2] + 0.72)
            posts.append(_flat(_mesh("post", "guardrail_post", None, P, tri, np.zeros((len(P), 2)))))
    return (merge(rails, "1WALL_guardrail", "guardrail", "WALL", renderable=False, cast_shadows=False)
            + merge(sheets, "guardrail_beam", "guardrail", None)
            + merge(posts, "guardrail_posts", "guardrail_post", None))


# ----------------------------------------------------------------------------- textures, materials
def _periodic_noise(n, rng, beta=2.0):
    """Tileable fractal noise in [0, 1] (power spectrum ~ 1/f^beta)."""
    f = np.fft.fftfreq(n)
    fr = np.sqrt(f[:, None] ** 2 + f[None, :] ** 2)
    fr[0, 0] = 1
    spec = (rng.normal(size=(n, n)) + 1j * rng.normal(size=(n, n))) / fr ** (beta / 2)
    spec[0, 0] = 0
    a = np.real(np.fft.ifft2(spec))
    return (a - a.min()) / (np.ptp(a) + 1e-9)


def resize_bands(im, size):
    """Resize each channel on its own: Pillow resizes RGBA premultiplied, which wipes the colour
    wherever alpha is 0 (our masks and specular maps use alpha as data, not opacity)."""
    return Image.merge(im.mode, [b.resize(size, Image.LANCZOS) for b in im.split()])


_WORK = {}                    # shared with the forked painters


def _paint_far(ij):
    i, j = ij
    half, span = _WORK["half"], TILE + 2 * TILE_MARGIN
    col, w = _WORK["painter"].paint(i * TILE - half - TILE_MARGIN, half - j * TILE + TILE_MARGIN, span, TILE_PX)
    tx.save(col, f"terrain_{i}{j}.png")
    tx.save(w, f"terrain_mask_{i}{j}.png", "RGBA")
    return ij


def _paint_corridor(ij):
    i, j = ij
    half, span = _WORK["half"], CORR_TILE + 2 * CORR_MARGIN
    col, w = _WORK["painter"].paint(i * CORR_TILE - half - CORR_MARGIN, half - j * CORR_TILE + CORR_MARGIN, span,
                                    CORR_PX, _WORK["quads"])
    base = Image.fromarray((np.clip(col, 0, 1) * 255).round().astype(np.uint8)).resize((CORR_BASE_PX, CORR_BASE_PX), Image.LANCZOS)
    return ij, np.asarray(base), (np.clip(w, 0, 1) * 255).round().astype(np.uint8)


def make_terrain_textures(cfg, hf, road, tiles_far, corridor_table, workers=8):
    """terrain_<i><j>.png / terrain_mask_<i><j>.png (far tiles) and terrain_c<k>.png /
    terrain_cmask_<k>.png (corridor atlases), painted by `workers` forked processes."""
    import multiprocessing as mp
    means = tx.make_terrain_details()
    _WORK.update(painter=tx.TerrainPainter(cfg, hf, means), half=cfg.half, quads=road.asphalt_quads())
    table = sorted(corridor_table, key=lambda t: (t[2], t[4], t[3]))
    slot = {(t[0], t[1]): t[2:] for t in table}
    left = {}
    for t in table:
        left[t[2]] = left.get(t[2], 0) + 1
    atlases = {}
    with mp.get_context("fork").Pool(workers) as pool:
        for k, _ in enumerate(pool.imap_unordered(_paint_far, [tuple(t) for t in tiles_far])):
            if (k + 1) % 16 == 0:
                print(f"  far tiles {k + 1}/{len(tiles_far)}", flush=True)
        for (ij, base, mask) in pool.imap(_paint_corridor, [(t[0], t[1]) for t in table]):
            a, sx, sy = slot[ij]
            if a not in atlases:
                atlases[a] = (np.zeros((ATLAS * CORR_BASE_PX, ATLAS * CORR_BASE_PX, 3), np.uint8),
                              np.zeros((ATLAS * CORR_PX, ATLAS * CORR_PX, 4), np.uint8))
                atlases[a][1][..., 0] = 255
            atlases[a][0][sy * CORR_BASE_PX:(sy + 1) * CORR_BASE_PX, sx * CORR_BASE_PX:(sx + 1) * CORR_BASE_PX] = base
            atlases[a][1][sy * CORR_PX:(sy + 1) * CORR_PX, sx * CORR_PX:(sx + 1) * CORR_PX] = mask
            left[a] -= 1
            if not left[a]:
                b, m = atlases.pop(a)
                TEX_DIR.mkdir(parents=True, exist_ok=True)
                Image.fromarray(b).save(TEX_DIR / f"terrain_c{a:02d}.png")
                Image.fromarray(m, "RGBA").save(TEX_DIR / f"terrain_cmask_{a:02d}.png")
                print(f"  corridor atlas {a + 1}/{max(left) + 1}", flush=True)


def materials_spec(far_tiles, n_atlas):
    road = {"ksAmbient": 0.45, "ksDiffuse": 0.72, "ksSpecular": 0.15, "ksSpecularEXP": 28.0,
            "fresnelC": 0.03, "fresnelEXP": 4.0, "fresnelMaxLevel": 0.18, "isAdditive": 0.0, "nmObjectSpace": 0.0,
            "useDetail": 1.0, "detailUVMultiplier": float(tx.ROAD_DETAIL_MULT), "detailNormalBlend": 0.6,
            "sunSpecular": 0.4, "sunSpecularEXP": 14.0}           # less sun glare than an earlier build (0.22 / 0.6)
    rock = road | {"ksSpecular": 0.12, "ksSpecularEXP": 14.0, "fresnelMaxLevel": 0.1, "ksAmbient": 0.5, "ksDiffuse": 0.75,
                   "detailUVMultiplier": float(tx.ROCK_DETAIL_MULT), "detailNormalBlend": 0.5, "sunSpecular": 0.3}
    plain = {"ksAmbient": 0.5, "ksDiffuse": 0.75, "ksSpecular": 0.05, "ksSpecularEXP": 8.0}
    mats = {
        "road": {"shader": "ksPerPixelMultiMap_NMDetail", "props": road,
                 "textures": [["txDiffuse", "road_macro.png", "DXT5"], ["txNormal", "road_macro_nm.png", "DXT1"],
                              ["txMaps", "road_macro_maps.png", "DXT1"], ["txDetail", "road_detail.png", "DXT1"],
                              ["txNormalDetail", "road_detail_nm.png", "DXT1"]]},
        "rock_face": {"shader": "ksPerPixelMultiMap_NMDetail", "props": rock,
                      "textures": [["txDiffuse", "rock_face.png", "DXT5"], ["txNormal", "rock_face_nm.png", "DXT1"],
                                   ["txMaps", "rock_face_maps.png", "DXT1"], ["txDetail", "rock_detail.png", "DXT1"],
                                   ["txNormalDetail", "rock_detail_nm.png", "DXT1"]]},
        "guardrail": {"shader": "ksPerPixel", "props": plain | {"ksSpecular": 0.45, "ksSpecularEXP": 30.0},
                      "textures": [["txDiffuse", "guardrail.png", "DXT1"]]},
        "guardrail_post": {"shader": "ksPerPixel", "props": plain | {"ksSpecular": 0.35, "ksSpecularEXP": 25.0},
                           "textures": [["txDiffuse", "guardrail_post.png", "DXT1"]]},
        "steel_dark": {"shader": "ksPerPixel", "props": plain, "textures": [["txDiffuse", "guardrail_post.png", "DXT1"]]},
        "timber": {"shader": "ksPerPixel", "props": plain | {"ksSpecular": 0.03, "ksSpecularEXP": 6.0},
                   "textures": [["txDiffuse", "timber.png", "DXT1"]]},
        "road_lines": {"shader": "ksPerPixelAT", "alpha_tested": True,
                       "props": {"ksAmbient": 0.5, "ksDiffuse": 0.78, "ksSpecular": 0.12, "ksSpecularEXP": 20.0, "ksAlphaRef": 0.45},
                       "textures": [["txDiffuse", "road_lines.png", "DXT5"]]},
    }
    for m in tx.WALLS:
        mats[m] = {"shader": "ksPerPixelNM", "props": plain | {"ksSpecular": 0.06, "ksSpecularEXP": 10.0},
                   "textures": [["txDiffuse", f"{m}.png", "DXT1"], ["txNormal", f"{m}_nm.png", "DXT1"]]}
    ml = {"ksAmbient": 0.42, "ksDiffuse": 0.62, "ksSpecular": 0.04, "ksSpecularEXP": 8.0, "magicMult": tx.MAGIC_MULT,
          **{f"mult{c}": 1 / tx.TERRAIN_DETAIL[c][1] for c in "RGBA"}}
    details = [[f"txDetail{c}", f"terrain_detail_{c}.png", "DXT1"] for c in "RGBA"]
    for (i, j) in far_tiles:
        mats[f"terrain_{i}{j}"] = {"shader": "ksMultilayer", "props": ml,
                                   "textures": [["txDiffuse", f"terrain_{i}{j}.png", "DXT1"],
                                                {"sampler": "txMask", "file": f"terrain_mask_{i}{j}.png", "fmt": "DXT5",
                                                 "max_px": TILE_MASK_PX}] + details}
    for a in range(n_atlas):
        mats[f"terrain_c{a:02d}"] = {"shader": "ksMultilayer", "props": ml,
                                     "textures": [["txDiffuse", f"terrain_c{a:02d}.png", "DXT1"],
                                                  ["txMask", f"terrain_cmask_{a:02d}.png", "DXT5"]] + details}
    return mats


# ----------------------------------------------------------------------------- markers
def timed_section():
    """(start, finish) route metres of the timed run: config assetto_corsa.start_m, hillclimb.finish_m."""
    cfg = Config()
    hc = cfg["hillclimb"]
    return cfg.raw.get("assetto_corsa", {}).get("start_m", hc["start_m"]), hc["finish_m"]


def markers(road):

    def at(route_m, lat=0.0):
        i = int(np.argmin(np.abs(road.route_m - route_m)))
        p = road.xy[i] + road.n[i] * lat
        return dict(pos=[float(p[0]), float(p[1]), float(road.z[i] - CROWN * abs(lat))],
                    fwd=[float(road.t[i, 0]), float(road.t[i, 1]), 0.0], hw=float(road.hw[i]))

    s0, s1 = timed_section()
    w0, w1 = at(s0)["hw"] + 0.5, at(s1)["hw"] + 0.5
    out = {"AC_HOTLAP_START_0": at(s0 - 30),
           "AC_AB_START_L": at(s0, w0), "AC_AB_START_R": at(s0, -w0),
           "AC_AB_FINISH_L": at(s1, w1), "AC_AB_FINISH_R": at(s1, -w1)}
    # pit boxes / start slots on the road before the start line (assetto_corsa.pitboxes: one per
    # car online; AC_PIT_n and AC_START_n share a spot, numbered from 0 without gaps). Two
    # staggered columns where the asphalt is wide enough for two cars, else single file.
    n = max(1, int(Config().raw.get("assetto_corsa", {}).get("pitboxes", 1)))
    if n == 1:
        out.update({"AC_START_0": at(s0 - 8), "AC_PIT_0": at(s0 - 14)})
        return out
    first, room = s0 - 8.0, s0 - 8.0 - 6.0                     # the last slot stays 6 m inside the road's start
    hw = min(at(first - d)["hw"] for d in np.linspace(0, room, 8))
    two = hw >= 2.75 and room / max(n - 1, 1) >= 4.0
    step = min(4.5 if two else 7.0, room / (n - 1))
    if not two and step < 6.0:
        raise SystemExit(f"{n} pit boxes do not fit before the start line ({room:.0f} m of road {2 * hw:.1f} m wide): "
                         "fewer boxes, or a later hillclimb.start_m")
    lat = min(1.5, hw - 1.25) if two else 0.0
    for k in range(n):
        slot = at(first - k * step, lat if k % 2 == 0 else -lat)
        out[f"AC_START_{k}"] = out[f"AC_PIT_{k}"] = slot
    print(f"pit boxes: {n} ({'two staggered columns' if two else 'single file'}, {step:.1f} m apart, from "
          f"{first:.0f} m back to {first - (n - 1) * step:.0f} m; asphalt {2 * hw:.1f} m wide there)")
    return out


# ----------------------------------------------------------------------------- main
def main():
    cfg = Config()
    AC_DIR.mkdir(parents=True, exist_ok=True)
    hf = Heightfield(cfg, OUTPUT / "dem_final.tif")
    road = Road()
    kz = np.abs(np.gradient(np.gradient(road.z, STEP), STEP))[~road.bridge]
    s0, s1 = timed_section()
    tm = (road.route_m >= s0) & (road.route_m <= s1)
    print(f"road: {road.s[-1]:.0f} m, {len(road.s)} stations; heights moved <= {road.dev * 100:.1f} cm by smoothing; "
          f"vertical curvature max {kz.max():.4f} 1/m (p99 {np.percentile(kz, 99):.4f}); "
          f"verge < 0.5 m on {np.mean(road.verge[1] < 0.5):.1%} (L) / {np.mean(road.verge[-1] < 0.5):.1%} (R)")
    print(f"road width on the timed run: median {np.median(2 * road.hw[tm]):.2f} m (was {np.median(2 * road.hw0[tm]):.2f}), "
          f"min {np.min(2 * road.hw[tm]):.2f} m (was {np.min(2 * road.hw0[tm]):.2f}); widened by less than "
          f"{widen_m():.1f} m on {np.mean(2 * road.widening[tm] < widen_m() - 0.05):.1%} of it (other leg or house close)")
    meshes, road_pos = road_meshes(road)
    meshes += bridge_meshes(road, road_pos)
    meshes += marking_meshes(road)
    P, tri, height = terrain_mesh(cfg, hf, road, road_pos)
    kind = classify_terrain(cfg, P, tri, road)
    print(f"rock relief: {roughen_rock(P, tri, kind, road):,} vertices; ground relief: "
          f"{roughen_ground(P, tri, kind, road):,} vertices", flush=True)
    np.save(AC_DIR / "terrain_points.npy", P)          # ac_trees rebuilds the same triangulation from these
    nrm = vertex_normals(P, tri)
    far = far_chunks(cfg, P, tri[kind == 0], nrm)
    corr, table = corridor_chunks(cfg, P, tri[kind == 1], nrm)
    meshes += far + corr + rock_chunks(P, tri[kind == 2], nrm)
    meshes += wall_meshes(road, height)
    meshes += guardrail_meshes(road, height)
    far_tiles = sorted({tuple(int(c) for c in m["material"].split("_")[1]) for m in far})
    n_atlas = max(t[2] for t in table) + 1
    print(f"terrain: {len(far_tiles)} far tiles, {len(table)} corridor tiles in {n_atlas} atlases", flush=True)
    mk = markers(road)
    i0, i1 = (int(np.argmin(np.abs(road.route_m - m))) for m in (s0, s1))
    hp = hairpins()
    timed = {"length_m": round(float(road.s[i1] - road.s[i0])), "climb_m": round(float(road.z[i1] - road.z[i0])),
             "hairpins": int(((hp > s0) & (hp < s1)).sum()),
             "width_m": [round(float(np.percentile(2 * road.hw[tm], 5)), 1), round(float(np.percentile(2 * road.hw[tm], 95)), 1)]}
    print(f"timed run: {timed}")
    meta = save_part("geometry", meshes, materials_spec(far_tiles, n_atlas), markers=mk, timed=timed,
                     centreline=road.xy[::10].round(2).tolist())
    print(f"ac_track: {len(meta)} meshes, {sum(m['verts'] for m in meta):,} vertices, "
          f"{sum(m['tris'] for m in meta):,} triangles -> {AC_DIR}", flush=True)
    tiles = {"far": [list(t) for t in far_tiles], "corridor": [list(t) for t in table]}
    tiles_json = AC_DIR / "terrain_tiles.json"
    same = tiles_json.exists() and json.loads(tiles_json.read_text()) == tiles
    tiles_json.write_text(json.dumps(tiles))
    (AC_DIR / "preview_route.json").write_text(json.dumps(       # for ac_preview.py: the centreline's stations
        {"s": road.s[::10].round(2).tolist(), "route_m": road.route_m[::10].round(2).tolist(), "z": road.z[::10].round(3).tolist()}))
    if same and "--keep-textures" in __import__("sys").argv:
        print("ac_track: same terrain tiles, textures kept")
    else:
        textures(cfg, hf, road)


def textures(cfg=None, hf=None, road=None):
    """Every texture of the geometry part (road, rock, walls, terrain tiles from terrain_tiles.json)."""
    import os
    cfg = cfg or Config()
    hf = hf or Heightfield(cfg, OUTPUT / "dem_final.tif")
    road = road or Road()
    tiles = json.loads((AC_DIR / "terrain_tiles.json").read_text())
    road_png = TEX_DIR / "road_macro.png"
    if not road_png.exists() or road_png.stat().st_mtime < os.path.getmtime(tx.__file__):
        tx.make_road_textures()
        tx.make_rock_wall_textures()
    if not (TEX_DIR / "road_lines.png").exists() or (TEX_DIR / "road_lines.png").stat().st_mtime < os.path.getmtime(tx.__file__):
        tx.make_line_texture()
    if not (TEX_DIR / "timber.png").exists() or (TEX_DIR / "timber.png").stat().st_mtime < os.path.getmtime(tx.__file__):
        tx.make_timber_texture()
    make_terrain_textures(cfg, hf, road, [tuple(t) for t in tiles["far"]], [tuple(t) for t in tiles["corridor"]])
    print("ac_track: textures done", flush=True)


if __name__ == "__main__":
    import sys
    textures() if "--textures" in sys.argv else main()
