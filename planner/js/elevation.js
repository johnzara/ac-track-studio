/* Elevation along the route, for the profile and the brief's numbers: Copernicus DEM GLO-90
   through api.open-meteo.com (no key, at most 100 points per call, CC BY 4.0). A 90 m surface
   model (trees and buildings included, nearest-cell values), so it is a planning preview only: the
   build uses the best local DTM (docs/DATA_SOURCES.md). Google's Elevation API is not used: its
   terms forbid terrain models from it and showing its values on a non-Google map. */
(function () {
  "use strict";
  const OPEN_METEO = "https://api.open-meteo.com/v1/elevation";

  async function openMeteo(pts, onStatus) {
    const out = [];
    for (let i = 0; i < pts.length; i += 100) {
      const chunk = pts.slice(i, i + 100);
      const lat = chunk.map((p) => p[1].toFixed(6)).join(",");
      const lon = chunk.map((p) => p[0].toFixed(6)).join(",");
      onStatus && onStatus(`Elevation ${Math.min(i + 100, pts.length)}/${pts.length}…`);
      let j;
      for (let attempt = 0; attempt < 4; attempt++) {
        const r = await fetch(`${OPEN_METEO}?latitude=${lat}&longitude=${lon}`);
        if (r.ok) { j = await r.json(); break; }
        if (attempt === 3) throw new Error(`Open-Meteo HTTP ${r.status}${r.status === 429 ? " (rate limit: try again in a minute)" : ""}`);
        // the free API limits coordinates per minute: back off for real on 429
        const wait = r.status === 429 ? 20 * (attempt + 1) : attempt + 1;
        for (let t = wait; t > 0; t--) {
          onStatus && onStatus(`Elevation: rate-limited, retrying in ${t} s…`);
          await new Promise((res) => setTimeout(res, 1000));
        }
      }
      out.push(...j.elevation);
      if (i + 100 < pts.length) await new Promise((res) => setTimeout(res, 120));
    }
    return out;
  }

  function smooth(z, s, sigmaM) {
    if (z.length < 3) return z.slice();
    const step = (s[s.length - 1] - s[0]) / (s.length - 1);
    const k = Math.max(1, Math.round(sigmaM / step));
    const w = [];
    for (let i = -3 * k; i <= 3 * k; i++) w.push(Math.exp(-0.5 * (i / k) ** 2));
    const out = new Array(z.length);
    for (let i = 0; i < z.length; i++) {
      let a = 0, b = 0;
      for (let j = -3 * k; j <= 3 * k; j++) {
        const m = i + j;
        if (m < 0 || m >= z.length) continue;
        a += z[m] * w[j + 3 * k]; b += w[j + 3 * k];
      }
      out[i] = a / b;
    }
    return out;
  }

  /** Grade (%) over a centred window of `win` metres. */
  function grades(zs, s, win) {
    const n = zs.length, out = new Array(n).fill(0);
    const step = (s[n - 1] - s[0]) / Math.max(1, n - 1);
    const k = Math.max(1, Math.round(win / 2 / step));
    for (let i = 0; i < n; i++) {
      const a = Math.max(0, i - k), b = Math.min(n - 1, i + k);
      out[i] = b > a ? ((zs[b] - zs[a]) / (s[b] - s[a])) * 100 : 0;
    }
    return out;
  }

  function stats(s, z, zs, win) {
    let gain = 0, loss = 0, ref = zs[0];
    for (const v of zs) {             // 2 m hysteresis: ignore noise
      if (v > ref + 2) { gain += v - ref; ref = v; } else if (v < ref - 2) { loss += ref - v; ref = v; }
    }
    const gw = grades(zs, s, win);
    let maxUp = { g: -Infinity, at: 0 }, maxDown = { g: Infinity, at: 0 };
    gw.forEach((g, i) => { if (g > maxUp.g) maxUp = { g, at: s[i] }; if (g < maxDown.g) maxDown = { g, at: s[i] }; });
    const L = s[s.length - 1];
    return {
      start_m: +z[0].toFixed(1), end_m: +z[z.length - 1].toFixed(1),
      min_m: +Math.min(...z).toFixed(1), max_m: +Math.max(...z).toFixed(1),
      gain_m: Math.round(gain), loss_m: Math.round(loss),
      avg_grade_pct: +(((zs[zs.length - 1] - zs[0]) / L) * 100).toFixed(2),
      grade_window_m: win,
      max_grade_pct: +maxUp.g.toFixed(1), max_grade_at_m: Math.round(maxUp.at),
      min_grade_pct: +maxDown.g.toFixed(1), min_grade_at_m: Math.round(maxDown.at),
    };
  }

  /** Profile along a route: {source, step_m, s, z, zs, grade, stats}. */
  async function profile(coords, cum, onStatus) {
    // nearest 90 m cell (steps): sampled coarsely (at most 300 points, which also keeps within the
    // free API's per-minute budget), smoothed, grades over 200 m
    const P = { min: 30, max: 300, sigma: 60, win: 200 };
    const L = cum[cum.length - 1];
    const step = Math.max(P.min, Math.ceil(L / P.max / 5) * 5);
    const rs = Geo.resample(coords, cum, step);
    const z = await openMeteo(rs.pts, onStatus);
    const zs = smooth(z, rs.s, P.sigma);
    return {
      source: "Open-Meteo / Copernicus GLO-90",
      step_m: step, grade_window_m: P.win, s: rs.s.map((v) => +v.toFixed(1)), z: z.map((v) => +(+v).toFixed(1)),
      zs: zs.map((v) => +v.toFixed(1)), grade: grades(zs, rs.s, P.win).map((v) => +v.toFixed(1)),
      stats: stats(rs.s, z, zs, P.win),
    };
  }

  /** Coarse 8 x 8 grid over the map frame: the terrain's height range (for config.json). */
  async function frameRange(bboxLonLat) {
    const [w, s, e, n] = bboxLonLat, pts = [];
    for (let i = 0; i < 8; i++) for (let j = 0; j < 8; j++)
      pts.push([w + ((e - w) * (i + 0.5)) / 8, s + ((n - s) * (j + 0.5)) / 8]);
    const z = await openMeteo(pts);
    return { min_m: Math.min(...z), max_m: Math.max(...z) };
  }

  window.Elevation = { profile, frameRange, grades };
})();
