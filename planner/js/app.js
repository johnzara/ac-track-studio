/* AC Track Studio planner: draw a real-road track on OSM, survey it, and generate the brief. */
(function () {
  "use strict";
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const LS_STATE = "acts.state.v1", LS_SETTINGS = "acts.settings.v1";
  const NOMINATIM = "https://nominatim.openstreetmap.org";
  // Without serve.py (page opened as a file) the studio is the folder above planner/; Settings can change it.
  const STUDIO_GUESS = location.protocol === "file:" ? decodeURIComponent(location.pathname).replace(/\/planner\/[^/]*$/, "") : "";
  const DEFAULT_PATHS = { studio_root: STUDIO_GUESS, reference_pipeline: STUDIO_GUESS && `${STUDIO_GUESS}/pipeline` };

  const DEFAULT_DETAIL = {
    road_widen_m: 1.0, road_min_width_m: 3.8, crown_pct: 1.5, surface: "survey", markings: "survey",
    kerbs: "real", racing_kerb_max_radius_m: 120, barriers: "survey",
    tree_mesh_radius_m: 60, tree_card_radius_m: 600, tree_species: "", building_detail_radius_m: 150,
    terrain_corridor_m: 60, map_margin_m: 1000, horizon_km: 30,
    csp_grass: true, rain: true, ai_line: true, replay_cameras: true, night_lights: false,
  };

  function newState() {
    return {
      schema: 1, name: "", slug: "", slugEdited: false, description: "", author: "",
      type: "point_to_point",
      waypoints: [],
      routing: { engine: "fast", oneway: "soft", allowTracks: true, allowPaths: false },
      route: null, elevation: null, frame: null, place: null,
      timing: { auto: true, start_m: 30, finish_m: 0, splits: [], laps: 5, grid_slots: 12, grid_spacing_m: 8, pits: "grid", pit_lat: null, pit_lon: null, pit_count: 12 },
      detail: Object.assign({}, DEFAULT_DETAIL),
      survey: { coverage: [], segments: [] },
      annotations: [], notes: "",
      view: null,
    };
  }

  function loadState() {
    try {
      const s = JSON.parse(localStorage.getItem(LS_STATE) || "null");
      if (s && s.schema === 1) return normalise(s);
    } catch (e) { /* ignore */ }
    return null;
  }
  function normalise(s) {
    const n = newState();
    const out = Object.assign(n, s);
    out.detail = Object.assign({}, DEFAULT_DETAIL, s.detail || {});
    out.timing = Object.assign(newState().timing, s.timing || {});
    out.routing = Object.assign(newState().routing, s.routing || {});
    out.survey = Object.assign({ coverage: [], segments: [] }, s.survey || {});
    out.waypoints = (s.waypoints || []).map((w, i) => Object.assign({ id: uid(), leg: "road", label: "" }, w, { id: w.id || uid() + i }));
    out.annotations = (s.annotations || []).map((a) => Object.assign({ id: uid() }, a));
    if (out.route) out.route._cum = null;
    return out;
  }
  let saveTimer = null;
  function save() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      try { localStorage.setItem(LS_STATE, JSON.stringify(st, (k, v) => (k.startsWith("_") ? undefined : v))); }
      catch (e) { toast("Could not autosave (browser storage full?)", true); }
    }, 400);
  }
  function uid() { return Math.random().toString(36).slice(2, 10); }

  let st = loadState() || newState();
  let settings = Object.assign({}, DEFAULT_PATHS, JSON.parse(localStorage.getItem(LS_SETTINGS) || "{}"));
  let server = null;                    // /api/info when served by serve.py
  const legCache = new Map();
  const undo = [];
  let pick = null;                      // {kind: "wp", index} | {kind: "pit"}
  let routeSeq = 0, elevSeq = 0;
  let svActive = false, svS = 0;           // survey position along the route (Street View window)

  // ------------------------------------------------------------------------------------- helpers
  function cum() {
    if (!st.route) return null;
    if (!st.route._cum || st.route._cum.length !== st.route.coords.length) st.route._cum = Geo.cumulative(st.route.coords);
    return st.route._cum;
  }
  const L_ = () => (st.route ? st.route.length : 0);
  const km = (m) => (m / 1000).toFixed(2);
  let statusTimer = null;
  function status(msg, busy) {
    const el = $("#status");
    el.textContent = msg || "";
    el.classList.toggle("busy", !!busy);
    clearTimeout(statusTimer);
    if (msg && !busy) statusTimer = setTimeout(() => { el.textContent = ""; }, 6000);
  }
  let toastTimer = null;
  function toast(msg, err) {
    const el = $("#toast");
    el.textContent = msg; el.classList.toggle("err", !!err); el.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.add("hidden"), err ? 7000 : 3500);
  }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
  // Nominatim's usage policy: at most one request per second, no autocomplete.
  let nomChain = Promise.resolve();
  function nominatim(path) {
    const p = nomChain.then(() => fetch(`${NOMINATIM}${path}`, { headers: { "Accept-Language": "en" } }));
    nomChain = p.catch(() => {}).then(() => new Promise((r) => setTimeout(r, 1100)));
    return p.then((r) => { if (!r.ok) throw new Error(`Nominatim HTTP ${r.status}`); return r.json(); });
  }
  function shortLabel(s) { return (s || "").split(",").slice(0, 2).map((x) => x.trim()).filter(Boolean).join(", "); }
  function pushUndo() {
    undo.push(JSON.stringify({ waypoints: st.waypoints, timing: st.timing, type: st.type }));
    if (undo.length > 60) undo.shift();
  }

  // ----------------------------------------------------------------------------------------- map
  const map = L.map("map", { zoomControl: true }).setView(st.view ? st.view.c : [45.5, 11.3], st.view ? st.view.z : 6);
  const baseLayers = {
    "OpenStreetMap": L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "&copy; OpenStreetMap contributors" }),
    "Satellite (Esri)": L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", { maxZoom: 19, attribution: "Source: Esri, Vantor, Earthstar Geographics, and the GIS User Community (reference view only)" }),
    "Topographic": L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", { maxZoom: 17, subdomains: "abc", attribution: "&copy; OpenStreetMap contributors, SRTM | &copy; OpenTopoMap (CC-BY-SA)" }),
  };
  baseLayers.OpenStreetMap.addTo(map);
  const G = {
    network: L.layerGroup(), route: L.layerGroup().addTo(map), marks: L.layerGroup().addTo(map),
    corners: L.layerGroup().addTo(map), coverage: L.layerGroup().addTo(map), survey: L.layerGroup().addTo(map),
    notes: L.layerGroup().addTo(map), wps: L.layerGroup().addTo(map), hover: L.layerGroup().addTo(map), sv: L.layerGroup().addTo(map),
  };
  L.control.layers(baseLayers, {
    "Loaded road network": G.network, "Corners": G.corners,
    "Survey segments": G.survey, "Annotations": G.notes,
  }, { position: "topright" }).addTo(map);
  L.control.scale({ imperial: false }).addTo(map);
  map.on("moveend", () => { const c = map.getCenter(); st.view = { c: [c.lat, c.lng], z: map.getZoom() }; save(); if (map.hasLayer(G.network)) drawNetwork(); });
  map.on("overlayadd", (e) => { if (e.layer === G.network) drawNetwork(); });
  const hoverMarker = L.circleMarker([0, 0], { radius: 6, color: "#fff", weight: 2, fillColor: "#111", fillOpacity: 1, interactive: false });

  map.on("click", (e) => {
    if (pick) { finishPick(e.latlng); return; }
    mapMenu(e.latlng);
  });
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    if (e.key === "Escape") { cancelPick(); map.closePopup(); closeModal(); return; }
    if ((e.metaKey || e.ctrlKey) && e.key === "z" && !typing) { e.preventDefault(); doUndo(); return; }
    if (typing || !svActive) return;
    const steps = { "[": -25, "]": 25, "{": -100, "}": 100 };
    if (steps[e.key]) { e.preventDefault(); svStep(steps[e.key]); }
  });

  function popupMenu(latlng, title, items) {
    const box = document.createElement("div");
    box.className = "popup-menu";
    box.innerHTML = `<div class="where">${esc(title)}</div>`;
    items.filter(Boolean).forEach(([label, fn]) => {
      const b = document.createElement("button");
      b.type = "button"; b.textContent = label;
      b.addEventListener("click", () => { map.closePopup(); fn(); });
      box.appendChild(b);
    });
    L.popup({ closeButton: true, autoPan: true }).setLatLng(latlng).setContent(box).openOn(map);
  }

  function mapMenu(ll) {
    const n = st.waypoints.length;
    const pt = { lat: ll.lat, lon: ll.lng };
    popupMenu(ll, `${ll.lat.toFixed(5)}, ${ll.lng.toFixed(5)}`, [
      [n === 0 ? "Start from here" : "Add stop here (end)", () => addWaypoint(pt)],
      n >= 1 && ["Insert as first stop", () => addWaypoint(pt, 0)],
      ["Note here…", () => editAnnotation(null, pt)],
      st.type === "circuit" && st.timing.pits === "area" && ["Pit area here", () => setPit(pt)],
      ["Street View here", () => openSvAtLatLon(pt.lon, pt.lat)],
    ]);
  }

  function routeMenu(ll) {
    const pr = Geo.project(st.route.coords, cum(), [ll.lng, ll.lat]);
    const s = pr.s;
    const isC = st.type === "circuit";
    popupMenu(ll, `${km(s)} km along the route`, [
      ["Add a stop here (reshape)", () => insertWaypointAt(s, pr.p)],
      [isC ? "Start / finish here" : "Start here", () => setTiming(isC ? "sf" : "start", s)],
      !isC && ["Finish here", () => setTiming("finish", s)],
      [isC ? "Sector line here" : "Split here", () => addSplit(s)],
      ["Street View here", () => openSvAt(s)],
      ["Survey segment from here", () => newSegment({ from_m: Math.round(s) })],
      ["Note here…", () => editAnnotation(null, { lat: ll.lat, lon: ll.lng })],
    ]);
  }

  function startPick(p, banner) {
    pick = p;
    $("#pickBanner").textContent = banner + "  (Esc to cancel)";
    $("#pickBanner").classList.remove("hidden");
    document.body.classList.add("map-crosshair");
    renderWaypoints();
  }
  function cancelPick() {
    if (!pick) return;
    pick = null;
    $("#pickBanner").classList.add("hidden");
    document.body.classList.remove("map-crosshair");
    renderWaypoints();
  }
  function finishPick(ll) {
    const p = pick;
    cancelPick();
    if (p.kind === "wp" && p.add) {
      addWaypoint({ lat: ll.lat, lon: ll.lng }, Math.min(p.index, st.waypoints.length));
    } else if (p.kind === "wp") {
      pushUndo();
      const w = st.waypoints[p.index];
      if (w) { w.lat = ll.lat; w.lon = ll.lng; w.label = ""; labelFromReverse(w); }
      changed();
    } else if (p.kind === "pit") setPit({ lat: ll.lat, lon: ll.lng });
  }

  // ---------------------------------------------------------------------------------- waypoints
  function addWaypoint(pt, index) {
    pushUndo();
    const w = { id: uid(), lat: pt.lat, lon: pt.lon, label: pt.label || "", leg: "road" };
    if (index == null) st.waypoints.push(w); else st.waypoints.splice(index, 0, w);
    if (!pt.label) labelFromReverse(w);
    changed();
  }
  function insertWaypointAt(s, p) {
    const legs = st.route.legs;
    let k = legs.findIndex((l) => s >= l.s0 && s <= l.s1);
    if (k < 0) k = legs.length - 1;
    pushUndo();
    const w = { id: uid(), lat: p[1], lon: p[0], label: "", leg: st.waypoints[legs[k].from].leg };
    st.waypoints.splice(legs[k].from + 1, 0, w);
    labelFromReverse(w);
    changed();
  }
  function removeWaypoint(i) {
    pushUndo();
    st.waypoints.splice(i, 1);
    changed();
  }
  function reverseRoute() {
    const n = st.waypoints.length;
    if (n < 2) return;
    pushUndo();
    const legs = st.waypoints.map((w) => w.leg);
    const L = L_();
    if (st.type === "circuit") {
      // keep the first stop (the start / finish area) first: A, D, C, B
      st.waypoints = [st.waypoints[0], ...st.waypoints.slice(1).reverse()];
      st.waypoints.forEach((w, j) => { w.leg = legs[(n - j - 1 + n) % n] || "road"; });
    } else {
      st.waypoints.reverse();
      st.waypoints.forEach((w, j) => { w.leg = legs[n - 2 - j] || "road"; });
      const t = st.timing;
      [t.start_ll, t.finish_ll] = [t.finish_ll, t.start_ll];
      if (L) { const a = t.start_m, b = t.finish_m; t.start_m = L - b; t.finish_m = L - a; }
    }
    if (L) {
      // timing lines move with their map anchors (adjustTiming); survey sides swap
      st.survey.segments.forEach((g) => {
        [g.from_m, g.to_m] = [Math.round(L - g.to_m), Math.round(L - g.from_m)];
        [g.from_ll, g.to_ll] = [g.to_ll, g.from_ll];
        [g.left, g.right] = [g.right, g.left];
        [g.left_height_m, g.right_height_m] = [g.right_height_m, g.left_height_m];
        if (g.edge_lines === "left") g.edge_lines = "right"; else if (g.edge_lines === "right") g.edge_lines = "left";
      });
    }
    changed();
  }
  function doUndo() {
    const s = undo.pop();
    if (!s) { toast("Nothing to undo"); return; }
    const o = JSON.parse(s);
    st.waypoints = o.waypoints; st.timing = o.timing; st.type = o.type;
    changed();
  }

  async function labelFromReverse(w) {
    try {
      const j = await nominatim(`/reverse?format=jsonv2&lat=${w.lat}&lon=${w.lon}&zoom=17&addressdetails=1`);
      const a = j.address || {};
      const road = a.road || a.pedestrian || a.path || "";
      const town = a.village || a.town || a.city || a.hamlet || a.municipality || a.suburb || "";
      w.label = [road, town].filter(Boolean).join(", ") || shortLabel(j.display_name) || "";
      renderWaypoints(); autoName(); save();
    } catch (e) { /* label stays empty */ }
  }

  async function searchPlace(q, near) {
    const m = q.trim().match(/^(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)$/);
    if (m) return [{ lat: +m[1], lon: +m[2], label: "", full: `${(+m[1]).toFixed(5)}, ${(+m[2]).toFixed(5)}`, coords: true }];
    const b = map.getBounds();
    const vb = `${b.getWest()},${b.getNorth()},${b.getEast()},${b.getSouth()}`;
    const j = await nominatim(`/search?format=jsonv2&limit=6&q=${encodeURIComponent(q)}&viewbox=${vb}`);
    return j.map((x) => ({ lat: +x.lat, lon: +x.lon, label: shortLabel(x.display_name), full: x.display_name }));
  }

  function renderWaypoints() {
    const ol = $("#wpList");
    ol.innerHTML = "";
    const n = st.waypoints.length;
    const isC = st.type === "circuit";
    const rows = n ? st.waypoints : [];
    const adding = pick && pick.kind === "wp" && pick.add && pick.index >= n ? 1 : 0;
    const slots = Math.max(2, n + adding);
    for (let i = 0; i < slots; i++) {
      const w = rows[i];
      const li = document.createElement("li");
      const cls = i === 0 ? "first" : (!isC && i === slots - 1 ? "last" : "");
      const ph = i === 0 ? (isC ? "Start / finish area" : "From") : (i === slots - 1 && !isC ? "To" : "Via");
      li.innerHTML = `<div class="wp-row">
          <span class="wp-badge ${cls}">${String.fromCharCode(65 + (i % 26))}</span>
          <input type="text" placeholder="${ph}: search a place or lat, lon" value="${esc(w ? w.label || `${w.lat.toFixed(5)}, ${w.lon.toFixed(5)}` : "")}">
          <button type="button" class="pick ${pick && pick.kind === "wp" && pick.index === i ? "on" : ""}" title="Pick on the map">⌖</button>
          <button type="button" class="zoom" title="Zoom to" ${w ? "" : "disabled"}>◎</button>
          <button type="button" class="del ghost" title="Remove" ${w ? "" : "disabled"}>✕</button>
        </div>`;
      const input = $("input", li);
      input.addEventListener("keydown", async (e) => {
        if (e.key !== "Enter") return;
        e.preventDefault();
        const q = input.value.trim();
        if (!q) return;
        status("Searching…", true);
        try {
          const res = await searchPlace(q);
          status("");
          if (res.length === 1 && res[0].coords) choosePlace(res[0], i);
          else showResults(li, res, i);
        } catch (err) { status(""); toast(err.message, true); }
      });
      $(".pick", li).addEventListener("click", () => {
        if (!w) { // empty slot: the next map click adds it
          startPick({ kind: "wp", index: i, add: true }, `Click the map to set ${ph}`);
          return;
        }
        startPick({ kind: "wp", index: i }, `Click the map to move stop ${String.fromCharCode(65 + i)}`);
      });
      $(".zoom", li).addEventListener("click", () => w && map.setView([w.lat, w.lon], Math.max(map.getZoom(), 16)));
      $(".del", li).addEventListener("click", () => w && removeWaypoint(i));
      ol.appendChild(li);
      const hasLeg = w && (i < n - 1 || (isC && n >= 2));
      if (hasLeg) {
        const leg = st.route && st.route.legs ? st.route.legs.find((l) => l.from === i) : null;
        const nx = rows[(i + 1) % n];
        const straight = nx ? Geo.haversine([w.lon, w.lat], [nx.lon, nx.lat]) : 0;
        const detour = leg && !leg.error && leg.mode !== "direct" && leg.length > 3 * straight && leg.length - straight > 1000;
        const lr = document.createElement("li");
        lr.className = "leg-row";
        lr.innerHTML = `<select title="How this leg follows the ground"><option value="road">along roads</option><option value="direct">direct line</option></select>
          <span>${leg ? esc(km(leg.length)) + " km" : ""}${leg && leg.error ? ` <span class="err">${esc(leg.error)}</span>` : ""}${detour ? ` <button type="button" class="small detour" title="Much longer than the straight line: a race circuit, a private or a missing road? This switches the router to the downloaded OSM roads (Routing rules); adding a stop or a direct leg also helps.">long detour? use downloaded roads</button>` : ""}${i === n - 1 && isC ? " · back to A" : ""}</span>`;
        const sel = $("select", lr);
        sel.value = w.leg;
        sel.addEventListener("change", () => { pushUndo(); w.leg = sel.value; changed(); });
        const dt = $(".detour", lr);
        if (dt) dt.addEventListener("click", () => { st.routing.engine = "graph"; renderRouting(); changed(); });
        ol.appendChild(lr);
      }
    }
    $("#loopHint").textContent = isC ? "closed loop: the last stop returns to A" : "";
    if (adding) { const inputs = $$("#wpList input[type=text]"); const last = inputs[inputs.length - 1]; if (last && document.activeElement !== last) last.focus(); }
  }

  function choosePlace(r, i) {
    cancelPick();
    if (st.waypoints[i]) {
      pushUndo();
      Object.assign(st.waypoints[i], { lat: r.lat, lon: r.lon, label: r.label });
      if (!r.label) labelFromReverse(st.waypoints[i]);
      changed();
    } else addWaypoint({ lat: r.lat, lon: r.lon, label: r.label }, Math.min(i, st.waypoints.length));
    map.setView([r.lat, r.lon], Math.max(map.getZoom(), 14));
  }

  function showResults(li, res, i) {
    $$(".results").forEach((x) => x.remove());
    const box = document.createElement("div");
    box.className = "results";
    if (!res.length) box.innerHTML = `<button type="button" disabled>No results</button>`;
    res.forEach((r) => {
      const b = document.createElement("button");
      b.type = "button"; b.textContent = r.full;
      b.addEventListener("click", () => { box.remove(); choosePlace(r, i); });
      box.appendChild(b);
    });
    $(".wp-row", li).appendChild(box);
    setTimeout(() => document.addEventListener("click", function off(e) {
      if (!box.contains(e.target)) { box.remove(); document.removeEventListener("click", off); }
    }), 0);
  }

  function drawWaypointMarkers() {
    G.wps.clearLayers();
    const n = st.waypoints.length, isC = st.type === "circuit";
    st.waypoints.forEach((w, i) => {
      const cls = i === 0 ? "first" : (!isC && i === n - 1 ? "last" : "");
      const m = L.marker([w.lat, w.lon], {
        draggable: true, autoPan: true, zIndexOffset: 1000,
        icon: L.divIcon({ className: "", html: `<div class="wp-icon ${cls}">${String.fromCharCode(65 + (i % 26))}</div>`, iconSize: [26, 26], iconAnchor: [13, 13] }),
        title: w.label || "",
      });
      m.on("dragend", () => {
        pushUndo();
        const ll = m.getLatLng();
        w.lat = ll.lat; w.lon = ll.lng; w.label = "";
        labelFromReverse(w);
        changed();
      });
      m.on("click", (e) => {
        L.DomEvent.stopPropagation(e);
        popupMenu(m.getLatLng(), `Stop ${String.fromCharCode(65 + i)}: ${w.label || ""}`, [
          ["Remove this stop", () => removeWaypoint(i)],
          ["Street View here", () => openSvAtLatLon(w.lon, w.lat)],
        ]);
      });
      G.wps.addLayer(m);
    });
  }

  // ------------------------------------------------------------------------------------ routing
  let routeTimer = null;
  function changed(opts) {
    renderWaypoints();
    drawWaypointMarkers();
    renderTypeToggle();
    autoName();
    save();
    clearTimeout(routeTimer);
    routeTimer = setTimeout(() => computeRoute(opts), 250);
  }

  function legKey(a, b, mode) {
    const r = st.routing;
    return [a.lat.toFixed(7), a.lon.toFixed(7), b.lat.toFixed(7), b.lon.toFixed(7), mode, r.oneway, r.allowTracks, r.allowPaths].join("|");
  }

  async function routeLeg(a, b, mode, avoid, noBack) {
    const key = legKey(a, b, mode) + (noBack ? `|${noBack.q}|${noBack.p}` : "");
    if (!avoid && legCache.has(key)) return legCache.get(key);
    let res;
    if (mode === "direct") {
      res = { coords: [[a.lon, a.lat], [b.lon, b.lat]], nodes: [null, null], edgeWays: [null], length: Geo.haversine([a.lon, a.lat], [b.lon, b.lat]), againstOneway: 0 };
    } else {
      const d = Geo.haversine([a.lon, a.lat], [b.lon, b.lat]);
      for (const factor of [0.35, 1.0]) {
        const pad = Math.max(900, d * factor);
        const bb = bboxPad([[a.lon, a.lat], [b.lon, b.lat]], pad);
        await OsmGraph.ensure(bb, (m) => status(m, true));
        const opt = st.routing;
        const sa = OsmGraph.snap(a.lon, a.lat, opt), sb = OsmGraph.snap(b.lon, b.lat, opt);
        if (!sa) throw new Error("no road within 250 m of the stop");
        if (!sb) throw new Error("no road within 250 m of the next stop");
        status("Routing…", true);
        // drive through the stop; allow a U-turn only if there is no other way (a dead end)
        res = (noBack && OsmGraph.route(sa, sb, opt, avoid, noBack)) || OsmGraph.route(sa, sb, opt, avoid);
        if (res) break;
      }
      if (!res) throw new Error("no road connection (try the rules, another stop, or a direct leg)");
    }
    if (!avoid) legCache.set(key, res);
    return res;
  }

  function bboxPad(pts, padM) {
    let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
    for (const p of pts) { w = Math.min(w, p[0]); e = Math.max(e, p[0]); s = Math.min(s, p[1]); n = Math.max(n, p[1]); }
    const dLat = padM / 111320, dLon = padM / (111320 * Math.cos(((s + n) / 2) * Math.PI / 180));
    return [w - dLon, s - dLat, e + dLon, n + dLat];
  }

  async function computeRoute() {
    const seq = ++routeSeq;
    const wps = st.waypoints, isC = st.type === "circuit";
    const n = wps.length;
    if (n < 2) {
      st.route = null; st.elevation = null; st.frame = null;
      drawAll(); return;
    }
    const pairs = [];
    for (let i = 0; i < n - 1; i++) pairs.push([i, i + 1]);
    if (isC) pairs.push([n - 1, 0]);
    status("Routing…", true);
    let built = await routeLegs(pairs, false);
    if (!built || seq !== routeSeq) return;
    if (built.repeated && pairs.length > 1 && built.engine === "graph") {
      // sparse stops on a network with shortcuts (an infield, a paddock road) make legs overlap:
      // route again, each leg avoiding what the earlier ones drive, and keep it if it overlaps less
      const again = await routeLegs(pairs, true);
      if (seq !== routeSeq) return;
      if (again && again.repeated < built.repeated && again.length < built.length * 2) built = again;
    }
    const { C, N, E, legs, errors, ways, engine, osmDate } = built;
    const cm = Geo.cumulative(C);
    const length = cm[cm.length - 1];
    let sAcc = 0;
    legs.forEach((l) => { l.s0 = sAcc; sAcc += l.length; l.s1 = sAcc; });
    const prevLen = L_();
    st.route = {
      coords: C, nodes: N, edgeWays: E, legs, length, closed: isC, _cum: cm,
      engine, wayInfo: ways, osm_timestamp: osmDate || null,
    };
    analyseRoute();
    if (st.elevation && st.elevation.hash !== routeHash()) st.elevation = null;   // stale: recomputed below
    adjustTiming(prevLen);
    reprojectSurvey();
    status(errors ? `${errors} leg(s) could not be routed: drawn straight` : `Route ${km(length)} km (${engine === "fast" ? "directions service" : "downloaded OSM roads"})`, false);
    if (errors) toast(`${errors} leg(s) could not be routed on roads; they are drawn as straight lines.`, true);
    st.frame = null;
    drawAll();
    renderWaypoints();
    save();
    placeLookup();
    scheduleElevation();
  }

  function directLeg(a, b) {
    return { coords: [[a.lon, a.lat], [b.lon, b.lat]], nodes: [null, null], edgeWays: [null], length: Geo.haversine([a.lon, a.lat], [b.lon, b.lat]), againstOneway: 0 };
  }
  // a road segment by its two end points (0.1 m), whichever way it is driven: finds repeats
  function segKey(p, q) {
    const a = p[0].toFixed(6) + "," + p[1].toFixed(6), b = q[0].toFixed(6) + "," + q[1].toFixed(6);
    return a < b ? a + "|" + b : b + "|" + a;
  }
  function useDirections() { return st.routing.engine !== "graph" && !st.routing.allowPaths; }

  /** Route every leg and join them: through the directions service when it can (fast), else on the
      downloaded OSM road graph. With `avoid` (graph only), each leg avoids the segments of the legs
      before. */
  async function routeLegs(pairs, avoid) {
    if (!avoid && useDirections()) {
      try {
        const r = await routeLegsFast(pairs);
        if (r) return r;
        if (r === null) return null;           // superseded by a newer edit
      } catch (err) {
        status(`Directions service: ${err.message}. Downloading the roads instead…`, true);
      }
    }
    return routeLegsGraph(pairs, avoid);
  }

  async function routeLegsFast(pairs) {
    const wps = st.waypoints, seq = routeSeq, r = st.routing;
    const results = new Array(pairs.length);
    let k = 0;
    while (k < pairs.length) {
      if (wps[pairs[k][0]].leg === "direct") { results[k] = directLeg(wps[pairs[k][0]], wps[pairs[k][1]]); k++; continue; }
      // consecutive road legs go to the service in one call
      let e = k;
      while (e + 1 < pairs.length && wps[pairs[e + 1][0]].leg !== "direct") e++;
      const stops = [wps[pairs[k][0]], ...pairs.slice(k, e + 1).map((p) => wps[p[1]])];
      const key = "fast|" + stops.map((p) => p.lat.toFixed(7) + "," + p.lon.toFixed(7)).join(";") + `|${r.oneway}|${r.allowTracks}`;
      let legs = legCache.get(key);
      if (!legs) {
        legs = await Directions.route(stops, r, (m) => status(m, true));
        legCache.set(key, legs);
      }
      if (seq !== routeSeq) return null;
      legs.forEach((l, m) => { results[k + m] = l; });
      k = e + 1;
    }
    const built = joinLegs(pairs, results);
    built.engine = "fast";
    built.osmDate = await Directions.osmDate();
    return built;
  }

  async function routeLegsGraph(pairs, avoid) {
    const wps = st.waypoints, seq = routeSeq;
    const results = [], used = avoid ? new Set() : null;
    let noBack = null;
    for (const [i, j] of pairs) {
      const a = wps[i], b = wps[j];
      let res;
      try { res = await routeLeg(a, b, a.leg, used, noBack); }
      catch (err) { res = Object.assign(directLeg(a, b), { failed: true, error: err.message }); }
      if (seq !== routeSeq) return null;
      const real = res.nodes.filter((x) => x != null);
      noBack = res.failed || a.leg === "direct" || !real.length ? null : { q: real[real.length - 1], p: real.length > 1 ? real[real.length - 2] : null };
      if (used) for (let k = 0; k < res.nodes.length - 1; k++) {
        if (res.nodes[k] != null && res.nodes[k + 1] != null) used.add(OsmGraph.edgeKey(res.nodes[k], res.nodes[k + 1]));
      }
      results.push(res);
    }
    const built = joinLegs(pairs, results);
    built.engine = "graph";
    built.osmDate = OsmGraph.stats().timestamp;
    return built;
  }

  /** Join leg results ({coords, nodes, edgeWays, length, againstOneway, ways?, failed?, error?}). */
  function joinLegs(pairs, results) {
    const wps = st.waypoints;
    const legs = [], ways = {};
    let coords = [], nodes = [], edgeWays = [], errors = 0;
    pairs.forEach(([i, j], k) => {
      const res = results[k];
      if (res.error) errors++;
      Object.assign(ways, res.ways || {});
      if (coords.length) {
        // join: drop the duplicate first point of the leg
        coords = coords.concat(res.coords.slice(1)); nodes = nodes.concat(res.nodes.slice(1));
      } else { coords = res.coords.slice(); nodes = res.nodes.slice(); }
      edgeWays = edgeWays.concat(res.edgeWays);
      legs.push({ from: i, to: j, mode: res.failed ? "direct" : wps[i].leg, length: res.length, againstOneway: res.againstOneway, error: res.error || null });
    });
    // drop zero-length steps (a stop exactly on a node)
    const C = [], N = [], E = [];
    for (let k = 0; k < coords.length; k++) {
      if (C.length && Geo.haversine(C[C.length - 1], coords[k]) < 0.05) continue;
      if (C.length) E.push(edgeWays[k - 1]);
      C.push(coords[k]); N.push(nodes[k]);
    }
    const seen = new Set();
    let repeated = 0, length = 0;
    for (let k = 1; k < C.length; k++) {
      length += Geo.haversine(C[k - 1], C[k]);
      if (E[k - 1] == null) continue;
      const key = segKey(C[k - 1], C[k]);
      if (seen.has(key)) repeated++; else seen.add(key);
    }
    return { C, N, E, legs, errors, repeated, length, ways };
  }

  function analyseRoute() {
    const R = st.route, cm = cum();
    // roads in order
    const streets = [];
    let s = 0;
    const seenEdges = new Map();
    let repeated = 0;
    for (let k = 0; k < R.edgeWays.length; k++) {
      const segLen = cm[k + 1] - cm[k];
      const wid = R.edgeWays[k];
      const way = wid != null ? OsmGraph.way(wid) : null;
      const t = way ? way.tags : (R.wayInfo && R.wayInfo[wid]) || null;
      if (wid != null) {
        const key = segKey(R.coords[k], R.coords[k + 1]);
        if (seenEdges.has(key)) repeated++; else seenEdges.set(key, 1);
      }
      const label = t ? (t.name || t.ref || "") + "|" + (t.highway || "") + "|" + (t.bridge ? "b" : "") + (t.tunnel ? "t" : "") : "(off network)";
      const last = streets[streets.length - 1];
      if (last && last._label === label) {
        last.to_m = s + segLen;
        if (wid != null && !last.way_ids.includes(wid)) last.way_ids.push(wid);
      } else {
        streets.push({
          _label: label, from_m: s, to_m: s + segLen,
          name: t ? t.name || "" : "(off the OSM network)", ref: t ? t.ref || "" : "", highway: t ? t.highway || "" : "",
          lanes: t ? t.lanes || "" : "", width: t ? t.width || t["est_width"] || "" : "", surface: t ? t.surface || "" : "",
          maxspeed: t ? t.maxspeed || "" : "", oneway: t ? !!OsmGraph.isOneway(t) : false,
          bridge: !!(t && t.bridge && t.bridge !== "no"), tunnel: !!(t && t.tunnel && t.tunnel !== "no"),
          lit: !!(t && t.lit === "yes"), sidewalk: t ? t.sidewalk || "" : "",
          way_ids: wid != null ? [wid] : [],
        });
      }
      s += segLen;
    }
    R.streets = streets.map((x) => { const o = Object.assign({}, x); delete o._label; o.from_m = Math.round(o.from_m); o.to_m = Math.round(o.to_m); return o; });
    R.repeatedEdges = repeated;
    R.corners = Geo.corners(R.coords, cm, { closed: R.closed });
    R.crossings = Geo.selfCrossings(R.coords, cm);
    if (R.closed) {
      // shoelace in a local frame: negative area = clockwise
      const loc = Geo.makeLocal(R.coords[0]);
      let a = 0;
      for (let k = 0; k < R.coords.length - 1; k++) {
        const p = loc.fwd(R.coords[k]), q = loc.fwd(R.coords[k + 1]);
        a += p[0] * q[1] - q[0] * p[1];
      }
      R.clockwise = a < 0;
    }
  }

  /** Timing lines stay where they are on the map when the route changes: each one the user placed
      keeps a lon / lat anchor and is projected back onto the new route (if still within 50 m). */
  function anchorOf(s) {
    const p = Geo.pointAt(st.route.coords, cum(), s).p;
    return [+p[0].toFixed(7), +p[1].toFixed(7)];
  }
  function reanchor(ll, fallback) {
    if (!ll) return fallback;
    const pr = Geo.project(st.route.coords, cum(), ll);
    return pr.dist < 50 ? Math.round(pr.s) : fallback;
  }
  function adjustTiming(prevLen) {
    const t = st.timing, L = L_();
    if (st.type === "point_to_point") {
      const pad = Math.min(30, L * 0.05);
      if (t.auto || !prevLen) {
        t.start_m = Math.round(pad); t.finish_m = Math.round(L - pad);
        t.start_ll = null; t.finish_ll = null;
      } else {
        t.start_m = reanchor(t.start_ll, t.start_m);
        t.finish_m = reanchor(t.finish_ll, t.finish_m);
        t.start_m = Math.max(0, Math.min(L - 10, t.start_m));
        t.finish_m = Math.max(t.start_m + 10, Math.min(L, t.finish_m));
      }
    } else {
      t.start_m = Math.max(0, Math.min(L, reanchor(t.start_ll, t.start_m || 0)));
    }
    t.splits.forEach((sp) => { sp.at_m = reanchor(sp.ll, sp.at_m); });
    t.splits = t.splits.filter((sp) => sp.at_m >= 0 && sp.at_m <= L).sort((a, b) => a.at_m - b.at_m);
    renumberSplits();
  }
  function renumberSplits() {
    // default names follow the order along the route; names the user typed stay
    st.timing.splits.forEach((sp, i) => {
      if (/^(Sector|Split) \d+$/.test(sp.name)) sp.name = st.type === "circuit" ? `Sector ${i + 2}` : `Split ${i + 1}`;
    });
  }

  function reprojectSurvey() {
    const R = st.route, cm = cum();
    st.survey.segments.forEach((g) => {
      if (g.from_ll) g.from_m = Math.round(Geo.project(R.coords, cm, g.from_ll).s);
      if (g.to_ll) g.to_m = Math.round(Geo.project(R.coords, cm, g.to_ll).s);
    });
    st.survey.coverage = st.survey.coverage.filter((c) => {
      if (c.lat == null && c.slat == null) return true;
      const p = [c.slon != null ? c.slon : c.lon, c.slat != null ? c.slat : c.lat];
      const pr = Geo.project(R.coords, cm, p);
      c.at_m = Math.round(pr.s);
      return pr.dist < 60;
    });
  }

  // --------------------------------------------------------------------------------- elevation
  let elevTimer = null;
  function routeHash() {
    const R = st.route;
    if (!R) return "";
    const c = R.coords;
    return `${c.length}:${c[0]}:${c[c.length - 1]}:${Math.round(R.length)}`;
  }
  function scheduleElevation(force) {
    clearTimeout(elevTimer);
    if (!st.route) return;
    if (!force && st.elevation && st.elevation.v === 2 && st.elevation.hash === routeHash()) return;
    elevTimer = setTimeout(computeElevation, 600);
  }
  async function computeElevation() {
    const seq = ++elevSeq;
    try {
      status("Elevation…", true);
      const prof = await Elevation.profile(st.route.coords, cum(), (m) => status(m, true));
      if (seq !== elevSeq) return;
      prof.hash = routeHash(); prof.v = 2;
      st.elevation = prof;
      status(`Elevation from ${prof.source}`);
      drawRoute(); renderSummary(); drawProfile(); save();
      computeFrame();
    } catch (err) {
      if (seq !== elevSeq) return;
      status("");
      toast("Elevation failed: " + err.message, true);
    }
  }

  async function computeFrame() {
    if (!st.route) return null;
    const fr = Geo.mapFrame(st.route.coords, st.detail.map_margin_m);
    const half = fr.extent_m / 2;
    const c = fr.center_lonlat;
    const dLat = half / 111320, dLon = half / (111320 * Math.cos(c[1] * Math.PI / 180));
    try {
      const rg = await Elevation.frameRange([c[0] - dLon, c[1] - dLat, c[0] + dLon, c[1] + dLat]);
      const routeMin = st.elevation ? st.elevation.stats.min_m : rg.min_m;
      const routeMax = st.elevation ? st.elevation.stats.max_m : rg.max_m;
      const lo = Math.min(rg.min_m, routeMin), hi = Math.max(rg.max_m, routeMax);
      const base = Math.floor((lo - 30) / 10) * 10;
      fr.elev = { min_m: lo, max_m: hi, terrain_base_z: base, max_height_m: Math.ceil((hi + 60 - base) / 10) * 10 };
    } catch (e) { /* frame without heights */ }
    st.frame = fr;
    renderFrame(); save();
    return fr;
  }

  async function placeLookup() {
    const R = st.route;
    if (!R) return;
    const p = R.coords[0];
    if (st.place && st.place.lat != null && Geo.haversine(p, [st.place.lon, st.place.lat]) < 3000) return;
    try {
      const j = await nominatim(`/reverse?format=jsonv2&lat=${p[1]}&lon=${p[0]}&zoom=12&addressdetails=1`);
      const a = j.address || {};
      st.place = {
        country: a.country || "", country_code: (a.country_code || "").toLowerCase(),
        region: a.state || a.region || "", county: a.county || a.province || a.state_district || "",
        town: a.city || a.town || a.village || a.municipality || a.hamlet || "",
        lat: +p[1].toFixed(5), lon: +p[0].toFixed(5),
      };
      save(); renderSummary();
    } catch (e) { /* offline */ }
  }

  // ------------------------------------------------------------------------------------- drawing
  const GRADE_COLORS = [[3, "#2f9e44"], [6, "#a3b80f"], [9, "#f08c00"], [12, "#e8590c"], [16, "#c92a2a"], [999, "#7b1fa2"]];
  function gradeColor(g) { const a = Math.abs(g); for (const [lim, c] of GRADE_COLORS) if (a < lim) return c; return "#7b1fa2"; }

  function drawAll() {
    drawRoute(); drawMarks(); drawCorners(); drawCoverage(); drawSurvey(); drawNotes(); drawWaypointMarkers(); showSvPos();
    renderSummary(); renderTiming(); renderSegments(); renderNotes(); renderFrame(); drawProfile();
  }

  function drawRoute() {
    G.route.clearLayers();
    const R = st.route;
    if (!R) return;
    const ll = R.coords.map((p) => [p[1], p[0]]);
    G.route.addLayer(L.polyline(ll, { color: "#111", weight: 8, opacity: 0.55, interactive: false }));
    const E = st.elevation;
    if (E && E.hash === routeHash()) {
      // colour by grade: one polyline per run of equal colour
      const cm = cum();
      let run = [], col = null;
      const flush = () => { if (run.length > 1) G.route.addLayer(L.polyline(run, { color: col, weight: 5, opacity: 1, interactive: false })); };
      let k = 0;
      for (let i = 0; i < R.coords.length; i++) {
        while (k < E.s.length - 1 && E.s[k + 1] <= cm[i]) k++;
        const c = gradeColor(E.grade[k] || 0);
        const p = [R.coords[i][1], R.coords[i][0]];
        if (c !== col) { if (run.length) { run.push(p); flush(); } run = [p]; col = c; } else run.push(p);
      }
      flush();
    } else {
      G.route.addLayer(L.polyline(ll, { color: "#d6402b", weight: 5, opacity: 1, interactive: false }));
    }
    R.legs.filter((l) => l.mode === "direct").forEach((l) => {
      const a = Geo.pointAt(R.coords, cum(), l.s0).p, b = Geo.pointAt(R.coords, cum(), l.s1).p;
      G.route.addLayer(L.polyline([[a[1], a[0]], [b[1], b[0]]], { color: "#fff", weight: 2, dashArray: "6 6", interactive: false }));
    });
    // arrows
    const L0 = R.length, nArrows = Math.min(40, Math.max(4, Math.round(L0 / 400)));
    for (let k = 1; k < nArrows; k++) {
      const pa = Geo.pointAt(R.coords, cum(), (L0 * k) / nArrows);
      G.route.addLayer(L.marker([pa.p[1], pa.p[0]], { interactive: false, keyboard: false,
        icon: L.divIcon({ className: "", html: `<div class="arrow-icon" style="transform:rotate(${pa.bearing}deg)">▲</div>`, iconSize: [14, 14], iconAnchor: [7, 7] }) }));
    }
    const hit = L.polyline(ll, { color: "#000", weight: 22, opacity: 0, bubblingMouseEvents: false });
    hit.on("click", (e) => routeMenu(e.latlng));
    hit.on("mousemove", (e) => {
      const pr = Geo.project(R.coords, cum(), [e.latlng.lng, e.latlng.lat]);
      showHover(pr.s, true);
    });
    hit.on("mouseout", () => hideHover());
    G.route.addLayer(hit);
  }

  function crossLine(s, half, color, label) {
    const pa = Geo.pointAt(st.route.coords, cum(), s);
    const a = Geo.offset(pa.p, pa.bearing - 90, half), b = Geo.offset(pa.p, pa.bearing + 90, half);
    G.marks.addLayer(L.polyline([[a[1], a[0]], [b[1], b[0]]], { color: "#fff", weight: 7, opacity: 0.95, interactive: false }));
    G.marks.addLayer(L.polyline([[a[1], a[0]], [b[1], b[0]]], { color, weight: 4, dashArray: color === "#111" ? "3 3" : null, interactive: false }));
    if (label) G.marks.addLayer(L.marker([b[1], b[0]], { interactive: false, icon: L.divIcon({ className: "", html: `<span class="line-label">${esc(label)}</span>`, iconSize: null, iconAnchor: [-4, 8] }) }));
  }

  function drawMarks() {
    G.marks.clearLayers();
    const R = st.route;
    if (!R) return;
    const t = st.timing;
    if (st.type === "circuit") {
      crossLine(t.start_m || 0, 9, "#111", "START / FINISH");
      // grid slots behind the line
      for (let k = 0; k < t.grid_slots; k++) {
        let s = (t.start_m || 0) - 6 - k * t.grid_spacing_m;
        while (s < 0) s += R.length;
        const pa = Geo.pointAt(R.coords, cum(), s);
        const q = Geo.offset(pa.p, pa.bearing + (k % 2 ? 90 : -90), 1.6);
        G.marks.addLayer(L.circleMarker([q[1], q[0]], { radius: 3, color: "#111", weight: 1, fillColor: "#fff", fillOpacity: 1, interactive: false }));
      }
      if (t.pits === "area" && t.pit_lat != null) {
        G.marks.addLayer(L.marker([t.pit_lat, t.pit_lon], { icon: L.divIcon({ className: "", html: `<div class="pit-icon">P</div>`, iconSize: [24, 24], iconAnchor: [12, 12] }), title: "Pit area" }));
      }
    } else {
      crossLine(t.start_m, 9, "#1f8a4c", "START");
      crossLine(t.finish_m, 9, "#111", "FINISH");
    }
    t.splits.forEach((sp, i) => crossLine(sp.at_m, 7, "#2563eb", sp.name || `S${i + 1}`));
  }

  function drawCorners() {
    G.corners.clearLayers();
    const R = st.route;
    if (!R || !R.corners) return;
    R.corners.filter((c) => c.hairpin || c.radius_m < 30).forEach((c) => {
      G.corners.addLayer(L.circleMarker([c.apex[1], c.apex[0]], { radius: 5, color: c.hairpin ? "#7b1fa2" : "#e8590c", weight: 2, fillOpacity: 0.2 })
        .bindTooltip(`${c.hairpin ? "Hairpin" : "Tight corner"} ${c.dir === "L" ? "left" : "right"} · R ≈ ${c.radius_m} m · ${c.turn_deg}° · ${km(c.apex_m)} km`));
    });
  }

  function drawCoverage() {
    G.coverage.clearLayers();
    st.survey.coverage.forEach((c) => {
      const lat = c.slat != null ? c.slat : c.lat, lon = c.slon != null ? c.slon : c.lon;
      if (lat == null) return;
      G.coverage.addLayer(L.circleMarker([lat, lon], { radius: 3, weight: 0, fillColor: c.pano_id ? "#2f9e44" : "#c92a2a", fillOpacity: 0.9 })
        .bindTooltip(c.pano_id ? `${km(c.at_m)} km · ${c.date || "date?"} · ${c.offset_m} m off` : `${km(c.at_m)} km · no Street View`)
        .on("click", (e) => { L.DomEvent.stopPropagation(e); openSvAt(c.at_m); }));
    });
  }

  const SIDE_COLORS = { guardrail: "#1c7ed6", wall: "#868e96", retaining: "#5c4033", kerb: "#e03131", fence: "#d9a400", hedge: "#2b8a3e", building: "#7048e8", rock: "#495057", barrier: "#0b7285" };
  function sideColor(code) {
    if (!code || code === "none") return null;
    if (/guardrail|armco|cable/.test(code)) return SIDE_COLORS.guardrail;
    if (/retaining/.test(code)) return SIDE_COLORS.retaining;
    if (/wall|parapet/.test(code)) return SIDE_COLORS.wall;
    if (/kerb|sidewalk/.test(code)) return SIDE_COLORS.kerb;
    if (/fence/.test(code)) return SIDE_COLORS.fence;
    if (/hedge/.test(code)) return SIDE_COLORS.hedge;
    if (/building/.test(code)) return SIDE_COLORS.building;
    if (/rock/.test(code)) return SIDE_COLORS.rock;
    return SIDE_COLORS.barrier;
  }

  function drawSurvey() {
    G.survey.clearLayers();
    const R = st.route;
    if (!R) return;
    st.survey.segments.forEach((g) => {
      const a = Math.max(0, Math.min(g.from_m, g.to_m)), b = Math.min(R.length, Math.max(g.from_m, g.to_m));
      if (b - a < 1) return;
      const pts = [];
      for (let s = a; s <= b; s += 5) pts.push(Geo.pointAt(R.coords, cum(), s).p);
      pts.push(Geo.pointAt(R.coords, cum(), b).p);
      const half = (g.width_m || 5) / 2 + 2;
      [["left", half], ["right", -half]].forEach(([side, d]) => {
        const col = sideColor(g[side]);
        if (!col) return;
        const line = Geo.offsetLine(pts, d).map((p) => [p[1], p[0]]);
        G.survey.addLayer(L.polyline(line, { color: col, weight: 4, opacity: 0.9 })
          .bindTooltip(`${side}: ${Vocab.roadside(g[side]).label}${g[side + "_height_m"] ? ` (${g[side + "_height_m"]} m)` : ""} · ${km(a)}–${km(b)} km`));
      });
    });
  }

  function drawNotes() {
    G.notes.clearLayers();
    st.annotations.forEach((a) => {
      const k = Vocab.annotation(a.kind);
      const m = L.marker([a.lat, a.lon], {
        draggable: true, title: a.text,
        icon: L.divIcon({ className: "", html: `<div class="note-icon" style="background:${k.color}">${esc(k.label[0])}</div>`, iconSize: [20, 20], iconAnchor: [10, 10] }),
      });
      m.bindTooltip(`${k.label}${a.side ? ` (${a.side})` : ""}: ${a.text || ""}`);
      m.on("dragend", () => { const ll = m.getLatLng(); a.lat = ll.lat; a.lon = ll.lng; save(); renderNotes(); });
      m.on("click", (e) => { L.DomEvent.stopPropagation(e); editAnnotation(a); });
      G.notes.addLayer(m);
    });
  }

  function drawNetwork() {
    G.network.clearLayers();
    if (map.getZoom() < 13) return;
    const b = map.getBounds();
    const ways = OsmGraph.waysIn([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()], 6000);
    ways.forEach((w) => {
      const hw = w.tags.highway;
      const col = OsmGraph.PATHS.has(hw) ? "#adb5bd" : hw === "track" ? "#a0522d" : hw === "raceway" ? "#e64980" : "#4dabf7";
      G.network.addLayer(L.polyline(w.coords.map((p) => [p[1], p[0]]), { color: col, weight: 2, opacity: 0.8, interactive: false }));
    });
  }

  function showHover(s, fromMap) {
    if (!st.route) return;
    const p = Geo.pointAt(st.route.coords, cum(), s).p;
    hoverMarker.setLatLng([p[1], p[0]]);
    if (!G.hover.hasLayer(hoverMarker)) G.hover.addLayer(hoverMarker);
    profileCursor = s;
    drawProfile();
    if (fromMap) tipAt(s);
  }
  function hideHover() { G.hover.clearLayers(); profileCursor = null; drawProfile(); $("#profileTip").classList.add("hidden"); }

  // ------------------------------------------------------------------------------------- profile
  let profileCursor = null;
  const canvas = $("#profileCanvas");
  function profileGeom() {
    const r = canvas.getBoundingClientRect();
    return { w: r.width, h: r.height, padL: 44, padR: 10, padT: 8, padB: 18 };
  }
  function elevAt(s) {
    const E = st.elevation;
    if (!E) return null;
    let k = 0;
    while (k < E.s.length - 1 && E.s[k + 1] <= s) k++;
    const k2 = Math.min(E.s.length - 1, k + 1);
    const t = E.s[k2] > E.s[k] ? (s - E.s[k]) / (E.s[k2] - E.s[k]) : 0;
    return { z: E.zs[k] + (E.zs[k2] - E.zs[k]) * t, g: E.grade[k] };
  }
  function drawProfile() {
    const dpr = window.devicePixelRatio || 1;
    const g = profileGeom();
    if (!g.w || !g.h) return;
    canvas.width = Math.round(g.w * dpr); canvas.height = Math.round(g.h * dpr);
    const c = canvas.getContext("2d");
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, g.w, g.h);
    const css = getComputedStyle(document.documentElement);
    const muted = css.getPropertyValue("--muted").trim(), line = css.getPropertyValue("--line").trim(), text = css.getPropertyValue("--text").trim();
    const E = st.elevation, R = st.route;
    $("#profileInfo").textContent = E ? `${E.source}, every ${E.step_m} m · colour = grade over ${E.grade_window_m || 100} m · planning preview` : (R ? "computing…" : "draw a route");
    if (!E || !R || E.hash !== routeHash()) return;
    const L0 = E.s[E.s.length - 1];
    let zmin = Math.min(...E.zs), zmax = Math.max(...E.zs);
    const padZ = Math.max(5, (zmax - zmin) * 0.08);
    zmin -= padZ; zmax += padZ;
    const X = (s) => g.padL + (s / L0) * (g.w - g.padL - g.padR);
    const Y = (z) => g.padT + (1 - (z - zmin) / (zmax - zmin)) * (g.h - g.padT - g.padB);
    // grid
    c.strokeStyle = line; c.lineWidth = 1; c.fillStyle = muted; c.font = "10px -apple-system, sans-serif";
    const zStep = niceStep((zmax - zmin) / 4);
    for (let z = Math.ceil(zmin / zStep) * zStep; z <= zmax; z += zStep) {
      c.beginPath(); c.moveTo(g.padL, Y(z)); c.lineTo(g.w - g.padR, Y(z)); c.stroke();
      c.fillText(`${Math.round(z)} m`, 4, Y(z) + 3);
    }
    const sStep = niceStep(L0 / 8);
    for (let s = 0; s <= L0; s += sStep) c.fillText(`${(s / 1000).toFixed(sStep < 1000 ? 1 : 0)} km`, X(s) - 10, g.h - 4);
    // fill by grade
    for (let i = 0; i < E.s.length - 1; i++) {
      c.fillStyle = gradeColor(E.grade[i]);
      c.globalAlpha = 0.85;
      c.beginPath();
      c.moveTo(X(E.s[i]), Y(zmin)); c.lineTo(X(E.s[i]), Y(E.zs[i])); c.lineTo(X(E.s[i + 1]), Y(E.zs[i + 1])); c.lineTo(X(E.s[i + 1]), Y(zmin));
      c.closePath(); c.fill();
    }
    c.globalAlpha = 1;
    c.strokeStyle = text; c.lineWidth = 1.2; c.beginPath();
    E.s.forEach((s, i) => (i ? c.lineTo(X(s), Y(E.zs[i])) : c.moveTo(X(s), Y(E.zs[i])))); c.stroke();
    // timing lines
    const vline = (s, col, dash) => { c.strokeStyle = col; c.setLineDash(dash || []); c.lineWidth = 1.5; c.beginPath(); c.moveTo(X(s), g.padT); c.lineTo(X(s), g.h - g.padB); c.stroke(); c.setLineDash([]); };
    const t = st.timing;
    if (st.type === "circuit") vline(t.start_m || 0, text);
    else { vline(t.start_m, "#1f8a4c"); vline(t.finish_m, text); }
    t.splits.forEach((sp) => vline(sp.at_m, "#2563eb", [4, 3]));
    // hairpins
    (R.corners || []).filter((k) => k.hairpin).forEach((k) => {
      c.fillStyle = "#7b1fa2"; c.beginPath(); c.moveTo(X(k.apex_m), g.h - g.padB); c.lineTo(X(k.apex_m) - 4, g.h - g.padB + 6); c.lineTo(X(k.apex_m) + 4, g.h - g.padB + 6); c.fill();
    });
    // Street View coverage strip
    st.survey.coverage.forEach((cv) => { c.fillStyle = cv.pano_id ? "#2f9e44" : "#c92a2a"; c.fillRect(X(cv.at_m) - 1, g.padT - 6, 2, 4); });
    // survey segments strip
    st.survey.segments.forEach((sg) => { c.fillStyle = "#1c7ed6"; c.fillRect(X(Math.min(sg.from_m, sg.to_m)), g.padT - 2, Math.max(2, X(Math.abs(sg.to_m - sg.from_m)) - g.padL), 2); });
    if (svActive) vline(svS, "#f6b400");
    if (profileCursor != null) vline(profileCursor, text, [2, 2]);
  }
  function niceStep(x) {
    const p = Math.pow(10, Math.floor(Math.log10(Math.max(x, 1e-6))));
    for (const m of [1, 2, 2.5, 5, 10]) if (x <= m * p) return m * p;
    return 10 * p;
  }
  function sFromCanvas(ev) {
    const g = profileGeom(), E = st.elevation;
    if (!E) return null;
    const r = canvas.getBoundingClientRect();
    const x = ev.clientX - r.left;
    const L0 = E.s[E.s.length - 1];
    return Math.max(0, Math.min(L0, ((x - g.padL) / (g.w - g.padL - g.padR)) * L0));
  }
  function tipAt(s) {
    const tip = $("#profileTip"), e = elevAt(s);
    if (!e) return;
    const g = profileGeom(), L0 = st.elevation.s[st.elevation.s.length - 1];
    tip.textContent = `${km(s)} km · ${Math.round(e.z)} m · ${e.g > 0 ? "+" : ""}${e.g.toFixed(1)} %`;
    tip.style.left = Math.min(g.w - 150, g.padL + (s / L0) * (g.w - g.padL - g.padR) + 8) + "px";
    tip.classList.remove("hidden");
  }
  canvas.addEventListener("mousemove", (ev) => { const s = sFromCanvas(ev); if (s == null) return; showHover(s); tipAt(s); });
  canvas.addEventListener("mouseleave", hideHover);
  canvas.addEventListener("click", (ev) => {
    const s = sFromCanvas(ev);
    if (s == null) return;
    const p = Geo.pointAt(st.route.coords, cum(), s).p;
    map.panTo([p[1], p[0]]);
    if (svActive) openSvAt(s);
  });
  window.addEventListener("resize", () => drawProfile());
  OsmGraph.onChange(() => { if (map.hasLayer(G.network)) drawNetwork(); });
  $("#btnProfileToggle").addEventListener("click", () => {
    $("#profile").classList.toggle("collapsed");
    setTimeout(() => { map.invalidateSize(); drawProfile(); }, 50);
  });

  // ------------------------------------------------------------------------------------- summary
  function renderSummary() {
    const box = $("#summary"), R = st.route;
    if (!R) { box.className = "summary empty"; box.textContent = "No route yet. Set at least two stops."; $("#streets").innerHTML = ""; return; }
    const E = st.elevation && st.elevation.hash === routeHash() ? st.elevation : null;
    const hp = (R.corners || []).filter((c) => c.hairpin).length;
    const tight = (R.corners || []).length ? Math.min(...R.corners.map((c) => c.radius_m)) : null;
    const stats = [
      [km(R.length) + " km", st.type === "circuit" ? "lap" : "length"],
      [E ? `${Math.round(E.stats.start_m)}→${Math.round(E.stats.end_m)} m` : "…", "elevation"],
      [E ? `+${E.stats.gain_m} / −${E.stats.loss_m}` : "…", "climb / descent m"],
      [E ? `${E.stats.max_grade_pct} %` : "…", `steepest ${E ? E.stats.grade_window_m : 100} m`],
      [`${(R.corners || []).length} / ${hp}`, "corners / hairpins"],
      [tight != null ? `${tight} m` : "–", "tightest radius"],
    ];
    if (st.place) stats.push([esc(st.place.country_code.toUpperCase() || "?"), esc(st.place.town || st.place.region || st.place.country)]);
    if (st.type === "circuit" && R.clockwise != null) stats.push([R.clockwise ? "CW" : "CCW", "direction"]);
    box.className = "summary";
    box.innerHTML = stats.map(([b, s]) => `<div class="stat"><b>${b}</b><span>${s}</span></div>`).join("");
    $("#streets").innerHTML = (R.streets || []).filter((s) => s.to_m - s.from_m >= 15).map((s, i) =>
      `<div class="street" data-s="${s.from_m}"><span class="km">${km(s.from_m)}</span><span>${esc([s.name, s.ref].filter(Boolean).join(" / ") || "(unnamed)")}${s.bridge ? " · bridge" : ""}${s.tunnel ? " · tunnel" : ""}</span><span class="cls">${esc(s.highway)}${s.surface ? " · " + esc(s.surface) : ""}</span></div>`).join("");
    $$("#streets .street").forEach((el) => el.addEventListener("click", () => {
      const p = Geo.pointAt(R.coords, cum(), +el.dataset.s + 10).p;
      map.setView([p[1], p[0]], Math.max(map.getZoom(), 16));
    }));
  }

  function renderFrame() {
    const box = $("#frameInfo");
    if (!st.route) { box.className = "kv empty"; box.textContent = "Draw a route first."; return; }
    const f = st.frame || Geo.mapFrame(st.route.coords, st.detail.map_margin_m);
    box.className = "kv";
    box.innerHTML = [
      ["CRS", `EPSG:${f.epsg} (UTM ${f.utm_zone}${f.hemisphere})`],
      ["Centre (E, N)", f.center_utm.join(", ")],
      ["Terrain", `${f.terrain_size_px} px × ${f.meters_per_px} m = ${km(f.extent_m)} km`],
      ["Route box", `${km(f.route_extent_m[0])} × ${km(f.route_extent_m[1])} km`],
      ["Heights", f.elev ? `${Math.round(f.elev.min_m)}–${Math.round(f.elev.max_m)} m` : "…"],
    ].map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("") + (f.fits ? "" : `<dt></dt><dd style="color:var(--err)">Too big for one 8192 px grid</dd>`);
  }

  // -------------------------------------------------------------------------------------- timing
  function setTiming(which, s) {
    pushUndo();
    const t = st.timing;
    s = Math.round(s);
    if (which === "start") { t.start_m = Math.min(s, t.finish_m - 10); t.auto = false; }
    else if (which === "finish") { t.finish_m = Math.max(s, t.start_m + 10); t.auto = false; }
    else if (which === "sf") t.start_m = s;
    anchorTiming();
    drawMarks(); renderTiming(); drawProfile(); save();
  }
  function addSplit(s) {
    const t = st.timing;
    if (t.splits.length >= 2) { toast("Assetto Corsa supports at most 2 intermediate timing lines (3 sectors)", true); return; }
    pushUndo();
    if (s == null) {
      const a = st.type === "circuit" ? 0 : t.start_m, b = st.type === "circuit" ? L_() : t.finish_m;
      const pts = [a, ...t.splits.map((x) => x.at_m).sort((x, y) => x - y), b];
      let best = 0;
      for (let i = 1; i < pts.length; i++) if (pts[i] - pts[i - 1] > pts[best + 1] - pts[best]) best = i - 1;
      s = (pts[best] + pts[best + 1]) / 2;
    }
    t.splits.push({ name: st.type === "circuit" ? `Sector ${t.splits.length + 2}` : `Split ${t.splits.length + 1}`, at_m: Math.round(s) });
    t.splits.sort((x, y) => x.at_m - y.at_m);
    renumberSplits();
    anchorTiming();
    drawMarks(); renderTiming(); drawProfile(); save();
  }
  function anchorTiming() {
    if (!st.route) return;
    const t = st.timing;
    t.start_ll = anchorOf(t.start_m);
    if (st.type === "point_to_point") t.finish_ll = anchorOf(t.finish_m);
    t.splits.forEach((sp) => { sp.ll = anchorOf(sp.at_m); });
  }
  function setPit(pt) { st.timing.pit_lat = pt.lat; st.timing.pit_lon = pt.lon; drawMarks(); renderTiming(); save(); }

  function renderTiming() {
    const t = st.timing, L = Math.round(L_()), isC = st.type === "circuit";
    $$("[data-only]").forEach((el) => el.classList.toggle("hidden", el.dataset.only !== st.type));
    $("#splitsTitle").textContent = isC ? "Sector lines" : "Splits";
    [["#startRange", "#startM", "start_m"], ["#finishRange", "#finishM", "finish_m"], ["#sfRange", "#sfM", "start_m"]].forEach(([r, n, k]) => {
      $(r).max = L; $(n).max = L; $(r).value = t[k] || 0; $(n).value = t[k] || 0;
    });
    $("#timedLen").textContent = L ? `Timed run ${km(t.finish_m - t.start_m)} km of ${km(L)} km.` : "";
    $("#laps").value = t.laps; $("#gridSlots").value = t.grid_slots; $("#gridSpacing").value = t.grid_spacing_m; $("#pitCount").value = t.pit_count;
    $("#pitMode").value = t.pits;
    $("#pitPickRow").classList.toggle("hidden", t.pits !== "area");
    $("#pitWhere").textContent = t.pit_lat != null ? `${t.pit_lat.toFixed(5)}, ${t.pit_lon.toFixed(5)}` : "not set";
    const list = $("#splitList");
    list.innerHTML = "";
    t.splits.forEach((sp, i) => {
      const row = document.createElement("div");
      row.className = "item item-split";
      row.innerHTML = `<input type="text" value="${esc(sp.name)}"><input type="number" class="num" min="0" max="${L}" value="${sp.at_m}"><button type="button" class="ghost small">✕</button>`;
      const [nm, at] = $$("input", row);
      nm.addEventListener("change", () => { sp.name = nm.value; drawMarks(); save(); });
      at.addEventListener("change", () => { sp.at_m = Math.max(0, Math.min(L, +at.value)); t.splits.sort((a, b) => a.at_m - b.at_m); anchorTiming(); drawMarks(); drawProfile(); renderTiming(); save(); });
      $("button", row).addEventListener("click", () => { t.splits.splice(i, 1); drawMarks(); drawProfile(); renderTiming(); save(); });
      list.appendChild(row);
    });
    if (!t.splits.length) list.innerHTML = `<p class="hint">None.</p>`;
  }
  function bindTiming() {
    const t = () => st.timing;
    const upd = () => { anchorTiming(); drawMarks(); drawProfile(); renderTiming(); save(); };
    [["#startRange", "start_m"], ["#startM", "start_m"]].forEach(([sel]) => $(sel).addEventListener("input", (e) => { t().start_m = Math.min(+e.target.value, t().finish_m - 10); t().auto = false; upd(); }));
    [["#finishRange"], ["#finishM"]].forEach(([sel]) => $(sel).addEventListener("input", (e) => { t().finish_m = Math.max(+e.target.value, t().start_m + 10); t().auto = false; upd(); }));
    ["#sfRange", "#sfM"].forEach((sel) => $(sel).addEventListener("input", (e) => { t().start_m = +e.target.value; upd(); }));
    $("#laps").addEventListener("change", (e) => { t().laps = Math.max(1, +e.target.value); save(); });
    $("#gridSlots").addEventListener("change", (e) => { t().grid_slots = Math.max(1, +e.target.value); if (t().pits === "grid") t().pit_count = t().grid_slots; upd(); });
    $("#gridSpacing").addEventListener("change", (e) => { t().grid_spacing_m = Math.max(4, +e.target.value); upd(); });
    $("#pitCount").addEventListener("change", (e) => { t().pit_count = Math.max(1, +e.target.value); save(); });
    $("#pitMode").addEventListener("change", (e) => { t().pits = e.target.value; upd(); });
    $("#btnPickPit").addEventListener("click", () => startPick({ kind: "pit" }, "Click the pit / paddock area"));
    $("#btnAddSplit").addEventListener("click", () => { if (st.route) addSplit(); });
  }

  // -------------------------------------------------------------------------------------- detail
  function bindDetail() {
    Object.keys(DEFAULT_DETAIL).forEach((k) => {
      const el = $("#d_" + k);
      if (!el) return;
      const ev = el.type === "checkbox" ? "change" : "input";
      el.addEventListener(ev, () => {
        st.detail[k] = el.type === "checkbox" ? el.checked : el.type === "number" ? +el.value : el.value;
        if (k === "map_margin_m") { st.frame = null; renderFrame(); clearTimeout(bindDetail.t); bindDetail.t = setTimeout(computeFrame, 800); }
        if (k === "kerbs") $("#racingKerbRow").classList.toggle("hidden", st.detail.kerbs !== "racing");
        save();
      });
    });
  }
  function renderDetail() {
    Object.keys(DEFAULT_DETAIL).forEach((k) => {
      const el = $("#d_" + k);
      if (!el) return;
      if (el.type === "checkbox") el.checked = !!st.detail[k]; else el.value = st.detail[k];
    });
    $("#racingKerbRow").classList.toggle("hidden", st.detail.kerbs !== "racing");
  }

  // ------------------------------------------------------------------------------------- survey
  function fillSelect(sel, opts) { sel.innerHTML = opts.map((o) => typeof o === "string" ? `<option value="${o}">${o}</option>` : `<option value="${o.id}">${esc(o.label)}${o.pipeline ? "" : " *"}</option>`).join(""); }
  function initSurveyForm() {
    const f = $("#segForm");
    fillSelect(f.edge_lines, Vocab.EDGE_LINES); fillSelect(f.centre_line, Vocab.CENTRE_LINE);
    fillSelect(f.left, Vocab.ROADSIDE); fillSelect(f.right, Vocab.ROADSIDE); fillSelect(f.surface, Vocab.SURFACE);
    f.addEventListener("submit", (e) => { e.preventDefault(); saveSegment(); });
    $("#btnSegCancel").addEventListener("click", () => { f.classList.add("hidden"); f.dataset.edit = ""; });
    $$("[data-use]", f).forEach((b) => b.addEventListener("click", () => {
      if (!requireSv()) return;
      f[b.dataset.use].value = Math.round(svS);
    }));
    $("#btnNewSeg").addEventListener("click", () => newSegment({}));
  }
  function newSegment(pre) {
    if (!st.route) { toast("Draw a route first"); return; }
    switchTab("survey");
    const f = $("#segForm");
    f.reset(); f.dataset.edit = "";
    const last = st.survey.segments.slice().sort((a, b) => b.to_m - a.to_m)[0];
    const from = pre.from_m != null ? pre.from_m : last ? last.to_m : 0;
    const vals = Object.assign({ from_m: from, to_m: pre.to_m != null ? pre.to_m : Math.min(Math.round(L_()), from + 150), edge_lines: "none", centre_line: "none", left: "none", right: "none", surface: "worn" },
      last ? { width_m: last.width_m, lanes: last.lanes, edge_lines: last.edge_lines, centre_line: last.centre_line, surface: last.surface } : {}, pre);
    Object.entries(vals).forEach(([k, v]) => { if (f[k] && v != null) f[k].value = v; });
    f.classList.remove("hidden");
    f.from_m.focus();
  }
  function editSegment(i) {
    const g = st.survey.segments[i], f = $("#segForm");
    switchTab("survey");
    f.reset(); f.dataset.edit = String(i);
    ["from_m", "to_m", "width_m", "lanes", "edge_lines", "centre_line", "left", "right", "left_height_m", "right_height_m", "surface", "vegetation", "notes"].forEach((k) => { if (g[k] != null) f[k].value = g[k]; });
    f.classList.remove("hidden");
  }
  function saveSegment() {
    const f = $("#segForm");
    const num = (v) => (v === "" || v == null ? null : +v);
    const from = num(f.from_m.value), to = num(f.to_m.value);
    if (from == null || to == null || to <= from) { toast("A segment needs From < To (metres along the route)", true); return; }
    const R = st.route, cm = cum();
    const pa = Geo.pointAt(R.coords, cm, from).p, pb = Geo.pointAt(R.coords, cm, to).p;
    const g = {
      from_m: from, to_m: to, width_m: num(f.width_m.value), lanes: num(f.lanes.value),
      edge_lines: f.edge_lines.value, centre_line: f.centre_line.value,
      left: f.left.value, right: f.right.value, left_height_m: num(f.left_height_m.value), right_height_m: num(f.right_height_m.value),
      surface: f.surface.value, vegetation: f.vegetation.value.trim(), notes: f.notes.value.trim(),
      from_ll: [+pa[0].toFixed(7), +pa[1].toFixed(7)], to_ll: [+pb[0].toFixed(7), +pb[1].toFixed(7)],
      source: "user (planner)",
    };
    if (f.dataset.edit !== "") st.survey.segments[+f.dataset.edit] = Object.assign(st.survey.segments[+f.dataset.edit], g);
    else st.survey.segments.push(g);
    st.survey.segments.sort((a, b) => a.from_m - b.from_m);
    f.classList.add("hidden"); f.dataset.edit = "";
    renderSegments(); drawSurvey(); drawProfile(); save();
    toast("Segment saved");
  }
  function renderSegments() {
    const list = $("#segList");
    const segs = st.survey.segments;
    if (!segs.length) { list.innerHTML = `<p class="hint">None yet. Optional: Claude surveys whatever you leave out. Codes marked * need new code in the build.</p>`; return; }
    list.innerHTML = "";
    segs.forEach((g, i) => {
      const row = document.createElement("div");
      row.className = "item";
      const sw = (c) => `<span class="swatch" style="background:${sideColor(c) || "transparent"};border:1px solid var(--line)"></span>`;
      row.innerHTML = `<div><b>${km(g.from_m)}–${km(g.to_m)} km</b>${g.width_m ? ` · ${g.width_m} m` : ""} · ${esc(g.surface || "")}
        <div class="meta">${sw(g.left)}L ${esc(Vocab.roadside(g.left).label)}${g.left_height_m ? ` ${g.left_height_m} m` : ""} · ${sw(g.right)}R ${esc(Vocab.roadside(g.right).label)}${g.right_height_m ? ` ${g.right_height_m} m` : ""}</div>
        ${g.notes ? `<div class="meta">${esc(g.notes)}</div>` : ""}</div>
        <div class="btns"><button type="button" class="small" data-a="go">View</button><button type="button" class="small" data-a="edit">Edit</button><button type="button" class="small ghost" data-a="del">✕</button></div>`;
      $("[data-a=go]", row).addEventListener("click", () => openSvAt(g.from_m + 5));
      $("[data-a=edit]", row).addEventListener("click", () => editSegment(i));
      $("[data-a=del]", row).addEventListener("click", () => { segs.splice(i, 1); renderSegments(); drawSurvey(); drawProfile(); save(); });
      list.appendChild(row);
    });
  }

  // ------------------------------------------------------------------------------- street view
  // Street View opens in Google Maps in a separate window (StreetView.open); here we only keep the
  // survey position along the route and show it on our map and profile.
  function showSvPos() {
    G.sv.clearLayers();
    if (!svActive || !st.route) { $("#svPos").textContent = "not open"; return; }
    const pa = Geo.pointAt(st.route.coords, cum(), svS);
    const h = pa.bearing + (svBack() ? 180 : 0);
    G.sv.addLayer(L.marker([pa.p[1], pa.p[0]], { interactive: false, zIndexOffset: 2000,
      icon: L.divIcon({ className: "", html: `<div class="sv-icon" style="transform:rotate(${h}deg)"></div>`, iconSize: [18, 18], iconAnchor: [9, 9] }) }));
    $("#svPos").textContent = `${km(svS)} km`;
    if (!map.getBounds().contains([pa.p[1], pa.p[0]])) map.panTo([pa.p[1], pa.p[0]]);
    drawProfile();
  }
  function svBack() { return $("#svBack").checked; }
  function openSvAt(s) {
    if (!st.route) { toast("Draw a route first"); return; }
    svS = Math.max(0, Math.min(L_(), s)); svActive = true;
    const pa = Geo.pointAt(st.route.coords, cum(), svS);
    if (!StreetView.open(pa.p[0], pa.p[1], pa.bearing + (svBack() ? 180 : 0))) toast("The browser blocked the Street View window: allow pop-ups for this page", true);
    showSvPos();
  }
  function openSvAtLatLon(lon, lat) {
    if (st.route) {
      const pr = Geo.project(st.route.coords, cum(), [lon, lat]);
      if (pr.dist < 30) { openSvAt(pr.s); return; }
    }
    if (!StreetView.open(lon, lat, 0)) toast("The browser blocked the Street View window: allow pop-ups for this page", true);
  }
  function svStep(d) {
    if (!st.route) return;
    const L = L_();
    let s = (svActive ? svS : 0) + (svBack() ? -d : d);
    if (st.type === "circuit") s = ((s % L) + L) % L; else s = Math.max(0, Math.min(L, s));
    openSvAt(s);
  }
  function requireSv() {
    if (svActive) return true;
    toast("Open Street View first (Survey tab), or click the route → Street View here");
    return false;
  }
  function bindSurveyControls() {
    $$(".sv-controls [data-step]").forEach((b) => b.addEventListener("click", () => svStep(+b.dataset.step)));
    $("#btnSvStart").addEventListener("click", () => openSvAt(st.type === "circuit" ? st.timing.start_m || 0 : st.timing.start_m));
    $("#svBack").addEventListener("change", () => { if (svActive) openSvAt(svS); });
    $("#btnSegFromSv").addEventListener("click", () => { if (requireSv()) newSegment({ from_m: Math.round(svS) }); });
    $("#btnSegToSv").addEventListener("click", () => {
      if (!requireSv()) return;
      const f = $("#segForm");
      if (f.classList.contains("hidden")) newSegment({ to_m: Math.round(svS) }); else f.to_m.value = Math.round(svS);
    });
    $("#btnNoteSv").addEventListener("click", () => {
      if (!requireSv()) return;
      const p = Geo.pointAt(st.route.coords, cum(), svS).p;
      editAnnotation(null, { lat: p[1], lon: p[0] });
    });
  }

  // -------------------------------------------------------------------------------- annotations
  function editAnnotation(a, pt) {
    const isNew = !a;
    a = a || { id: uid(), kind: "note", side: "", text: "", lat: pt.lat, lon: pt.lon };
    openModal(isNew ? "New annotation" : "Annotation", `
      <div class="grid2">
        <label class="field">Kind<select id="anKind">${Vocab.ANNOTATION.map((k) => `<option value="${k.id}">${esc(k.label)}</option>`).join("")}</select></label>
        <label class="field">Side of the road<select id="anSide"><option value="">—</option><option value="left">left</option><option value="right">right</option><option value="both">both</option></select></label>
      </div>
      <label class="field">What Claude should know<textarea id="anText" rows="4" placeholder="e.g. 'Village church: model by hand, white dome + bell tower' or 'guardrail ends here, concrete wall starts'"></textarea></label>
      <div class="row gap"><button type="button" class="primary" id="anSave">Save</button>${isNew ? "" : `<button type="button" class="ghost danger" id="anDel">Delete</button>`}<span class="hint">${a.lat.toFixed(5)}, ${a.lon.toFixed(5)}</span></div>`);
    $("#anKind").value = a.kind; $("#anSide").value = a.side || ""; $("#anText").value = a.text || "";
    $("#anText").focus();
    $("#anSave").addEventListener("click", () => {
      a.kind = $("#anKind").value; a.side = $("#anSide").value; a.text = $("#anText").value.trim();
      if (isNew) st.annotations.push(a);
      closeModal(); drawNotes(); renderNotes(); save();
    });
    if (!isNew) $("#anDel").addEventListener("click", () => {
      st.annotations = st.annotations.filter((x) => x !== a);
      closeModal(); drawNotes(); renderNotes(); save();
    });
  }
  function renderNotes() {
    const list = $("#noteList");
    if (!st.annotations.length) { list.innerHTML = `<p class="hint">None. Mark landmarks to model by hand, special walls, kerbs, pit areas…</p>`; return; }
    list.innerHTML = "";
    st.annotations.forEach((a) => {
      const k = Vocab.annotation(a.kind);
      const s = st.route ? Geo.project(st.route.coords, cum(), [a.lon, a.lat]) : null;
      const row = document.createElement("div");
      row.className = "item";
      row.innerHTML = `<div><span class="swatch" style="background:${k.color}"></span><b>${esc(k.label)}</b>${a.side ? ` · ${a.side}` : ""}${s ? ` · ${km(s.s)} km, ${Math.round(s.dist)} m off` : ""}<div class="meta">${esc(a.text || "")}</div></div>
        <div class="btns"><button type="button" class="small" data-a="go">Show</button><button type="button" class="small" data-a="edit">Edit</button></div>`;
      $("[data-a=go]", row).addEventListener("click", () => map.setView([a.lat, a.lon], Math.max(map.getZoom(), 17)));
      $("[data-a=edit]", row).addEventListener("click", () => editAnnotation(a));
      list.appendChild(row);
    });
  }

  // ------------------------------------------------------------------------------- name, notes
  function autoName() {
    if (!st.name && st.waypoints.length >= 2) {
      const a = (st.waypoints[0].label || "").split(",")[0].trim();
      const b = (st.waypoints[st.waypoints.length - 1].label || "").split(",")[0].trim();
      const auto = st.type === "circuit" ? (a ? `${a} circuit` : "") : (a && b && a !== b ? `${a} – ${b}` : "");
      if (auto) { $("#trackName").placeholder = auto; }
    }
    if (!st.slugEdited) {
      st.slug = Geo.slugify(st.name || $("#trackName").placeholder || "track");
      $("#slug").value = st.slug;
    }
  }
  function bindMeta() {
    const name = $("#trackName");
    name.addEventListener("input", () => { st.name = name.value; autoName(); save(); });
    $("#slug").addEventListener("input", (e) => { st.slug = Geo.slugify(e.target.value); st.slugEdited = !!e.target.value; save(); });
    $("#slug").addEventListener("change", (e) => { e.target.value = st.slug; });
    $("#description").addEventListener("input", (e) => { st.description = e.target.value; save(); });
    $("#author").addEventListener("input", (e) => { st.author = e.target.value; save(); });
    $("#notes").addEventListener("input", (e) => { st.notes = e.target.value; save(); });
    $("#setStudio").addEventListener("change", (e) => { settings.studio_root = e.target.value.replace(/\/+$/, ""); localStorage.setItem(LS_SETTINGS, JSON.stringify(settings)); });
    $("#setReference").addEventListener("change", (e) => { settings.reference_pipeline = e.target.value.replace(/\/+$/, ""); localStorage.setItem(LS_SETTINGS, JSON.stringify(settings)); });
  }
  function renderMeta() {
    $("#trackName").value = st.name; $("#slug").value = st.slug; $("#description").value = st.description;
    $("#author").value = st.author; $("#notes").value = st.notes;
    $("#setStudio").value = server ? server.studio_root : settings.studio_root;
    $("#setReference").value = server && server.reference_pipeline ? server.reference_pipeline : settings.reference_pipeline;
    $("#setReference").disabled = !!(server && server.reference_pipeline);
    $("#setStudio").disabled = !!server;
    $("#serverHint").textContent = server ? `Served by serve.py: briefs are saved to ${server.tracks_dir}.` : "Not served by serve.py: you can download the files; run serve.py to save them into the project.";
  }

  function renderTypeToggle() {
    $$("#typeToggle button").forEach((b) => b.classList.toggle("active", b.dataset.type === st.type));
  }
  function bindType() {
    $$("#typeToggle button").forEach((b) => b.addEventListener("click", () => {
      if (st.type === b.dataset.type) return;
      pushUndo();
      st.type = b.dataset.type;
      st.timing.splits = [];
      st.timing.auto = true;
      st.timing.start_ll = null; st.timing.finish_ll = null;
      if (st.type === "circuit") st.timing.start_m = 0;
      changed();
    }));
  }

  // -------------------------------------------------------------------------------------- tabs
  function switchTab(name) {
    $$(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    $$(".panel").forEach((p) => p.classList.toggle("active", p.dataset.panel === name));
  }
  $$(".tabs button").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));

  // ------------------------------------------------------------------------------------- modal
  function openModal(title, html) {
    $("#modalTitle").textContent = title;
    $("#modalBody").innerHTML = html;
    $("#modal").classList.remove("hidden");
  }
  function closeModal() { $("#modal").classList.add("hidden"); $("#modalBody").innerHTML = ""; }
  $("#btnModalClose").addEventListener("click", closeModal);
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });

  // ---------------------------------------------------------------------------------- generate
  function paths() {
    const root = server ? server.studio_root : settings.studio_root;
    const ref = server && server.reference_pipeline ? server.reference_pipeline : settings.reference_pipeline;
    return { studio_root: root, reference_pipeline: ref, track_dir: `${root}/tracks/${st.slug}` };
  }
  function download(name, text, type) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: type || "text/plain" }));
    a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  async function generate() {
    if (!st.name) { st.name = $("#trackName").placeholder && $("#trackName").placeholder !== "Track name" ? $("#trackName").placeholder : ""; $("#trackName").value = st.name; autoName(); }
    if (!st.route) { toast("Draw a route first", true); return; }
    if (!st.name) { toast("Give the track a name", true); $("#trackName").focus(); return; }
    if (!st.slug) { st.slug = Geo.slugify(st.name); $("#slug").value = st.slug; }
    status("Preparing the brief…", true);
    if (!st.frame || !st.frame.elev) await computeFrame();
    if (!st.place) await placeLookup();
    status("");
    const spec = Brief.buildSpec(st, { paths: paths() });
    const md = Brief.markdown(spec, st);
    const gj = Brief.routeGeoJSON(spec);
    const prompt = Brief.prompt(spec);
    const warns = Brief.warnings(st);
    const specText = JSON.stringify(spec, null, 1);
    openModal(`Brief for “${st.name}”`, `
      <ul class="warnings">${warns.length ? warns.map((w) => `<li class="${w.level}">${esc(w.text)}</li>`).join("") : `<li class="info">No warnings.</li>`}</ul>
      <div class="row gap">
        ${server ? `<button type="button" class="primary" id="gSave">Save to tracks/${esc(st.slug)}/</button>` : ""}
        <button type="button" id="gMd">Download BRIEF.md</button>
        <button type="button" id="gJson">Download track.json</button>
        <button type="button" id="gGeo">Download route.geojson</button>
      </div>
      <div id="gSaved"></div>
      <div class="subtabs"><button type="button" class="active" data-v="md">BRIEF.md</button><button type="button" data-v="prompt">Prompt for Claude</button><button type="button" data-v="json">track.json (${Math.round(specText.length / 1024)} KB)</button></div>
      <pre class="preview" id="gPreview"></pre>
      <div class="row gap"><button type="button" id="gCopy">Copy this</button><span class="hint" id="gHint"></span></div>`);
    const views = { md, prompt, json: specText.length > 200000 ? specText.slice(0, 200000) + "\n… (truncated in the preview)" : specText };
    let view = "md";
    const show = () => { $("#gPreview").textContent = views[view]; $$(".subtabs button").forEach((b) => b.classList.toggle("active", b.dataset.v === view)); };
    show();
    $$(".subtabs button").forEach((b) => b.addEventListener("click", () => { view = b.dataset.v; show(); }));
    $("#gCopy").addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(view === "json" ? specText : views[view]); $("#gHint").textContent = "Copied."; }
      catch (e) { $("#gHint").textContent = "Copy failed: select the text and copy it."; }
    });
    $("#gMd").addEventListener("click", () => download("BRIEF.md", md, "text/markdown"));
    $("#gJson").addEventListener("click", () => download("track.json", specText, "application/json"));
    $("#gGeo").addEventListener("click", () => download("route.geojson", JSON.stringify(gj), "application/geo+json"));
    if (server) $("#gSave").addEventListener("click", async () => {
      try {
        const r = await fetch(`/api/tracks/${encodeURIComponent(st.slug)}`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ files: { "track.json": specText, "BRIEF.md": md, "route.geojson": JSON.stringify(gj) } }),
        });
        const j = await r.json();
        if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
        $("#gSaved").innerHTML = `<div class="saved"><b>Saved</b> to <code>${esc(j.dir)}</code>. Paste this into Claude Code (opened in <code>${esc(server.studio_root)}</code>):<pre class="preview">${esc(prompt)}</pre><button type="button" id="gCopyPrompt" class="primary">Copy prompt</button></div>`;
        $("#gCopyPrompt").addEventListener("click", async () => { try { await navigator.clipboard.writeText(prompt); toast("Prompt copied"); } catch (e) { toast("Copy failed", true); } });
      } catch (err) { toast("Save failed: " + err.message, true); }
    });
  }
  $("#btnGenerate").addEventListener("click", generate);

  // ------------------------------------------------------------------------------- new / open
  $("#btnNew").addEventListener("click", () => {
    if (st.waypoints.length && !confirm("Start a new track? The current one stays in tracks/ only if you saved it.")) return;
    const view = st.view;
    st = newState(); st.view = view;
    legCache.clear(); undo.length = 0;
    renderAll(); changed();
  });
  $("#btnOpen").addEventListener("click", async () => {
    let list = [];
    if (server) { try { list = await (await fetch("/api/tracks")).json(); } catch (e) { list = []; } }
    openModal("Open a track", `
      ${server ? `<div class="track-list">${list.length ? list.map((t) => `<div class="item"><div><b>${esc(t.name || t.slug)}</b><div class="meta">${esc(t.slug)} · ${esc(t.type || "")} · ${t.length_m ? km(t.length_m) + " km" : ""} · ${esc((t.updated || "").slice(0, 16).replace("T", " "))}</div></div><div class="btns"><button type="button" class="small primary" data-slug="${esc(t.slug)}">Open</button></div></div>`).join("") : `<p class="hint">No saved tracks in tracks/ yet.</p>`}</div>` : `<p class="hint">Run <code>python3 serve.py</code> to list the tracks saved in the project.</p>`}
      <label class="field">Or import a track.json<input type="file" id="importFile" accept=".json,application/json"></label>`);
    $$("[data-slug]").forEach((b) => b.addEventListener("click", async () => {
      try {
        const spec = await (await fetch(`/api/tracks/${encodeURIComponent(b.dataset.slug)}`)).json();
        loadSpec(spec);
      } catch (err) { toast("Could not open: " + err.message, true); }
    }));
    $("#importFile").addEventListener("change", (e) => {
      const f = e.target.files[0];
      if (!f) return;
      const rd = new FileReader();
      rd.onload = () => { try { loadSpec(JSON.parse(rd.result)); } catch (err) { toast("Not a valid track.json: " + err.message, true); } };
      rd.readAsText(f);
    });
  });
  function loadSpec(spec) {
    if (!spec.planner_state) throw new Error("this file has no planner_state");
    st = normalise(spec.planner_state);
    legCache.clear(); undo.length = 0;
    closeModal();
    renderAll();
    if (st.route) {
      const b = L.latLngBounds(st.route.coords.map((p) => [p[1], p[0]]));
      map.fitBounds(b.pad(0.1));
    }
    save();
    toast(`Opened “${st.name}”`);
  }

  // ------------------------------------------------------------------------------------ routing UI
  function bindRouting() {
    $("#optEngine").addEventListener("change", (e) => { st.routing.engine = e.target.value; changed(); });
    $("#optOneway").addEventListener("change", (e) => { st.routing.oneway = e.target.value; changed(); });
    $("#optTracks").addEventListener("change", (e) => { st.routing.allowTracks = e.target.checked; changed(); });
    $("#optPaths").addEventListener("change", (e) => { st.routing.allowPaths = e.target.checked; changed(); });
    $("#btnAddWp").addEventListener("click", () => {
      startPick({ kind: "wp", index: st.waypoints.length, add: true }, "Click the map, or search in the new row, to add a stop at the end");
    });
    $("#btnReverse").addEventListener("click", reverseRoute);
    $("#btnClearWp").addEventListener("click", () => { if (!st.waypoints.length) return; pushUndo(); st.waypoints = []; changed(); });
  }
  function renderRouting() {
    $("#optEngine").value = st.routing.engine || "fast";
    $("#optOneway").value = st.routing.oneway; $("#optTracks").checked = st.routing.allowTracks; $("#optPaths").checked = st.routing.allowPaths;
  }

  function renderAll() {
    renderMeta(); renderRouting(); renderDetail(); renderTypeToggle(); renderWaypoints(); drawAll(); autoName();
  }

  // ------------------------------------------------------------------------------------- start
  async function detectServer() {
    try {
      const r = await fetch("/api/info", { cache: "no-store" });
      if (r.ok) server = await r.json();
    } catch (e) { server = null; }
    renderMeta();
  }

  bindType(); bindRouting(); bindTiming(); bindDetail(); initSurveyForm(); bindSurveyControls(); bindMeta();
  renderAll();
  detectServer();
  if (st.route) {
    // the stored route is kept (the graph reloads only when a stop changes), unless a leg had
    // failed to route (servers busy): then try again
    if (!st.view) map.fitBounds(L.latLngBounds(st.route.coords.map((p) => [p[1], p[0]])).pad(0.1));
    if ((st.route.legs || []).some((l) => l.error)) computeRoute(); else scheduleElevation();
  } else if (st.waypoints.length >= 2) {
    computeRoute();                      // stops saved before their route finished
  } else if (!st.view) {
    status("Search a place or click the map to start.");
  }
  window.__acts = { get state() { return st; }, map, computeRoute, generate };
})();
