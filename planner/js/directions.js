/* Routing through a directions service: Valhalla on the FOSSGIS servers, the router behind
   openstreetmap.org's own Directions panel. One /route call for the stops, then one
   /trace_attributes call per leg for the OSM way id, name, class, surface and lanes of every
   stretch. Seconds, instead of downloading every road around the route (osmgraph.js), which is
   still needed for race circuits (highway=raceway is not routable here), footpaths, and when
   this service is down. */
(function () {
  "use strict";
  const BASE = "https://valhalla1.openstreetmap.de";
  const TIMEOUT_MS = 20000;
  const ATTRS = ["edge.way_id", "edge.names", "edge.road_class", "edge.use", "edge.surface", "edge.lane_count",
    "edge.bridge", "edge.tunnel", "edge.speed_limit", "edge.traversability",
    "edge.begin_shape_index", "edge.end_shape_index", "shape"];
  let dataDate;                        // undefined = not asked yet

  async function call(path, body) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
    try {
      // POST as text/plain (a "simple" request: no CORS preflight; long shapes do not fit a URL)
      const r = await fetch(`${BASE}/${path}`, { method: "POST", body: JSON.stringify(body), signal: ctl.signal });
      const j = await r.json().catch(() => ({}));
      if (!r.ok || j.error) {
        const err = new Error(j.error ? `${j.error} (${j.error_code || r.status})` : `HTTP ${r.status}`);
        err.noRoute = j.error_code === 442 || j.error_code === 171 || /no path|no suitable edges/i.test(j.error || "");
        throw err;
      }
      return j;
    } catch (e) {
      if (e.name === "AbortError") throw new Error("directions service timed out");
      throw e;
    } finally { clearTimeout(timer); }
  }

  /** Google-style encoded polyline at 6 decimals (Valhalla) -> [[lon, lat], ...] */
  function decode(str) {
    const out = [];
    let i = 0, lat = 0, lon = 0;
    while (i < str.length) {
      for (const which of [0, 1]) {
        let shift = 0, result = 0, b;
        do { b = str.charCodeAt(i++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
        const d = result & 1 ? ~(result >> 1) : result >> 1;
        if (which === 0) lat += d; else lon += d;
      }
      out.push([lon / 1e6, lat / 1e6]);
    }
    return out;
  }

  function encode(coords) {
    let out = "", plat = 0, plon = 0;
    const enc = (v) => {
      v = v < 0 ? ~(v << 1) : v << 1;
      while (v >= 0x20) { out += String.fromCharCode((0x20 | (v & 0x1f)) + 63); v >>= 5; }
      out += String.fromCharCode(v + 63);
    };
    for (const [lon, lat] of coords) {
      const la = Math.round(lat * 1e6), lo = Math.round(lon * 1e6);
      enc(la - plat); enc(lo - plon); plat = la; plon = lo;
    }
    return out;
  }

  function costing(opt, ignoreOneways) {
    return { auto: {
      ignore_oneways: ignoreOneways, ignore_access: true, ignore_restrictions: true, ignore_closures: true,
      use_tracks: opt.allowTracks ? 0.5 : 0, use_ferry: 0,
    } };
  }

  const REF = /^([A-Z]{1,4}[ -]?\d+[A-Z]?|\d{1,4}[A-Z]?)$/;   // SP32, E4, A 1, M1225, 109

  /** Valhalla edge attributes -> OSM-like tags (what the brief's road table reads). */
  function tagsOf(e) {
    const names = e.names || [];
    const ref = names.filter((n) => REF.test(n)).join(";");
    const name = names.filter((n) => !REF.test(n)).join(" / ");
    const hw = e.use === "track" ? "track" : e.use === "driveway" || e.use === "alley" || e.use === "parking_aisle" ? "service"
      : (e.road_class || "").replace("service_other", "service");
    const t = { highway: hw };
    if (name) t.name = name;
    if (ref) t.ref = ref;
    if (e.surface) t.surface = e.surface;
    if (e.lane_count) t.lanes = String(e.lane_count);
    if (e.speed_limit) t.maxspeed = String(e.speed_limit);
    if (e.bridge) t.bridge = "yes";
    if (e.tunnel) t.tunnel = "yes";
    if (e.traversability && e.traversability !== "both") t.oneway = "yes";
    return t;
  }

  /** When the service's map data was built (for the brief), once per session. */
  async function osmDate() {
    if (dataDate !== undefined) return dataDate;
    dataDate = null;
    try {
      const r = await fetch(`${BASE}/status`);
      const j = await r.json();
      if (j.tileset_last_modified) dataDate = new Date(j.tileset_last_modified * 1000).toISOString();
    } catch (e) { /* unknown */ }
    return dataDate;
  }

  /** Route through the stops (each {lat, lon}); no U-turns at the stops in between.
      Returns one leg per pair of consecutive stops: {coords, nodes, edgeWays, length,
      againstOneway, ways: {wayId: tags}} (nodes are null: the service does not expose OSM nodes). */
  async function route(stops, opt, onStatus) {
    const locations = stops.map((p, i) => ({ lat: p.lat, lon: p.lon,
      type: i === 0 || i === stops.length - 1 ? "break" : "break_through" }));
    const ignore = opt.oneway === "ignore";
    onStatus && onStatus("Directions…");
    let j;
    try {
      j = await call("route", { locations, costing: "auto", costing_options: costing(opt, ignore), directions_type: "none" });
    } catch (err) {
      // one-way rules can make a closed-road route impossible: try again without them
      if (!ignore && err.noRoute) j = await call("route", { locations, costing: "auto", costing_options: costing(opt, true), directions_type: "none" });
      else throw err;
    }
    // a leg that starts in the middle of a road repeats its first point: drop repeats, then match
    // the line to the road edges (exact walk first, map matching if that fails)
    const shapes = j.trip.legs.map((l) => decode(l.shape).filter((p, i, a) => i === 0 || p[0] !== a[i - 1][0] || p[1] !== a[i - 1][1]));
    onStatus && onStatus("Road details…");
    const traced = await Promise.all(shapes.map((c) => call("trace_attributes", {
      encoded_polyline: encode(c), shape_match: "walk_or_snap", costing: "auto", costing_options: costing(opt, true),
      filters: { attributes: ATTRS, action: "include" },
    }).catch(() => null)));
    return shapes.map((routeCoords, k) => {
      // edge indices refer to the shape the trace returns (the same line, unless it was map-matched)
      const coords = traced[k] && traced[k].shape ? decode(traced[k].shape) : routeCoords;
      const edgeWays = new Array(Math.max(0, coords.length - 1)).fill(null);
      const ways = {};
      let against = 0;
      const t = traced[k];
      if (t && t.edges) {
        for (const e of t.edges) {
          if (e.way_id == null) continue;
          ways[e.way_id] = ways[e.way_id] || tagsOf(e);
          if (e.traversability === "backward") against++;
          for (let s = e.begin_shape_index; s < e.end_shape_index && s < edgeWays.length; s++) edgeWays[s] = e.way_id;
        }
      }
      let length = 0;
      for (let i = 1; i < coords.length; i++) length += Geo.haversine(coords[i - 1], coords[i]);
      return { coords, nodes: coords.map(() => null), edgeWays, length, againstOneway: against, ways };
    });
  }

  window.Directions = { route, osmDate, decode, encode };
})();
