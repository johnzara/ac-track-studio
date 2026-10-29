"""GeoDBT carriageway widths across a road (ported from an earlier project's road_profiles.py).

The Regione Veneto GeoDBT traffic areas (AC_VEI, zone 01xx = the carriageway between the edge
lines, kerb to kerb in town) are measured across the road every node; config.json
"detail_areas" with measured_road_widths says where. trace_climb.py uses them for the climb
wherever the orthophoto cannot see the road (under trees).
"""

import numpy as np
import shapely
from scipy.ndimage import gaussian_filter1d, median_filter
from shapely import STRtree
from shapely.geometry import LineString, Point

STEP = 2.0


# measured widths (config.json detail_areas with measured_road_widths): the GeoDBT carriageway
# (AC_VEI zone 01xx, edge line to edge line) across the road every 2 m. Car parks, lay-bys and
# the yards and private roads that touch the carriageway in town (other zones) are not part of
# it; on junctions, squares and roundabouts (02xx) nothing is measured. Those nodes, gaps and
# spikes are filled from the neighbours, then the width is smoothed over ~10 m. A roundabout's
# own ring is measured across the roundabout polygon (its islands are holes). A road with no
# carriageway along it (private lanes the GeoDBT files as roads inside a property) is
# measured across that lane instead; where it crosses a whole yard nothing is kept.
# The two halves of a two-way road split by a painted median count as one road; more than
# MEASURED_REACH_M from any measurement (a long junction area, carriageway not found) the game
# width comes back.
MEASURED_MIN_M = 3.0
MEASURED_MAX_M = 13.0
MEASURED_REACH_M = 20.0
MEDIAN_GAP_M = 3.5
PRIVATE_MAX_M = 6.5         # a private polygon wider than this is a yard, not a lane
MEASURED_EXTRA_M = 0.2      # the game road reaches a hair beyond the painted / worn edge
SECTION_HALF_M = 8.0
CENTRE_TOL_M = 2.5          # the carriageway piece must lie within this of the OSM centreline


def carriageway_data(cfg):
    """{"region", "road", "junction", "ring", "private": (polygons, STRtree)} or None. A lot
    without zones (None) counts as carriageway."""
    import geodbt
    region = geodbt.area_union(cfg, "measured_road_widths")
    if region is None:
        return None
    rows = [(g, z) for g, z in geodbt.read("AC_VEI", region.buffer(60), cfg)
            if g.geom_type in ("Polygon", "MultiPolygon")]
    road = [g for g, z in rows if z is None or str(z).startswith("01")]
    junction = [g for g, z in rows if z is not None and str(z).startswith("02")]
    ring = road + [g for g, z in rows if z == "0206"]
    private = [g for g, z in rows if z == "0307"]
    if not road:
        return None
    return {"region": region, **{k: (v, STRtree(v)) for k, v in
                                 (("road", road), ("junction", junction), ("ring", ring), ("private", private))}}


def measured_widths(xy, cw, which="road", oneway=False):
    """(width at every node, weight of the measurement 0..1) or None when the road is not
    measured, across the polygons cw[which]. The weight fades in and out over ~30 m at the
    edge of the detail region."""
    from common import road_normals
    n = len(xy)
    region = cw["region"]
    inside = shapely.contains_xy(region, xy[:, 0], xy[:, 1]).astype(float)
    if n < 3 or not inside.any():
        return None
    polys, tree = cw[which]
    skip = np.zeros(n, bool)
    if which != "ring" and cw["junction"][0]:
        hit = cw["junction"][1].query(shapely.points(xy), predicate="intersects")
        skip[hit[0]] = True
    nrm = road_normals(xy)
    raw = np.full(n, np.nan)
    for k in np.where((inside > 0) & ~skip)[0]:
        a, b = xy[k] - nrm[k] * SECTION_HALF_M, xy[k] + nrm[k] * SECTION_HALF_M
        seg = LineString([a, b])
        iv = []
        for i in tree.query(seg):
            g = polys[i].intersection(seg)
            for piece in getattr(g, "geoms", [g]):
                if piece.geom_type == "LineString" and piece.length > 0.05:
                    t = sorted(seg.project(Point(q)) for q in piece.coords)
                    iv.append((t[0], t[-1]))
        if not iv:
            continue
        iv.sort()
        merged = [list(iv[0])]
        for t0, t1 in iv[1:]:                           # neighbouring polygons share edges
            if t0 <= merged[-1][1] + 0.1:
                merged[-1][1] = max(merged[-1][1], t1)
            else:
                merged.append([t0, t1])
        c = SECTION_HALF_M
        t0, t1 = min(merged, key=lambda q: 0.0 if q[0] <= c <= q[1] else min(abs(q[0] - c), abs(q[1] - c)))
        if not oneway:                                  # centreline on a median between both halves
            for (a0, a1), (b0, b1) in zip(merged, merged[1:]):
                if a1 < c < b0 and b0 - a1 <= MEDIAN_GAP_M:
                    t0, t1 = a0, b1
        if t0 < 0.05 or t1 > 2 * c - 0.05:
            continue                                    # runs past the section: not a width
        if min(abs(t0 - c), abs(t1 - c)) <= CENTRE_TOL_M or t0 <= c <= t1:
            raw[k] = t1 - t0
    ok = np.isfinite(raw) & (raw >= 2.0)
    if ok.sum() < max(5, 0.3 * ((inside > 0) & ~skip).sum()):
        return None
    med = float(np.median(raw[ok]))
    ok &= (raw <= 1.5 * med) & (raw <= (PRIVATE_MAX_M if which == "private" else MEASURED_MAX_M))
    if ok.sum() < 5:
        return None
    i = np.arange(n)
    idx = i[ok]
    w = np.interp(i, idx, raw[ok])
    w = gaussian_filter1d(median_filter(w, size=7, mode="nearest"), 2.5, mode="nearest")
    w = np.clip(w + MEASURED_EXTRA_M, MEASURED_MIN_M, MEASURED_MAX_M)
    j = np.clip(np.searchsorted(idx, i), 1, len(idx) - 1)
    gap = np.minimum(np.abs(i - idx[j - 1]), np.abs(idx[j] - i)) * STEP
    near = np.clip(2.0 - gap / MEASURED_REACH_M, 0.0, 1.0)
    return w, gaussian_filter1d(inside * near, 6.0, mode="nearest")
