/* Exports: track.json (the machine-readable spec, which also reopens in the planner),
   route.geojson, BRIEF.md (instructions for Claude) and the prompt to paste into Claude Code. */
(function () {
  "use strict";
  const r1 = (v) => Math.round(v * 10) / 10;
  const r7 = (v) => Math.round(v * 1e7) / 1e7;

  function at(route, s) {
    const cum = route._cum || (route._cum = Geo.cumulative(route.coords));
    const p = Geo.pointAt(route.coords, cum, s);
    return { at_m: Math.round(s), lat: r7(p.p[1]), lon: r7(p.p[0]), bearing_deg: Math.round(p.bearing) };
  }

  function legsSummary(st) {
    return (st.route ? st.route.legs : []).map((l, i) => ({
      from: i, to: l.to, mode: l.mode, length_m: Math.round(l.length), against_oneway_edges: l.againstOneway || 0,
    }));
  }

  /** Planner state -> track.json */
  function buildSpec(st, ctx) {
    const R = st.route;
    const isCircuit = st.type === "circuit";
    const L = R ? R.length : 0;
    const t = st.timing;
    const timing = { };
    if (R) {
      if (isCircuit) {
        Object.assign(timing, {
          start_finish: at(R, t.start_m || 0),
          sectors: (t.splits || []).map((sp) => Object.assign({ name: sp.name }, at(R, sp.at_m))),
          laps: t.laps, grid_slots: t.grid_slots, grid_spacing_m: t.grid_spacing_m,
          pits: t.pits === "area" && t.pit_lat != null
            ? { mode: "area", lat: r7(t.pit_lat), lon: r7(t.pit_lon), count: t.pit_count }
            : { mode: "grid", count: t.grid_slots },
        });
      } else {
        Object.assign(timing, {
          start: at(R, t.start_m), finish: at(R, t.finish_m),
          timed_length_m: Math.round(t.finish_m - t.start_m),
          splits: (t.splits || []).map((sp) => Object.assign({ name: sp.name }, at(R, sp.at_m))),
          spawn_before_start_m: 8,
        });
      }
    }
    const frame = st.frame || (R ? Geo.mapFrame(R.coords, st.detail.map_margin_m) : null);
    const cc = st.place && st.place.country_code;
    return {
      schema: "ac-track-studio/track@1",
      generated_at: new Date().toISOString(),
      generator: "AC Track Studio planner",
      track: {
        name: st.name, slug: st.slug, type: st.type, description: st.description || "",
        author: st.author || "", laps: isCircuit ? t.laps : 1,
        direction: isCircuit ? (R && R.clockwise ? "clockwise" : "counter-clockwise") : "as drawn (first to last waypoint)",
      },
      place: st.place || null,
      waypoints: st.waypoints.map((w, i) => ({
        index: i, label: w.label || "", lat: r7(w.lat), lon: r7(w.lon),
        leg_to_next: i < st.waypoints.length - 1 || isCircuit ? w.leg : null,
      })),
      routing: Object.assign({}, st.routing, {
        engine: R && R.engine === "graph" ? "OSM road graph via Overpass (planner A*)" : "Valhalla directions service (FOSSGIS, OSM data)",
        osm_data_date: R ? R.osm_timestamp : null,
        note: R && R.engine !== "graph" ? "osm_nodes are null: the service gives OSM way ids, not node ids" : undefined,
      }),
      route: R ? {
        closed: isCircuit, length_m: r1(L),
        coordinates: R.coords.map((p) => [r7(p[0]), r7(p[1])]),
        osm_nodes: R.nodes,
        edge_way_ids: R.edgeWays,
        legs: legsSummary(st),
        streets: R.streets,
        corners: (R.corners || []).map((c) => ({
          apex_m: c.apex_m, from_m: c.from_m, to_m: c.to_m, radius_m: c.radius_m, turn_deg: c.turn_deg,
          dir: c.dir, hairpin: c.hairpin, apex: [r7(c.apex[0]), r7(c.apex[1])],
        })),
        self_crossings: (R.crossings || []).map((x) => ({ s_a: Math.round(x.s_a), s_b: Math.round(x.s_b), lon: r7(x.p[0]), lat: r7(x.p[1]) })),
      } : null,
      elevation: st.elevation ? {
        source: st.elevation.source, step_m: st.elevation.step_m, grade_window_m: st.elevation.grade_window_m, stats: st.elevation.stats,
        note: "planning preview only; the build must use the best local DTM",
        profile: st.elevation.s.map((s, i) => [s, st.elevation.z[i]]),
      } : null,
      timing,
      detail: st.detail,
      map_frame: frame,
      streetview: {
        coverage: st.survey.coverage,
        segments: st.survey.segments.map((g) => Object.assign({}, g)),
      },
      annotations: st.annotations.map((a) => {
        const out = { kind: a.kind, side: a.side, text: a.text, lat: r7(a.lat), lon: r7(a.lon) };
        if (R) {
          const pr = Geo.project(R.coords, R._cum || Geo.cumulative(R.coords), [a.lon, a.lat]);
          out.at_m = Math.round(pr.s); out.offset_m = r1(pr.dist);
        }
        return out;
      }),
      notes: st.notes || "",
      data_sources: {
        country_code: cc || null,
        suggested: Object.assign({}, Vocab.SOURCES._global, (cc && Vocab.SOURCES[cc]) || {}),
        marking_standard: (cc && Vocab.MARKINGS[cc]) || "the national road-marking standard",
        typical_trees: (cc && Vocab.TREES[cc]) || "from land cover and Street View",
      },
      paths: ctx.paths,
      planner_state: stripState(st),
    };
  }

  function stripState(st) {
    const c = JSON.parse(JSON.stringify(st, (k, v) => (k.startsWith("_") ? undefined : v)));
    return c;
  }

  function routeGeoJSON(spec) {
    const f = [];
    if (spec.route) f.push({ type: "Feature", geometry: { type: "LineString", coordinates: spec.route.coordinates },
      properties: { kind: "route", name: spec.track.name, length_m: spec.route.length_m, closed: spec.route.closed } });
    spec.waypoints.forEach((w) => f.push({ type: "Feature", geometry: { type: "Point", coordinates: [w.lon, w.lat] },
      properties: { kind: "waypoint", index: w.index, label: w.label } }));
    const tm = spec.timing || {};
    const pts = [];
    if (tm.start) pts.push(["start", tm.start]);
    if (tm.finish) pts.push(["finish", tm.finish]);
    if (tm.start_finish) pts.push(["start_finish", tm.start_finish]);
    (tm.splits || tm.sectors || []).forEach((s) => pts.push(["split", s]));
    pts.forEach(([k, p]) => f.push({ type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] },
      properties: Object.assign({ kind: k }, p) }));
    spec.annotations.forEach((a) => f.push({ type: "Feature", geometry: { type: "Point", coordinates: [a.lon, a.lat] },
      properties: Object.assign({ kind: "annotation" }, a) }));
    return { type: "FeatureCollection", features: f };
  }

  // ------------------------------------------------------------------------------------ Markdown
  function table(headers, rows) {
    if (!rows.length) return "_none_\n";
    const esc = (v) => String(v == null ? "" : v).replace(/\|/g, "\\|").replace(/\n/g, " ");
    return `| ${headers.join(" | ")} |\n|${headers.map(() => "---").join("|")}|\n` +
      rows.map((r) => `| ${r.map(esc).join(" | ")} |`).join("\n") + "\n";
  }
  const km = (m) => (m / 1000).toFixed(2);

  function warnings(st) {
    const w = [];
    const R = st.route;
    if (!R) { w.push({ level: "error", text: "No route yet: set a From and a To (and a loop for a circuit)." }); return w; }
    if (R.length < 300) w.push({ level: "error", text: `The route is only ${Math.round(R.length)} m long.` });
    const direct = R.legs.filter((l) => l.mode === "direct");
    if (direct.length) w.push({ level: "warn", text: `${direct.length} leg(s) are straight "direct" lines off the OSM network: the build must trace them from the orthophoto / Street View.` });
    const against = R.legs.reduce((a, l) => a + (l.againstOneway || 0), 0);
    if (against) w.push({ level: "info", text: `${against} road segment(s) are driven against a one-way restriction (fine for a closed-road event; check it is intended).` });
    if (R.repeatedEdges) w.push({ level: "warn", text: `The route drives ${R.repeatedEdges} road segment(s) twice (out-and-back or overlapping legs).` });
    if ((R.crossings || []).length) w.push({ level: "info", text: `The route crosses itself ${R.crossings.length} time(s): check bridges / level crossings.` });
    const unpaved = (R.streets || []).filter((s) => s.highway === "track" || (s.surface && OsmGraph.UNPAVED.test(s.surface)));
    if (unpaved.length) w.push({ level: "info", text: `${unpaved.length} stretch(es) are unpaved or tracks in OSM.` });
    if (!st.elevation) w.push({ level: "warn", text: "No elevation profile yet (Route tab → Elevation)." });
    if (!st.place) w.push({ level: "warn", text: "Country unknown (reverse geocoding has not run): data sources cannot be suggested." });
    const fr = st.frame || Geo.mapFrame(R.coords, st.detail.map_margin_m);
    if (!fr.fits) w.push({ level: "warn", text: `The route needs a ${km(Math.max(...fr.route_extent_m) + 2 * fr.margin_m)} km map: more than the pipeline's 8192 px grid at 4 m. Consider a smaller margin or a tiled terrain.` });
    else if (fr.meters_per_px > 1) w.push({ level: "info", text: `Map ${km(fr.extent_m)} km wide: the terrain grid would be ${fr.meters_per_px} m/px; the build should keep 1 m (or finer) near the road (the AC terrain is sampled per distance from the road).` });
    if (st.type === "point_to_point") {
      const t = st.timing;
      if (!(t.finish_m > t.start_m)) w.push({ level: "error", text: "The finish must come after the start." });
      if (t.start_m < 15) w.push({ level: "warn", text: "The start is less than 15 m from the beginning of the route: no room to spawn before the line." });
    }
    if (st.type === "circuit") {
      const t = st.timing;
      if (t.grid_slots * t.grid_spacing_m > R.length * 0.5) w.push({ level: "warn", text: "The grid is longer than half the lap." });
      if (!R.closed) w.push({ level: "error", text: "A circuit must close: the last leg returns to the first waypoint." });
    }
    return w;
  }

  function markdown(spec, st) {
    const T = spec.track, R = spec.route, E = spec.elevation, D = spec.detail, F = spec.map_frame;
    const P = spec.paths, place = spec.place || {};
    const isCircuit = T.type === "circuit";
    const where = [place.town, place.county, place.region, place.country].filter(Boolean).join(", ");
    const L = [];
    const h = (s) => L.push(`\n## ${s}\n`);
    const p = (s) => L.push(s);

    L.push(`# Brief: ${T.name} — Assetto Corsa track from real-world data`);
    p(`\n> Written by the AC Track Studio planner on ${spec.generated_at.slice(0, 10)}. The machine-readable spec is \`track.json\` in this folder (route geometry, OSM node / way ids, elevation profile, survey, annotations, map frame); \`route.geojson\` has the same line for GIS tools. If this brief and track.json disagree, track.json wins.`);

    h("Task");
    p(`Build a private **Assetto Corsa** track of real roads${where ? ` near **${where}**` : ""}: **${T.name}**, ` +
      (isCircuit ? `a closed **circuit** of **${km(R.length_m)} km** per lap, driven ${T.direction}, ${T.laps} laps by default.`
        : `a **point-to-point** run of **${km(R.length_m)} km** (timed ${km(spec.timing.timed_length_m)} km) from **${spec.waypoints[0].label || "the first waypoint"}** to **${spec.waypoints[spec.waypoints.length - 1].label || "the last waypoint"}**.`));
    if (T.description) p(`\n${T.description}`);
    p(`\nThe road must match reality as closely as the open data allows: its line, **width**, **elevation** and cross-section, its **surface and markings**, and what lines it (**kerbs, guardrails, walls, fences, hedges, buildings**), with **3D trees** near the road. Deliver \`${T.slug}_ac.zip\` for Content Manager (drag and drop), plus a README in the build folder.`);

    h("Rules");
    p([
      "- Private track for the user's own use: never published.",
      "- The Google Maps API key is only in the environment variable `GOOGLE_MAPS_API_KEY` (ask the user to export it before starting Claude). Never write it into any file, log or commit.",
      "- Street View: the survey is notes and codes (`streetview_segments.json`) with panorama ids, their location and capture date (ids change over time). By the user's decision (outside Google's terms: see the studio's `CLAUDE.md`), panoramas along the route may be downloaded into the build's private cache `streetview/` (`tools/streetview_fetch.py`) as the survey reference and as a texture source for this track: ask first with the count and size, keep the cache out of the zip, and say in the build README which textures use Street View pixels. Do not extract Google 3D tiles or Google satellite imagery.",
      "- Google Maps Platform terms (§3.2.3): never trace roads on Google satellite imagery, never build terrain or road profiles from the Google Elevation API, never show Street View next to a non-Google map in anything you build. The Street View metadata endpoint (panorama id, location, date) is free and fine to use.",
      "- Textures come from CC0 libraries (Poly Haven, ambientCG), our own procedural generators or, for this private track only, the build's Street View cache (rule above). Derive road edges, ground colour, crowns or roofs only from orthophotos whose licence allows it (CC BY, CC0, OGL, dl-de, Licence Ouverte…); restricted ones (e.g. AGEA in most of Italy, application-only Swedish orthophotos) are a visual reference: ask the user (docs/DATA_SOURCES.md → How to use it).",
      "- Credit every dataset and its licence in the build README (OSM: © OpenStreetMap contributors, ODbL).",
      "- In any browser, decline optional cookies.",
      "- Before any download over ~2 GB, or anything that costs money (Google API calls beyond metadata, paid data), ask the user.",
    ].join("\n"));

    h("Where things are");
    p(table(["What", "Path"], [
      ["This brief + track.json", `${P.track_dir}`],
      ["Studio instructions (read first)", `${P.studio_root}/CLAUDE.md`],
      ["Pipeline map, what to reuse / replace", `${P.studio_root}/docs/PIPELINE.md`],
      ["Quality bar (acceptance criteria)", `${P.studio_root}/docs/QUALITY.md`],
      ["Data sources by country", `${P.studio_root}/docs/DATA_SOURCES.md`],
      ["Reference pipeline (the studio's own; proven on two Veneto hill climbs)", `${P.reference_pipeline}`],
      ["Build folder (create with the scaffold)", `${P.studio_root}/builds/${T.slug}`],
      ["Scaffold command", `\`python3 ${P.studio_root}/tools/scaffold.py ${T.slug}\``],
    ]));

    h("The track");
    const rows = [
      ["Type", isCircuit ? `Circuit (closed loop), ${T.direction}` : "Point to point (hill climb / stage)"],
      ["Length", isCircuit ? `${km(R.length_m)} km per lap` : `${km(R.length_m)} km drawn, ${km(spec.timing.timed_length_m)} km timed`],
      ["Location", where || "unknown"],
      ["Country code", spec.data_sources.country_code || "unknown"],
    ];
    if (E) {
      const s = E.stats;
      rows.push(["Elevation (preview)", `${s.start_m} m → ${s.end_m} m; min ${s.min_m}, max ${s.max_m}; +${s.gain_m} / −${s.loss_m} m`]);
      rows.push([`Grade (over ${s.grade_window_m} m, preview)`, `steepest +${s.max_grade_pct} % at ${km(s.max_grade_at_m)} km; ${s.min_grade_pct} % at ${km(s.min_grade_at_m)} km; mean ${s.avg_grade_pct} %`]);
      rows.push(["Elevation source", `${E.source}, every ${E.step_m} m: a coarse planning preview (short grades are smoothed away), NOT for the build`]);
    }
    const corners = R.corners || [];
    const hp = corners.filter((c) => c.hairpin);
    rows.push(["Corners (R < 150 m)", `${corners.length}; ${hp.length} hairpins; tightest R ≈ ${corners.length ? Math.min(...corners.map((c) => c.radius_m)) : "–"} m (OSM line, polygonal: indicative)`]);
    p(table(["", ""], rows));

    p("\n**Waypoints** (the user's; the route was snapped to the OSM road graph between them):\n");
    p(table(["#", "Label", "Lat, lon", "Next leg"], spec.waypoints.map((w) => [w.index, w.label, `${w.lat}, ${w.lon}`, w.leg_to_next || "—"])));

    p("\n**Roads in order** (from the OSM tags; widths here are OSM's, often missing or wrong — measure them):\n");
    p(table(["From km", "To km", "Road", "Class", "Lanes", "Width", "Surface", "Limit", "Notes", "OSM way ids"],
      (R.streets || []).filter((s) => s.to_m - s.from_m >= 15).map((s) => [km(s.from_m), km(s.to_m), [s.name, s.ref].filter(Boolean).join(" / ") || "(unnamed)", s.highway || "",
        s.lanes || "", s.width || "", s.surface || "", s.maxspeed || "",
        [s.bridge ? "bridge" : "", s.tunnel ? "tunnel" : "", s.oneway ? "one-way" : "", s.lit ? "lit" : ""].filter(Boolean).join(", "),
        (s.way_ids || []).slice(0, 6).join(", ") + ((s.way_ids || []).length > 6 ? ", …" : "")])));

    if (corners.length) {
      p(`\n**Notable corners** (tightest 25 by radius; L/R in the driving direction):\n`);
      p(table(["Apex km", "Dir", "Radius m", "Turn °", "Hairpin"],
        corners.slice().sort((a, b) => a.radius_m - b.radius_m).slice(0, 25).sort((a, b) => a.apex_m - b.apex_m)
          .map((c) => [km(c.apex_m), c.dir, c.radius_m, c.turn_deg, c.hairpin ? "yes" : ""])));
    }
    if ((R.self_crossings || []).length) {
      p("\n**Self-crossings** (bridge, tunnel or a junction the route uses twice):\n");
      p(table(["km (first pass)", "km (second pass)", "Lat, lon"], R.self_crossings.map((x) => [km(x.s_a), km(x.s_b), `${x.lat}, ${x.lon}`])));
    }

    h("Timing and grid");
    const tm = spec.timing;
    if (isCircuit) {
      p(table(["Item", "Where"], [
        ["Start / finish line", `${km(tm.start_finish.at_m)} km (${tm.start_finish.lat}, ${tm.start_finish.lon}), heading ${tm.start_finish.bearing_deg}°`],
        ...tm.sectors.map((s, i) => [`Sector line ${i + 1} (${s.name})`, `${km(s.at_m)} km (${s.lat}, ${s.lon})`]),
        ["Grid", `${tm.grid_slots} slots behind the line, ${tm.grid_spacing_m} m apart, staggered left / right`],
        ["Pits", tm.pits.mode === "area" ? `${tm.pits.count} boxes in the area at ${tm.pits.lat}, ${tm.pits.lon} (user-placed)` : `${tm.pits.count} boxes = the grid slots (no pit lane)`],
        ["Laps", tm.laps],
      ]));
      p("\nCircuit work the reference pipeline does not do yet (docs/PIPELINE.md → Circuits, docs/AC_NOTES.md): a closed road mesh and terrain seam, `AC_TIME_0_L/R` start/finish and `AC_TIME_1/2_L/R` sector gates (at most two), `AC_START_n` grid and `AC_PIT_n` boxes numbered from 0 without gaps (as many grid slots as pit boxes; `AC_PIT_0` is mandatory), a closed `fast_lane.ai` starting at the line (no duplicated last point), `ui_track.json` with `pitboxes` = the number of `AC_PIT_n` and string geotags, and a `PIT` surface (`IS_PITLANE=1`) plus `pit_lane.ai` only if a real pit lane is built (never on the grid).");
    } else {
      p(table(["Item", "Where"], [
        ["Start line", `${km(tm.start.at_m)} km (${tm.start.lat}, ${tm.start.lon}), heading ${tm.start.bearing_deg}°`],
        ["Finish line", `${km(tm.finish.at_m)} km (${tm.finish.lat}, ${tm.finish.lon}), heading ${tm.finish.bearing_deg}°`],
        ...tm.splits.map((s) => [`Split: ${s.name}`, `${km(s.at_m)} km (${s.lat}, ${s.lon})`]),
        ["Spawn", "a few metres before the start line (AC_START_0, AC_PIT_0, AC_HOTLAP_START_0), as in the reference pipeline"],
      ]));
      if (tm.splits.length) p("\nSplits: the reference pipeline does not emit intermediate timing on point-to-point tracks. Try `AC_TIME_1/2_L/R` gates and check in game that AC reports them on an A-to-B layout; if not, keep them as `sections.ini` entries and spawn points (docs/AC_NOTES.md).");
    }

    h("Quality bar for this track");
    p(`Meet docs/QUALITY.md. The user's settings for this track:\n`);
    const kerbTxt = { real: "real-world kerbs only (kerbstones, sidewalks, gutters, where they exist)", racing: `real-world kerbs + racing kerbs (red / white) at apexes and exits of corners tighter than R ${D.racing_kerb_max_radius_m} m`, none: "no kerbs modelled" }[D.kerbs];
    const barrierTxt = { survey: "exactly where they are in reality (Street View survey)", survey_safety: "where they are in reality, plus guardrails where the ground drops away steeply and nothing is there (mark them as added in the README)" }[D.barriers];
    p(table(["Topic", "Requirement"], [
      ["Road line", "Within ~0.5 m of the real carriageway centre: fitted to orthophoto edges / topographic carriageway polygons, never raw OSM (often 5–15 m off under trees)."],
      ["Width", `Measured along the road (orthophoto, topo database, Street View estimate), varying as in reality; at least ${D.road_min_width_m} m; AC asphalt ${D.road_widen_m > 0 ? `${D.road_widen_m} m wider than measured (less where houses / other legs are close)` : "exactly as measured"}.`],
      ["Elevation", "From the best bare-earth DTM for the country (LiDAR ≤ 2 m where it exists). Keep the real gradient: smooth only DEM noise; no steps, no bumps the real road does not have. Report max deviation from the DTM along the centreline."],
      ["Cross-section", `Crown ${D.crown_pct} % (or real crossfall / superelevation where obvious, e.g. major roads); shoulders and verges shaped to the terrain; no gap or step between road and terrain meshes.`],
      ["Surface", { survey: "Per stretch as seen in Street View (new / worn / patched / concrete / cobbles / gravel), photo-based CC0 textures with normal maps, no visible repetition.", new: "Fresh asphalt everywhere.", worn: "Worn asphalt everywhere.", patched: "Patched / repaired asphalt everywhere." }[D.surface]],
      ["Markings", { survey: `Only where Street View shows them, to the dimensions of ${spec.data_sources.marking_standard}.`, standard: `Full markings per ${spec.data_sources.marking_standard} for each road class.`, none: "No markings." }[D.markings]],
      ["Kerbs", kerbTxt + "; true cross-section profiles (upstand height, chamfer / rounded top), physics matching (kerb surface)."],
      ["Guardrails, walls, fences", `${barrierTxt}. Real profiles: W-beam on posts with spacers and terminal ends, walls following the terrain (retaining where the bank rises), heights from the survey; every one solid (collision).`],
      ["Trees", `**3D trees** (trunk, branches, leaf clusters; species: ${D.tree_species || spec.data_sources.typical_trees}) within ${D.tree_mesh_radius_m} m of the road, at their real positions (crowns from the orthophoto / land cover); lighter cards out to ${D.tree_card_radius_m} m; no leaves within 0.4 m of the asphalt; trunk colliders near the road.`],
      ["Terrain", `1 m mesh and fine (~13 cm) ground masks within ${D.terrain_corridor_m} m of the road (gravel shoulder, grass, forest floor, paved), coarser beyond; rock faces on steep cuttings; ground colour from the orthophoto where licensed; horizon ring out to ${D.horizon_km} km.`],
      ["Buildings", `Footprints and heights from the topo database / OSM; full detail within ${D.building_detail_radius_m} m of the road; landmarks the user marked built by hand.`],
      ["Location", `Real sun and time zone: \`ui_track.json\` geotags as strings and \`[WEATHER_FX] LATITUDE / LONGITUDE / TIMEZONE_ID\` in \`data/surfaces.ini\` for ${spec.place && spec.place.lat != null ? `${spec.place.lat}, ${spec.place.lon}` : "the start"} (docs/AC_NOTES.md → A4.1).`],
      ["Extras", [D.csp_grass && "CSP 3D grass", D.rain && "CSP rain", D.ai_line && "AI line (fast_lane.ai)", D.replay_cameras && "replay camera sets", D.night_lights && "street lights for night (CSP)"].filter(Boolean).join(", ") || "none"],
    ]));

    h("What the user surveyed (authoritative)");
    const segs = spec.streetview.segments;
    if (segs.length) {
      p(`${segs.length} stretch(es) from the planner's Street View survey (same codes as the reference pipeline's \`streetview_segments.json\`; L/R in the driving direction). Codes not built by the reference pipeline yet: ${[...new Set(segs.flatMap((g) => [g.left, g.right]).filter((c) => c && !Vocab.roadside(c).pipeline))].join(", ") || "none"}.\n`);
      p(table(["From km", "To km", "Width", "Lines (edge / centre)", "Left", "Right", "Surface", "Notes"],
        segs.map((g) => [km(g.from_m), km(g.to_m), g.width_m || "", `${g.edge_lines || ""} / ${g.centre_line || ""}`,
          `${g.left || ""}${g.left_height_m ? ` ${g.left_height_m} m` : ""}`, `${g.right || ""}${g.right_height_m ? ` ${g.right_height_m} m` : ""}`,
          g.surface || "", g.notes || ""])));
      p("\nSurvey the rest of the road yourself in the same format (one stop every 100–150 m), and merge.");
    } else {
      p("Nothing surveyed in the planner: do the full Street View survey (one stop every 100–150 m) as in the reference project, and write `streetview_segments.json` plus readable notes.");
    }
    if (spec.annotations.length) {
      p("\n**Map annotations by the user:**\n");
      p(table(["Kind", "km", "Off road", "Side", "Lat, lon", "Note"], spec.annotations.map((a) =>
        [Vocab.annotation(a.kind).label, a.at_m != null ? km(a.at_m) : "", a.offset_m != null ? `${a.offset_m} m` : "", a.side || "", `${a.lat}, ${a.lon}`, a.text])));
    }
    if (spec.notes) p(`\n**Notes from the user:**\n\n${spec.notes.split("\n").map((l) => "> " + l).join("\n")}`);

    h("Street View");
    p(`Run \`tools/streetview_fetch.py ${T.slug} --plan\` (no key needed) for the panoramas along the route: how many, the download size and the stretches with no coverage. After the user's go-ahead, download them (about one every 50 m; every panorama, about 10 m, where road textures will be cut from them) and run \`tools/streetview_views.py ${T.slug}\` for the survey sheets (\`streetview/views/\`: ahead, back, left and right at each stop, with the distance along the route and the capture month) and the top-down road patches (\`streetview/ground/\`). Survey from the sheets, one stop every 50–150 m, and open Google Maps in the browser only where a sheet is not enough. Where there is no coverage, use the orthophoto, the topo database and the rules for the road class. If the user wants no images for this track, index with the free metadata endpoint instead (reference \`streetview_route.py\`, key from \`GOOGLE_MAPS_API_KEY\`) and survey in Google Maps.`);

    h("Data sources to use");
    const ds = spec.data_sources;
    p(`Country: **${place.country || "unknown"}**${place.region ? `, ${place.region}` : ""}. Starting points (verify licence, coverage and the service URL; docs/DATA_SOURCES.md has more):\n`);
    p(table(["Need", "Suggested source"], [
      ["Bare-earth elevation (DTM)", ds.suggested.dem],
      ["Orthophoto (road edges, ground colour)", ds.suggested.ortho],
      ["Topographic vectors (carriageway, buildings)", ds.suggested.topo],
      ["Road markings standard", ds.marking_standard],
      ["Typical trees", ds.typical_trees],
      ["Always", "OpenStreetMap (Overpass) for roads, land cover, buildings; Copernicus GLO-30 for the horizon"],
    ]));
    p("\nThe reference pipeline's Veneto-specific fetchers (`fetch_dem.py`, `fetch_geodbt.py`, `fetch_ortho.py`, `fetch_corridor.py`, `geodbt*.py`, `measured_widths.py`) must be replaced by equivalents for these sources, keeping their outputs' formats so the rest of the pipeline is unchanged.");

    h("Map frame (proposed config.json values)");
    p(table(["Key", "Value"], [
      ["crs", `EPSG:${F.epsg} (UTM ${F.utm_zone}${F.hemisphere})`],
      ["center_utm", `[${F.center_utm.join(", ")}]`],
      ["terrain_size_px × meters_per_px", `${F.terrain_size_px} × ${F.meters_per_px} m = ${km(F.extent_m)} km square (route ${km(F.route_extent_m[0])} × ${km(F.route_extent_m[1])} km + ${F.margin_m} m margin)`],
      ...(F.elev ? [["Heights in the map (preview)", `${Math.round(F.elev.min_m)}–${Math.round(F.elev.max_m)} m (coarse 90 m preview)`]] : []),
    ]));
    p("\n`tools/scaffold.py` writes these into the build's `config.json` and turns the route into the pipeline's `output/route_climb.geojson` (local coordinates, OSM node and way ids).");

    h("Plan");
    p([
      "1. Read the studio's `CLAUDE.md`, `docs/PIPELINE.md`, `docs/QUALITY.md` and `docs/DATA_SOURCES.md`, then `pipeline/README.md` and the latest build's README.",
      "2. Reply with your plan: data sources chosen (with licences), what the reference pipeline covers as is, what needs new code (region fetchers" + (isCircuit ? ", circuit support" : "") + ", any new roadside codes, 3D trees), and the open questions below. Wait for the user's go-ahead before large downloads.",
      `3. Scaffold the build folder (\`tools/scaffold.py ${T.slug}\`), set up the region's fetchers, fetch DTM / orthophoto / topo / OSM for the map frame.`,
      "4. Check the planner's route against the fresh OSM data (node and way ids); fix legs marked direct by tracing them on the orthophoto.",
      "5. Street View cache (`tools/streetview_fetch.py`, `tools/streetview_views.py`) and survey of the whole road → `streetview_segments.json` + notes (merge the user's segments above, which take precedence).",
      "6. Road: centreline and widths from the orthophoto / topo carriageway; height profile from the DTM; bridges and tunnels.",
      "7. Terrain, land cover, ground textures, buildings, landmarks.",
      "8. Roadside: walls, guardrails, fences, kerbs, hedges from the survey (and racing kerbs if requested), with collision.",
      `9. Trees: 3D models within ${D.tree_mesh_radius_m} m, cards beyond; species for the region.`,
      "10. Assetto Corsa export: kn5, markers, timing, AI line, cameras, surfaces, CSP config, UI files, zip.",
      "11. Verify (below), fix, and write the build README (sources, licences, numbers, known limits).",
    ].join("\n"));

    h("Verification before delivery");
    p([
      "- Route: the built centreline follows the traced road within ~0.5 m; length within 1 % of the drawn route (or explain).",
      "- Elevation: profile vs DTM along the centreline (max / mean deviation); grade plot without spikes; the steepest 100 m matches the DTM.",
      "- Width: table of built vs measured width per 100 m; narrowest and widest points.",
      "- Driving space: nothing solid or leafy over the asphalt (the reference `audit_ac.py`); every wall / guardrail has collision; no holes between road, verge and terrain.",
      "- Look: driver's-eye previews at the survey stops (e.g. the reference `ac_preview.py` in Blender) compared with your Street View notes: list every visible mismatch you fixed or left.",
      "- Performance: kn5 size, triangle count, number of meshes, largest textures; mesh nodes ≤ 65,536 vertices; LOD distances on trees and buildings.",
      "- The zip installs as `content/tracks/<slug>/` (not nested), loads in Content Manager, and the timing objects are where this brief puts them.",
    ].join("\n"));

    h("Open questions to settle with the user first");
    const qs = warnings(st).map((w) => `- ${w.level === "error" ? "**Blocking:** " : ""}${w.text}`);
    qs.push("- Any landmark (church, castle, a special building) to model by hand beyond those annotated?");
    if (isCircuit) qs.push("- Pit lane: none (boxes on the grid), or a real place the user marked? Track limits: road edge or kerb outer edge?");
    p(qs.join("\n"));

    return L.join("\n") + "\n";
  }

  function prompt(spec) {
    const P = spec.paths;
    return `Build the Assetto Corsa track described in ${P.track_dir}/BRIEF.md (spec: track.json in the same folder), following ${P.studio_root}/CLAUDE.md. ` +
      `Read the brief and the docs it lists first, then give me your plan, the data sources you will use and your open questions before downloading anything large. ` +
      `My Google Maps API key is in the environment variable GOOGLE_MAPS_API_KEY.`;
  }

  window.Brief = { buildSpec, markdown, routeGeoJSON, prompt, warnings };
})();
