/* Geometry helpers. Points are [lon, lat] (GeoJSON order) unless named otherwise.
   Distances in metres. "s" is the distance along the route from its first point. */
(function () {
  "use strict";
  const R = 6371008.8;
  const D2R = Math.PI / 180;

  function haversine(a, b) {
    const dLat = (b[1] - a[1]) * D2R, dLon = (b[0] - a[0]) * D2R;
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * D2R) * Math.cos(b[1] * D2R) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
  }

  /** Compass bearing a -> b in degrees (0 = north, 90 = east). */
  function bearing(a, b) {
    const f1 = a[1] * D2R, f2 = b[1] * D2R, dl = (b[0] - a[0]) * D2R;
    const y = Math.sin(dl) * Math.cos(f2);
    const x = Math.cos(f1) * Math.sin(f2) - Math.sin(f1) * Math.cos(f2) * Math.cos(dl);
    return (Math.atan2(y, x) / D2R + 360) % 360;
  }

  /** Point at `dist` metres from p on compass bearing `brg` (local flat approximation). */
  function offset(p, brg, dist) {
    const b = brg * D2R;
    const dn = Math.cos(b) * dist, de = Math.sin(b) * dist;
    return [p[0] + de / (R * Math.cos(p[1] * D2R)) / D2R, p[1] + dn / R / D2R];
  }

  /** Equirectangular local frame around ref: [x east, y north] in metres. Fine for a track-sized area. */
  function makeLocal(ref) {
    const k = Math.cos(ref[1] * D2R) * R * D2R, kn = R * D2R;
    return {
      fwd: (p) => [(p[0] - ref[0]) * k, (p[1] - ref[1]) * kn],
      inv: (q) => [ref[0] + q[0] / k, ref[1] + q[1] / kn],
    };
  }

  function cumulative(coords) {
    const cum = new Float64Array(coords.length);
    for (let i = 1; i < coords.length; i++) cum[i] = cum[i - 1] + haversine(coords[i - 1], coords[i]);
    return cum;
  }

  /** Index i with cum[i] <= s < cum[i+1] (binary search). */
  function segIndex(cum, s) {
    let lo = 0, hi = cum.length - 1;
    if (s <= 0) return 0;
    if (s >= cum[hi]) return hi - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (cum[mid] <= s) lo = mid; else hi = mid;
    }
    return lo;
  }

  /** Position and heading at distance s along the line. */
  function pointAt(coords, cum, s) {
    const n = coords.length;
    if (n < 2) return { p: coords[0], bearing: 0, i: 0 };
    s = Math.max(0, Math.min(cum[n - 1], s));
    const i = segIndex(cum, s);
    const a = coords[i], b = coords[i + 1];
    const len = cum[i + 1] - cum[i];
    const t = len > 0 ? (s - cum[i]) / len : 0;
    return { p: [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t], bearing: bearing(a, b), i };
  }

  /** Nearest point of the line to p: {s, dist, p, i}. */
  function project(coords, cum, p) {
    const loc = makeLocal(p);
    let best = { s: 0, dist: Infinity, p: coords[0], i: 0 };
    let prev = loc.fwd(coords[0]);
    for (let i = 0; i < coords.length - 1; i++) {
      const b = loc.fwd(coords[i + 1]);
      const dx = b[0] - prev[0], dy = b[1] - prev[1];
      const L2 = dx * dx + dy * dy;
      let t = L2 > 0 ? -(prev[0] * dx + prev[1] * dy) / L2 : 0;
      t = Math.max(0, Math.min(1, t));
      const qx = prev[0] + dx * t, qy = prev[1] + dy * t;
      const d = Math.hypot(qx, qy);
      if (d < best.dist) {
        best = { s: cum[i] + (cum[i + 1] - cum[i]) * t, dist: d, p: loc.inv([qx, qy]), i };
      }
      prev = b;
    }
    return best;
  }

  /** Points every `step` metres (the last point included). Returns {s:[], pts:[]}. */
  function resample(coords, cum, step) {
    const L = cum[cum.length - 1];
    const s = [], pts = [];
    const n = Math.max(1, Math.round(L / step));
    for (let k = 0; k <= n; k++) {
      const d = (L * k) / n;
      s.push(d);
      pts.push(pointAt(coords, cum, d).p);
    }
    return { s, pts };
  }

  /** Parallel line at `d` metres (positive = left of the travel direction). */
  function offsetLine(pts, d) {
    if (pts.length < 2) return pts.slice();
    const loc = makeLocal(pts[0]);
    const q = pts.map(loc.fwd);
    const out = [];
    for (let i = 0; i < q.length; i++) {
      const a = q[Math.max(0, i - 1)], b = q[Math.min(q.length - 1, i + 1)];
      const dx = b[0] - a[0], dy = b[1] - a[1];
      const L = Math.hypot(dx, dy) || 1;
      out.push(loc.inv([q[i][0] - (dy / L) * d, q[i][1] + (dx / L) * d]));
    }
    return out;
  }

  /** Signed circumradius of three local points (positive = left turn). */
  function circumradius(a, b, c) {
    const ab = Math.hypot(b[0] - a[0], b[1] - a[1]);
    const bc = Math.hypot(c[0] - b[0], c[1] - b[1]);
    const ca = Math.hypot(a[0] - c[0], a[1] - c[1]);
    const cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
    if (Math.abs(cross) < 1e-9) return Infinity;
    return (ab * bc * ca) / (2 * cross);
  }

  /** Corners of the line: stretches tighter than maxR, with apex radius, turn angle and direction.
      Radii are measured over a +-chord/2 window on a 5 m resampling (OSM corners are polygonal). */
  function corners(coords, cum, opts) {
    const o = Object.assign({ step: 5, chord: 20, maxR: 150, closed: false }, opts || {});
    const L = cum[cum.length - 1];
    if (L < o.chord * 2) return [];
    const rs = resample(coords, cum, o.step);
    const loc = makeLocal(rs.pts[0]);
    const q = rs.pts.map(loc.fwd);
    const n = q.length, k = Math.max(1, Math.round(o.chord / 2 / o.step));
    const rad = new Float64Array(n).fill(Infinity);
    const head = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      let ia = i - k, ic = i + k;
      if (o.closed) { ia = (ia + n - 1) % (n - 1); ic = ic % (n - 1); }
      else if (ia < 0 || ic >= n) continue;
      rad[i] = circumradius(q[ia], q[i], q[ic]);
    }
    for (let i = 0; i < n; i++) {
      const a = q[Math.max(0, i - 1)], b = q[Math.min(n - 1, i + 1)];
      head[i] = Math.atan2(b[0] - a[0], b[1] - a[1]) / D2R;
    }
    const out = [];
    let i = 0;
    while (i < n) {
      if (Math.abs(rad[i]) > o.maxR) { i++; continue; }
      const sign = Math.sign(rad[i]);
      let j = i;
      while (j + 1 < n && Math.abs(rad[j + 1]) <= o.maxR && Math.sign(rad[j + 1]) === sign) j++;
      let apex = i;
      for (let m = i; m <= j; m++) if (Math.abs(rad[m]) < Math.abs(rad[apex])) apex = m;
      let turn = 0;
      for (let m = Math.max(1, i - k); m <= Math.min(n - 1, j + k); m++) {
        let d = head[m] - head[m - 1];
        d = ((d + 540) % 360) - 180;
        turn += d;
      }
      const minR = Math.abs(rad[apex]);
      out.push({
        from_m: Math.round(rs.s[i]), to_m: Math.round(rs.s[j]), apex_m: Math.round(rs.s[apex]),
        radius_m: Math.round(minR * 10) / 10, turn_deg: Math.round(Math.abs(turn)),
        dir: sign > 0 ? "L" : "R",
        hairpin: Math.abs(turn) >= 130 && minR < 30,
        apex: rs.pts[apex],
      });
      i = j + 1;
    }
    // a short kink in the polygonal OSM line has a small radius but hardly turns the road
    return out.filter((c) => c.turn_deg >= 20);
  }

  /** Crossings between non-adjacent segments of a line (bridges, tunnels, figure-8s). */
  function selfCrossings(coords, cum) {
    if (coords.length < 4) return [];
    const loc = makeLocal(coords[0]);
    const q = coords.map(loc.fwd);
    const n = q.length - 1;
    // bucket segments in a 100 m grid to stay fast on long routes
    const cell = 100, grid = new Map();
    for (let i = 0; i < n; i++) {
      const x0 = Math.floor(Math.min(q[i][0], q[i + 1][0]) / cell), x1 = Math.floor(Math.max(q[i][0], q[i + 1][0]) / cell);
      const y0 = Math.floor(Math.min(q[i][1], q[i + 1][1]) / cell), y1 = Math.floor(Math.max(q[i][1], q[i + 1][1]) / cell);
      for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) {
        const key = x + "," + y;
        if (!grid.has(key)) grid.set(key, []);
        grid.get(key).push(i);
      }
    }
    const seen = new Set(), out = [];
    for (const list of grid.values()) {
      for (let a = 0; a < list.length; a++) for (let b = a + 1; b < list.length; b++) {
        const i = Math.min(list[a], list[b]), j = Math.max(list[a], list[b]);
        if (j - i < 2 || (i === 0 && j === n - 1 && haversine(coords[0], coords[n]) < 1)) continue;
        const key = i + ":" + j;
        if (seen.has(key)) continue;
        seen.add(key);
        const t = segIntersect(q[i], q[i + 1], q[j], q[j + 1]);
        if (t) {
          const p = loc.inv([q[i][0] + (q[i + 1][0] - q[i][0]) * t[0], q[i][1] + (q[i + 1][1] - q[i][1]) * t[0]]);
          out.push({ seg_a: i, seg_b: j, s_a: cum[i] + (cum[i + 1] - cum[i]) * t[0], s_b: cum[j] + (cum[j + 1] - cum[j]) * t[1], p });
        }
      }
    }
    return out.sort((a, b) => a.s_a - b.s_a);
  }

  function segIntersect(p1, p2, p3, p4) {
    const d = (p2[0] - p1[0]) * (p4[1] - p3[1]) - (p2[1] - p1[1]) * (p4[0] - p3[0]);
    if (Math.abs(d) < 1e-12) return null;
    const t = ((p3[0] - p1[0]) * (p4[1] - p3[1]) - (p3[1] - p1[1]) * (p4[0] - p3[0])) / d;
    const u = ((p3[0] - p1[0]) * (p2[1] - p1[1]) - (p3[1] - p1[1]) * (p2[0] - p1[0])) / d;
    if (t > 1e-6 && t < 1 - 1e-6 && u > 1e-6 && u < 1 - 1e-6) return [t, u];
    return null;
  }

  // ------------------------------------------------------------------------------------- UTM
  /** UTM zone for a lon/lat (with the Norway and Svalbard exceptions). */
  function utmZone(lon, lat) {
    let z = Math.floor((lon + 180) / 6) + 1;
    if (lat >= 56 && lat < 64 && lon >= 3 && lon < 12) z = 32;
    if (lat >= 72 && lat < 84) {
      if (lon >= 0 && lon < 9) z = 31; else if (lon >= 9 && lon < 21) z = 33;
      else if (lon >= 21 && lon < 33) z = 35; else if (lon >= 33 && lon < 42) z = 37;
    }
    return z;
  }

  /** WGS84 lon/lat -> UTM [E, N] in the given zone (Snyder's series, mm-level inside the zone). */
  function toUTM(lon, lat, zone, south) {
    const a = 6378137.0, f = 1 / 298.257223563, k0 = 0.9996;
    const e2 = f * (2 - f), ep2 = e2 / (1 - e2);
    const lon0 = ((zone - 1) * 6 - 180 + 3) * D2R;
    const phi = lat * D2R, lam = lon * D2R;
    const N = a / Math.sqrt(1 - e2 * Math.sin(phi) ** 2);
    const T = Math.tan(phi) ** 2, C = ep2 * Math.cos(phi) ** 2, A = Math.cos(phi) * (lam - lon0);
    const e4 = e2 * e2, e6 = e4 * e2;
    const M = a * ((1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
      - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * Math.sin(2 * phi)
      + (15 * e4 / 256 + 45 * e6 / 1024) * Math.sin(4 * phi)
      - (35 * e6 / 3072) * Math.sin(6 * phi));
    const E = k0 * N * (A + (1 - T + C) * A ** 3 / 6 + (5 - 18 * T + T * T + 72 * C - 58 * ep2) * A ** 5 / 120) + 500000;
    let Nn = k0 * (M + N * Math.tan(phi) * (A * A / 2 + (5 - T + 9 * C + 4 * C * C) * A ** 4 / 24
      + (61 - 58 * T + T * T + 600 * C - 330 * ep2) * A ** 6 / 720));
    if (south) Nn += 10000000;
    return [E, Nn];
  }

  /** Map frame for the pipeline: UTM zone, centre, terrain extent and grid. */
  function mapFrame(coords, marginM) {
    let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
    for (const p of coords) { w = Math.min(w, p[0]); e = Math.max(e, p[0]); s = Math.min(s, p[1]); n = Math.max(n, p[1]); }
    const c = [(w + e) / 2, (s + n) / 2];
    const south = c[1] < 0;
    const zone = utmZone(c[0], c[1]);
    const utm = coords.map((p) => toUTM(p[0], p[1], zone, south));
    let E0 = Infinity, E1 = -Infinity, N0 = Infinity, N1 = -Infinity;
    for (const [x, y] of utm) { E0 = Math.min(E0, x); E1 = Math.max(E1, x); N0 = Math.min(N0, y); N1 = Math.max(N1, y); }
    const centre = [Math.round((E0 + E1) / 20) * 10, Math.round((N0 + N1) / 20) * 10];
    const need = Math.max(E1 - E0, N1 - N0) + 2 * marginM;
    let mpp = 1, px = 1024;
    while (px * mpp < need && px < 8192) px *= 2;
    while (px * mpp < need && mpp < 4) mpp *= 2;
    return {
      epsg: (south ? 32700 : 32600) + zone,
      utm_zone: zone, hemisphere: south ? "S" : "N",
      center_utm: centre, center_lonlat: c.map((v) => +v.toFixed(6)),
      route_bbox_lonlat: [w, s, e, n].map((v) => +v.toFixed(6)),
      route_extent_m: [Math.round(E1 - E0), Math.round(N1 - N0)],
      margin_m: marginM,
      terrain_size_px: px, meters_per_px: mpp, extent_m: px * mpp,
      fits: px * mpp >= need,
    };
  }

  function slugify(s) {
    return (s || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase()
      .replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 48) || "track";
  }

  function fmtKm(m) { return m >= 1000 ? (m / 1000).toFixed(2) + " km" : Math.round(m) + " m"; }

  window.Geo = {
    R, haversine, bearing, offset, makeLocal, cumulative, segIndex, pointAt, project, resample,
    offsetLine, corners, selfCrossings, utmZone, toUTM, mapFrame, slugify, fmtKm,
  };
})();
