"""Fit the climb's centreline to the GeoDBT carriageway where the orthophoto cannot see the road.

Under trees OSM's line can be 5-15 m off the real road (it cuts the inside of hairpins); the
Regione Veneto GeoDBT traffic areas (AC_VEI) were surveyed stereoscopically and follow the real
road. Every station of a base line gets a lateral offset chosen by dynamic programming over
offsets OFF_MAX either side (OFF_STEP apart):

  cost = - min(signed distance into the carriageway, CENTRE_CAP)     (the middle of the road)
         + OUTSIDE * distance outside it
         + SEEN_W * (offset - orthophoto centre)^2   at stations the photo saw
         + ZERO_W * offset^2                         (weak pull to the base line)
         + SMOOTH * (offset change between stations)^2

Offsets past the centre of curvature on the inside of a bend are not allowed (the cross
sections would fold). The carriageway is AC_VEI zones 01xx plus junctions (0205) and
roundabouts (0206), rasterised at RES m in the corridor.
"""

import numpy as np
import shapely
from rasterio import features
from rasterio.transform import from_origin
from scipy.ndimage import distance_transform_edt, gaussian_filter1d, map_coordinates
from shapely.geometry import LineString

RES = 0.5
OFF_MAX, OFF_STEP = 15.0, 0.25
CENTRE_CAP = 4.0
OUTSIDE = 1.5
SEEN_W = 0.5
ZERO_W = 0.002
SMOOTH = 2.0


class SignedField:
    """Signed distance to the GeoDBT carriageway edge (m, > 0 inside) around a line."""

    def __init__(self, cfg, line_xy, pad=40.0):
        import geodbt
        corridor = LineString(line_xy).buffer(pad)
        rows = [g for g, z in geodbt.read("AC_VEI", corridor, cfg)
                if g.geom_type in ("Polygon", "MultiPolygon")
                and (z is None or str(z).startswith("01") or z in ("0205", "0206"))]
        self.ok = bool(rows)
        x0, y0, x1, y1 = corridor.bounds
        self.x0, self.y1 = x0, y1
        w, h = int(np.ceil((x1 - x0) / RES)), int(np.ceil((y1 - y0) / RES))
        if not self.ok:
            return
        inside = features.rasterize([(g, 1) for g in rows], out_shape=(h, w),
                                    transform=from_origin(x0, y1, RES, RES), dtype=np.uint8).astype(bool)
        self.f = (distance_transform_edt(inside) - distance_transform_edt(~inside)).astype(np.float32) * RES
        self.n_polys = len(rows)

    def sample(self, x, y):
        c = (np.asarray(x) - self.x0) / RES - 0.5
        r = (self.y1 - np.asarray(y)) / RES - 0.5
        return map_coordinates(self.f, [r, c], order=1, mode="nearest")


def fit_offsets(field, pts, nrm, seen_off=None, seen=None):
    """Lateral offset (m, along nrm) of the carriageway centre at every station of pts."""
    offs = np.arange(-OFF_MAX, OFF_MAX + 1e-6, OFF_STEP)
    n, k = len(pts), len(offs)
    gx = pts[:, 0:1] + nrm[:, 0:1] * offs[None, :]
    gy = pts[:, 1:2] + nrm[:, 1:2] * offs[None, :]
    f = field.sample(gx.ravel(), gy.ravel()).reshape(n, k)
    U = np.where(f > 0, -np.minimum(f, CENTRE_CAP), -OUTSIDE * f) + ZERO_W * offs[None, :] ** 2
    if seen is not None:
        U[seen] += SEEN_W * (offs[None, :] - seen_off[seen, None]) ** 2
    # no offsets past the centre of curvature on the inside of a bend
    t = np.gradient(gaussian_filter1d(pts, 2.0, axis=0, mode="nearest"), axis=0)
    t /= np.linalg.norm(t, axis=1, keepdims=True) + 1e-9
    ds = np.linalg.norm(np.gradient(pts, axis=0), axis=1) + 1e-9
    dt = np.gradient(t, axis=0)
    kappa = (t[:, 0] * dt[:, 1] - t[:, 1] * dt[:, 0]) / ds          # > 0: turning left (towards +nrm)
    reach = 0.9 / np.maximum(np.abs(kappa), 1e-6)
    fold = np.where(kappa[:, None] > 0, offs[None, :] > reach[:, None], offs[None, :] < -reach[:, None])
    U[fold] = 1e6
    # Viterbi
    trans = SMOOTH * (offs[:, None] - offs[None, :]) ** 2           # [from, to]
    acc = U[0].copy()
    back = np.zeros((n, k), np.int32)
    for i in range(1, n):
        tot = acc[:, None] + trans
        back[i] = np.argmin(tot, axis=0)
        acc = tot[back[i], np.arange(k)] + U[i]
    path = np.zeros(n, int)
    path[-1] = int(np.argmin(acc))
    for i in range(n - 1, 0, -1):
        path[i - 1] = back[i, path[i]]
    out = gaussian_filter1d(offs[path], 1.5, mode="nearest")
    inside = f[np.arange(n), path] > 0
    return out, inside
