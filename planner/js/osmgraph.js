/* OSM road graph for routing in the browser.
   Roads are downloaded from the Overpass API in TILE-degree tiles (each way with its full
   geometry, node ids and tags), joined into one graph keyed by OSM node id, and routed with A*.
   Unlike a car router this can ignore one-way rules and access tags (closed-road events), and it
   routes on raceways and tracks, so the line follows the real road the user means. */
(function () {
  "use strict";
  const TILE = 0.04;                   // degrees (~4.4 km N-S): one Overpass query each
  // The main instance first: on 429 it says (at /api/status) when a slot frees up, and its usage
  // policy asks clients to wait for it. Mirrors only when it errors or times out.
  const MAIN = "https://overpass-api.de/api/interpreter";
  const MIRRORS = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
  ];
  const HIGHWAYS = ["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
    "living_street", "service", "road", "track", "raceway", "busway", "motorway_link", "trunk_link",
    "primary_link", "secondary_link", "tertiary_link", "pedestrian", "path", "footway", "cycleway", "bridleway"];
  const PATHS = new Set(["pedestrian", "path", "footway", "cycleway", "bridleway"]);
  const PENALTY = {
    raceway: 0.5, living_street: 1.2, service: 1.6, busway: 2.0, track: 3.0,
    pedestrian: 4.0, path: 6.0, footway: 6.0, cycleway: 6.0, bridleway: 6.0,
  };
  const UNPAVED = /^(unpaved|gravel|fine_gravel|dirt|earth|ground|grass|mud|sand|compacted|pebblestone|rock|woodchips)$/;

  const nodes = new Map();             // id -> {lon, lat, adj: [{to, way, len, dir}]}
  const ways = new Map();              // id -> {id, tags, nodes}
  const loaded = new Set();            // tile keys
  const segGrid = new Map();           // "gx,gy" -> [[nodeA, nodeB, wayId]]
  const GRID = 0.002;
  let osmTimestamp = null;
  const listeners = [];

  function tileKey(ix, iy) { return ix + "_" + iy; }

  // Downloads are kept in IndexedDB for CACHE_DAYS, so reloading the page or reopening a track
  // does not hit the public Overpass servers again. Any storage error just skips the cache.
  const CACHE_DAYS = 7;
  let dbp = null;
  function db() {
    if (!dbp) dbp = new Promise((res, rej) => {
      const r = indexedDB.open("acts-overpass", 1);
      r.onupgradeneeded = () => r.result.createObjectStore("q");
      r.onsuccess = () => res(r.result);
      r.onerror = () => rej(r.error);
    });
    return dbp;
  }
  async function cacheGet(key) {
    try {
      const d = await db();
      const v = await new Promise((res, rej) => { const q = d.transaction("q").objectStore("q").get(key); q.onsuccess = () => res(q.result); q.onerror = () => rej(q.error); });
      return v && Date.now() - v.t < CACHE_DAYS * 864e5 ? v.json : null;
    } catch (e) { return null; }
  }
  async function cachePut(key, json) {
    try {
      const d = await db();
      d.transaction("q", "readwrite").objectStore("q").put({ t: Date.now(), json }, key);
    } catch (e) { /* no cache */ }
  }

  function tilesFor(bbox) {
    const [w, s, e, n] = bbox, out = [];
    for (let ix = Math.floor(w / TILE); ix <= Math.floor(e / TILE); ix++)
      for (let iy = Math.floor(s / TILE); iy <= Math.floor(n / TILE); iy++) out.push([ix, iy]);
    return out;
  }

  async function post(url, query, timeoutMs) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      return await fetch(url, { method: "POST", body: "data=" + encodeURIComponent(query), signal: ctl.signal,
        headers: { "Content-Type": "application/x-www-form-urlencoded" } });
    } finally { clearTimeout(timer); }
  }

  async function slotWait() {
    try {
      const t = await (await fetch(MAIN.replace("/interpreter", "/status"))).text();
      if (/slots? available now/.test(t) && !/^0 slots/m.test(t)) return 2;
      const m = t.match(/in (\d+) seconds/);
      return m ? Math.min(60, +m[1] + 1) : 10;
    } catch (e) { return 10; }
  }

  async function overpass(query, onStatus) {
    const plan = [MAIN, MAIN, MAIN, MAIN, MAIN, MAIN, ...MIRRORS];
    let lastErr, mainFailed = false;
    for (const url of plan) {
      if (url === MAIN && mainFailed) continue;
      const host = new URL(url).host;
      try {
        onStatus && onStatus(`Downloading roads (${host})…`);
        const r = await post(url, query, url === MAIN ? 120000 : 45000);
        if (r.status === 429 && url === MAIN) {
          lastErr = new Error("Overpass busy (HTTP 429)");
          for (let w = await slotWait(); w > 0; w--) {
            onStatus && onStatus(`Overpass: waiting ${w} s for a free slot…`);
            await new Promise((res) => setTimeout(res, 1000));
          }
          continue;
        }
        if (r.status >= 500 && url === MAIN) {
          // 502 / 504: a busy backend, usually fine a few seconds later
          lastErr = new Error(`Overpass HTTP ${r.status}`);
          for (let w = 4; w > 0; w--) {
            onStatus && onStatus(`Overpass busy (HTTP ${r.status}): retrying in ${w} s…`);
            await new Promise((res) => setTimeout(res, 1000));
          }
          continue;
        }
        if (!r.ok) throw new Error(`Overpass HTTP ${r.status} (${host})`);
        const j = await r.json();
        if (j.remark && /runtime error|timed out|out of memory/i.test(j.remark)) throw new Error("Overpass: " + j.remark);
        return j;
      } catch (err) {
        lastErr = err.name === "AbortError" ? new Error(`Overpass timed out (${host})`) : err;
        if (url === MAIN) mainFailed = true;      // errors / timeouts: try the mirrors
      }
    }
    throw lastErr || new Error("Overpass unavailable");
  }

  function addSegToGrid(a, b, wayId) {
    const na = nodes.get(a), nb = nodes.get(b);
    const x0 = Math.floor(Math.min(na.lon, nb.lon) / GRID), x1 = Math.floor(Math.max(na.lon, nb.lon) / GRID);
    const y0 = Math.floor(Math.min(na.lat, nb.lat) / GRID), y1 = Math.floor(Math.max(na.lat, nb.lat) / GRID);
    for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) {
      const k = x + "," + y;
      let l = segGrid.get(k);
      if (!l) segGrid.set(k, (l = []));
      l.push([a, b, wayId]);
    }
  }

  function ingest(json) {
    if (json.osm3s && json.osm3s.timestamp_osm_base) osmTimestamp = json.osm3s.timestamp_osm_base;
    let added = 0;
    for (const el of json.elements || []) {
      if (el.type !== "way" || ways.has(el.id) || !el.geometry || !el.nodes) continue;
      const t = el.tags || {};
      if (t.area === "yes") continue;
      ways.set(el.id, { id: el.id, tags: t, nodes: el.nodes });
      added++;
      for (let i = 0; i < el.nodes.length; i++) {
        const id = el.nodes[i], g = el.geometry[i];
        if (!g) continue;
        if (!nodes.has(id)) nodes.set(id, { lon: g.lon, lat: g.lat, adj: [] });
      }
      for (let i = 0; i < el.nodes.length - 1; i++) {
        const a = el.nodes[i], b = el.nodes[i + 1];
        if (!nodes.has(a) || !nodes.has(b) || a === b) continue;
        const na = nodes.get(a), nb = nodes.get(b);
        const len = Geo.haversine([na.lon, na.lat], [nb.lon, nb.lat]);
        na.adj.push({ to: b, way: el.id, len, dir: 1 });
        nb.adj.push({ to: a, way: el.id, len, dir: -1 });
        addSegToGrid(a, b, el.id);
      }
    }
    return added;
  }

  /** Make sure every tile touching bbox [w,s,e,n] is loaded: one query per tile (stable, so the
      cache can serve it again), two at a time (Overpass gives each address two slots). Downloads
      already running for the same tiles are shared. */
  const pending = new Map();           // tile key -> promise
  async function ensure(bbox, onStatus) {
    const tiles = tilesFor(bbox);
    const missing = tiles.filter(([x, y]) => !loaded.has(tileKey(x, y)) && !pending.has(tileKey(x, y)));
    let total = 0, done = 0;
    const n = missing.length;
    const fetchTile = ([x, y]) => {
      const key = tileKey(x, y);
      const q = `[out:json][timeout:120];way["highway"~"^(${HIGHWAYS.join("|")})$"](${(y * TILE).toFixed(5)},${(x * TILE).toFixed(5)},${((y + 1) * TILE).toFixed(5)},${((x + 1) * TILE).toFixed(5)});out body geom;`;
      const job = (async () => {
        let j = await cacheGet(q);
        if (!j) {
          j = await overpass(q, (m) => onStatus && onStatus(`${m} ${done + 1}/${n}`));
          cachePut(q, j);
        }
        total += ingest(j);
        loaded.add(key);
        done++;
      })().finally(() => pending.delete(key));
      pending.set(key, job);
      return job;
    };
    const queue = missing.slice();
    const worker = async () => { while (queue.length) await fetchTile(queue.shift()); };
    await Promise.all([worker(), worker()]);
    for (const [x, y] of tiles) { const p = pending.get(tileKey(x, y)); if (p) await p; }
    if (total) listeners.forEach((f) => f());
    return total;
  }

  function isOneway(t) {
    const o = t.oneway;
    if (o === "yes" || o === "true" || o === "1") return 1;
    if (o === "-1" || o === "reverse") return -1;
    if (o === "no") return 0;
    if (t.junction === "roundabout" || t.junction === "circular" || t.highway === "motorway") return 1;
    return 0;
  }

  const AVOID = 6;                     // cost factor on segments an earlier leg already drives
  function edgeKey(a, b) { return a < b ? a + "-" + b : b + "-" + a; }

  function edgeCost(e, opt, from, avoid) {
    const t = ways.get(e.way).tags;
    const hw = t.highway;
    if (PATHS.has(hw) && !opt.allowPaths) return Infinity;
    if (hw === "track" && !opt.allowTracks) return Infinity;
    let c = e.len * (PENALTY[hw] || 1.0);
    if (hw !== "track" && t.surface && UNPAVED.test(t.surface)) c *= 1.5;
    const ow = isOneway(t);
    if (ow && ow !== e.dir) {
      if (opt.oneway === "strict") return Infinity;
      if (opt.oneway === "soft") c *= 1.5;
    }
    if (avoid && typeof from === "number" && typeof e.to === "number" && avoid.has(edgeKey(from, e.to))) c *= AVOID;
    return c;
  }

  /** Nearest point on a loaded road segment: {a, b, way, t, p, dist} or null. */
  function snap(lon, lat, opt, maxDist) {
    maxDist = maxDist || 250;
    const gx = Math.floor(lon / GRID), gy = Math.floor(lat / GRID);
    const r = Math.ceil(maxDist / 150);
    const loc = Geo.makeLocal([lon, lat]);
    let best = null;
    for (let x = gx - r; x <= gx + r; x++) for (let y = gy - r; y <= gy + r; y++) {
      const list = segGrid.get(x + "," + y);
      if (!list) continue;
      for (const [a, b, wayId] of list) {
        const hw = ways.get(wayId).tags.highway;
        if (PATHS.has(hw) && !opt.allowPaths) continue;
        if (hw === "track" && !opt.allowTracks) continue;
        const na = nodes.get(a), nb = nodes.get(b);
        const pa = loc.fwd([na.lon, na.lat]), pb = loc.fwd([nb.lon, nb.lat]);
        const dx = pb[0] - pa[0], dy = pb[1] - pa[1], L2 = dx * dx + dy * dy;
        let t = L2 > 0 ? -(pa[0] * dx + pa[1] * dy) / L2 : 0;
        t = Math.max(0, Math.min(1, t));
        const d = Math.hypot(pa[0] + dx * t, pa[1] + dy * t);
        if (!best || d < best.dist) best = { a, b, way: wayId, t, dist: d, p: loc.inv([pa[0] + dx * t, pa[1] + dy * t]) };
      }
    }
    return best && best.dist <= maxDist ? best : null;
  }

  /** Shortest path between two snaps; `avoid` (optional) is a Set of edgeKey()s to make dearer;
      `noBack` (optional) {q, p} = the last two OSM nodes the previous leg drove through to reach
      this start: the leg may not begin by turning back along them (no U-turn at a stop).
      Returns {coords, nodes, edgeWays, length, againstOneway} or null. */
  function route(sa, sb, opt, avoid, noBack) {
    const VA = "A", VB = "B";
    const extra = new Map();          // node -> extra adjacency to the virtual nodes
    const pos = new Map([[VA, sa.p], [VB, sb.p]]);
    function addExtra(from, edge) {
      if (!extra.has(from)) extra.set(from, []);
      extra.get(from).push(edge);
    }
    function wireVirtual(v, sn) {
      const na = nodes.get(sn.a), nb = nodes.get(sn.b);
      const da = Geo.haversine([na.lon, na.lat], sn.p), db = Geo.haversine([nb.lon, nb.lat], sn.p);
      // dir is relative to the way's node order: a -> b is forward
      addExtra(v, { to: sn.a, way: sn.way, len: da, dir: -1 });
      addExtra(v, { to: sn.b, way: sn.way, len: db, dir: 1 });
      addExtra(sn.a, { to: v, way: sn.way, len: da, dir: 1 });
      addExtra(sn.b, { to: v, way: sn.way, len: db, dir: -1 });
    }
    wireVirtual(VA, sa);
    wireVirtual(VB, sb);
    if (sa.way === sb.way && ((sa.a === sb.a && sa.b === sb.b))) {
      // both on the same segment: direct link along it
      const len = Geo.haversine(sa.p, sb.p);
      addExtra(VA, { to: VB, way: sa.way, len, dir: sb.t >= sa.t ? 1 : -1 });
    }
    const coord = (id) => pos.get(id) || [nodes.get(id).lon, nodes.get(id).lat];
    const goal = sb.p;
    const minPen = 0.5;                // smallest cost factor (raceway): keeps the heuristic admissible
    const h = (id) => Geo.haversine(coord(id), goal) * minPen;
    // the previous leg came in along q (and p before it): starting back towards q is a U-turn;
    // if q is the stop itself (snapped onto a node), turning from q back to p is
    let banFirst = null, banAtQ = null;
    if (noBack && noBack.q != null && nodes.has(noBack.q)) {
      const nq = nodes.get(noBack.q);
      if (Geo.haversine([nq.lon, nq.lat], sa.p) > 0.5) banFirst = noBack.q;
      else if (noBack.p != null) banAtQ = noBack.p;
    }
    const g = new Map([[VA, 0]]), prev = new Map();
    const heap = new MinHeap();
    heap.push(h(VA), VA);
    const closed = new Set();
    let found = false, iter = 0;
    while (heap.size()) {
      const u = heap.pop();
      if (closed.has(u)) continue;
      if (u === VB) { found = true; break; }
      closed.add(u);
      if (++iter > 2e6) break;
      const gu = g.get(u);
      const adj = (nodes.get(u) ? nodes.get(u).adj : []).concat(extra.get(u) || []);
      for (const e of adj) {
        if (closed.has(e.to)) continue;
        if (u === VA && e.to === banFirst) continue;
        if (banAtQ != null && u === noBack.q && e.to === banAtQ && prev.get(u) && prev.get(u)[0] === VA) continue;
        const c = edgeCost(e, opt, u, avoid);
        if (!isFinite(c)) continue;
        const nd = gu + c;
        if (nd < (g.has(e.to) ? g.get(e.to) : Infinity)) {
          g.set(e.to, nd);
          prev.set(e.to, [u, e]);
          heap.push(nd + h(e.to), e.to);
        }
      }
    }
    if (!found) return null;
    const path = [VB], edges = [];
    while (path[path.length - 1] !== VA) {
      const [u, e] = prev.get(path[path.length - 1]);
      edges.push(e);
      path.push(u);
    }
    path.reverse(); edges.reverse();
    const coords = path.map(coord);
    let length = 0;
    for (const e of edges) length += e.len;
    const againstOneway = edges.filter((e) => { const o = isOneway(ways.get(e.way).tags); return o && o !== e.dir; }).length;
    return {
      coords,
      nodes: path.map((id) => (typeof id === "number" ? id : null)),
      edgeWays: edges.map((e) => e.way),
      length, againstOneway,
    };
  }

  class MinHeap {
    constructor() { this.k = []; this.v = []; }
    size() { return this.k.length; }
    push(key, val) {
      const k = this.k, v = this.v;
      k.push(key); v.push(val);
      let i = k.length - 1;
      while (i > 0) {
        const p = (i - 1) >> 1;
        if (k[p] <= k[i]) break;
        [k[p], k[i]] = [k[i], k[p]]; [v[p], v[i]] = [v[i], v[p]];
        i = p;
      }
    }
    pop() {
      const k = this.k, v = this.v;
      const top = v[0];
      const lk = k.pop(), lv = v.pop();
      if (k.length) {
        k[0] = lk; v[0] = lv;
        let i = 0;
        for (;;) {
          const l = 2 * i + 1, r = l + 1;
          let m = i;
          if (l < k.length && k[l] < k[m]) m = l;
          if (r < k.length && k[r] < k[m]) m = r;
          if (m === i) break;
          [k[m], k[i]] = [k[i], k[m]]; [v[m], v[i]] = [v[i], v[m]];
          i = m;
        }
      }
      return top;
    }
  }

  /** Loaded ways intersecting a bbox, for drawing the network. */
  function waysIn(bbox, limit) {
    const [w, s, e, n] = bbox, out = [];
    for (const way of ways.values()) {
      let inside = false;
      for (const id of way.nodes) {
        const nd = nodes.get(id);
        if (nd && nd.lon >= w && nd.lon <= e && nd.lat >= s && nd.lat <= n) { inside = true; break; }
      }
      if (inside) {
        out.push({ id: way.id, tags: way.tags, coords: way.nodes.filter((id) => nodes.has(id)).map((id) => [nodes.get(id).lon, nodes.get(id).lat]) });
        if (out.length >= (limit || 20000)) break;
      }
    }
    return out;
  }

  window.OsmGraph = {
    ensure, snap, route, waysIn, isOneway, edgeKey,
    way: (id) => ways.get(id),
    stats: () => ({ ways: ways.size, nodes: nodes.size, tiles: loaded.size, timestamp: osmTimestamp }),
    onChange: (f) => listeners.push(f),
    PATHS, UNPAVED,
  };
})();
