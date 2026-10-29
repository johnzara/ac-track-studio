"""Drivable bends for the hill-climb line.

OSM draws hairpins and junction corners with 1-3 m centreline radii, which no car can drive
(the outer front wheel of a car turns on ~5.6 m). Every bend tighter than R_MIN is rebuilt:
  * corners up to ~115 degrees: a circular fillet of R_MIN tangent to both legs
  * hairpins: the shape of a real mountain hairpin, a loop of radius R_MIN at the apex with the
    two legs flaring out to it (cubic Hermite transitions), so the legs keep their position
    and the apex keeps its place
Input/output: polylines sampled every STEP metres.
"""

import numpy as np
from scipy.ndimage import gaussian_filter1d

STEP = 2.0
R_MIN = 6.5
HAIRPIN_DEG = 115.0
WINDOW = 15            # stations each side of the apex (30 m)


def _arc(c):
    return np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(c, axis=0), axis=1))])


def radius(c, sigma=1.0):
    sm = gaussian_filter1d(c, sigma, axis=0, mode="nearest")
    d1 = np.gradient(sm, axis=0)
    d2 = np.gradient(d1, axis=0)
    # parametrisation-invariant curvature (1/m)
    k = (d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]) / np.maximum(np.linalg.norm(d1, axis=1) ** 3, 1e-9)
    return 1 / np.maximum(np.abs(k), 1e-9), np.sign(k)


def _unit(v):
    return v / (np.linalg.norm(v) + 1e-12)


def _hermite(p0, m0, p1, m1, n):
    u = np.linspace(0, 1, n)[:, None]
    h00, h10 = 2 * u ** 3 - 3 * u ** 2 + 1, u ** 3 - 2 * u ** 2 + u
    h01, h11 = -2 * u ** 3 + 3 * u ** 2, u ** 3 - u ** 2
    return h00 * p0 + h10 * m0 + h01 * p1 + h11 * m1


def _arc_pts(C, r, a0, a1, n):
    a = np.linspace(a0, a1, n)
    return C + r * np.column_stack([np.cos(a), np.sin(a)])


def _fillet(Pa, da, Pb, db):
    """Straight - arc - straight between Pa (heading da) and Pb (arriving with heading db)."""
    A = np.column_stack([da, db])
    try:
        u, v = np.linalg.solve(A, Pb - Pa)          # Pa + da*u = V = Pb - db*v
    except np.linalg.LinAlgError:
        return None
    if u <= 1 or v <= 1:
        return None
    V = Pa + da * u
    theta = np.arccos(np.clip(da @ db, -1, 1))       # deflection
    if theta < np.radians(5):
        return None
    T = R_MIN * np.tan(theta / 2)
    T = min(T, u - 1.0, v - 1.0)
    r = T / np.tan(theta / 2)
    t1, t2 = V - da * T, V + db * T
    cross = da[0] * db[1] - da[1] * db[0]
    nrm = np.array([-da[1], da[0]]) * np.sign(cross)
    C = t1 + nrm * r
    a0, a1 = np.arctan2(*(t1 - C)[::-1]), np.arctan2(*(t2 - C)[::-1])
    da_ = (a1 - a0 + np.pi) % (2 * np.pi) - np.pi
    arc = _arc_pts(C, r, a0, a0 + da_, max(3, int(abs(da_) * r / 0.5)))
    return np.vstack([np.linspace(Pa, t1, max(2, int(np.linalg.norm(t1 - Pa) / 0.5))), arc,
                      np.linspace(t2, Pb, max(2, int(np.linalg.norm(Pb - t2) / 0.5)))]), r


def _hairpin(Pa, da, Pb, db, apex):
    """Loop of radius R_MIN around the apex, legs flaring out to it."""
    e = _unit(da - db)                               # towards the apex
    M = (Pa + Pb) / 2
    far = max((apex - M) @ e, R_MIN + 4.0)
    Apx = M + e * far
    C = Apx - e * R_MIN
    perp = np.array([-e[1], e[0]])
    sa = np.sign((Pa - M) @ perp) or 1.0
    Q1, Q2 = C + perp * sa * R_MIN, C - perp * sa * R_MIN
    if (Q1 - Pa) @ e < 6.0 or (Q2 - Pb) @ e < 6.0:
        return None
    L1, L2 = np.linalg.norm(Q1 - Pa), np.linalg.norm(Q2 - Pb)
    h1 = _hermite(Pa, da * L1, Q1, e * L1, max(4, int(L1 / 0.5)))
    a1 = np.arctan2(*(Q1 - C)[::-1])
    a2 = np.arctan2(*(Q2 - C)[::-1])
    amid = np.arctan2(*e[::-1])
    # go from Q1 to Q2 through the apex direction
    d = (a2 - a1) % (2 * np.pi)
    if not ((amid - a1) % (2 * np.pi) < d):
        d -= 2 * np.pi
    arc = _arc_pts(C, R_MIN, a1, a1 + d, max(6, int(abs(d) * R_MIN / 0.5)))
    h2 = _hermite(Q2, -e * L2, Pb, db * L2, max(4, int(L2 / 0.5)))
    return np.vstack([h1, arc[1:], h2[1:]]), R_MIN


def open_bends(c, protect=()):
    """c: (n, 2) stations STEP m apart. protect: [(i0, i1)] station ranges never changed
    (bridges). Returns the new stations (STEP m apart) and, for each, the arc position on the
    old line it came from (to carry widths and feature boundaries across)."""
    s_old = _arc(c)
    R, _ = radius(c)
    bad = R < R_MIN
    bad[:WINDOW + 2] = bad[-WINDOW - 2:] = False
    for i0, i1 in protect:
        bad[max(i0 - WINDOW, 0):i1 + WINDOW + 1] = False
    idx = np.where(bad)[0]
    clusters = []
    for i in idx:
        if clusters and i - clusters[-1][1] <= 8:
            clusters[-1][1] = i
        else:
            clusters.append([i, i])
    pieces, last_b = [], -1
    stats = {"fillet": 0, "hairpin": 0, "skipped": 0}
    for i, j in clusters:
        k = i + int(np.argmin(R[i:j + 1]))
        a, b = max(i - WINDOW, last_b + 2, 2), min(j + WINDOW, len(c) - 3)
        if b - a < 6:
            stats["skipped"] += 1
            continue
        Pa, Pb = c[a], c[b]
        da, db = _unit(c[a + 1] - c[a - 1]), _unit(c[b + 1] - c[b - 1])
        turn = np.degrees(np.arccos(np.clip(da @ db, -1, 1)))
        res = None
        if turn < HAIRPIN_DEG:
            res = _fillet(Pa, da, Pb, db)
            kind = "fillet"
        if res is None:
            seg = c[a:b + 1]
            apex = seg[np.argmax((seg - (Pa + Pb) / 2) @ _unit(da - db))]
            res = _hairpin(Pa, da, Pb, db, apex)
            kind = "hairpin"
        if res is None:
            stats["skipped"] += 1
            continue
        pieces.append((a, b, res[0]))
        stats[kind] += 1
        last_b = b
    if not pieces:
        return c.copy(), s_old.copy(), stats
    out, src = [], []
    prev = 0
    for a, b, new in pieces:
        out.append(c[prev:a])
        src.append(s_old[prev:a])
        sn = _arc(new)
        out.append(new)
        src.append(s_old[a] + (s_old[b] - s_old[a]) * sn / max(sn[-1], 1e-9))
        prev = b + 1
    out.append(c[prev:])
    src.append(s_old[prev:])
    P, S = np.vstack(out), np.concatenate(src)
    keep = np.concatenate([[True], np.linalg.norm(np.diff(P, axis=0), axis=1) > 1e-6])
    P, S = P[keep], S[keep]
    sp = _arc(P)
    tt = np.linspace(0, sp[-1], int(round(sp[-1] / STEP)) + 1)
    newc = np.column_stack([np.interp(tt, sp, P[:, 0]), np.interp(tt, sp, P[:, 1])])
    return newc, np.interp(tt, sp, S), stats
