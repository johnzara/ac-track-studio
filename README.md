# AC Track Studio

Design an Assetto Corsa track on real roads, anywhere OpenStreetMap has them, and hand Claude a
complete brief to build it: a hill climb, a rally-style stage or a closed street circuit, with the
real road line, width, elevation and surface, kerbs, guardrails and walls as they really are, and
3D trees near the road.

It builds on an earlier hill-climb pipeline, now kept and improved inside the studio
(`pipeline/`), which produces AC tracks from open data for the Veneto region; the brief tells Claude
what to reuse and what to replace for another region or country. The studio is a git repository
(builds and caches excluded).

## Workflow

1. **Start the planner**

   ```bash
   python3 serve.py --open
   ```

   It serves http://127.0.0.1:8770 (this computer only) and saves briefs into `tracks/`.
2. **Draw the route** (Route tab): search the From and To places (or type `lat, lon`, or click the
   map), add stops to steer it, drag the markers. The route comes from the same directions service
   as openstreetmap.org's Directions (Valhalla, about a second), with the OSM way, name, class and
   surface of every stretch; it can ignore one-way rules (closed-road event) and never U-turns at a
   stop. For race circuits (`highway=raceway`) and footpaths, which that service does not drive on,
   choose *Routing rules → Downloaded OSM roads* (slower: it downloads the roads around the route;
   a "long detour?" button appears on legs that look wrong). Any leg can be a straight "direct" line
   for a bit that is not in OSM. Click the route to add a stop, the start, the finish or a split
   there. *Circuit* closes the loop back to the first stop.
3. **Check it**: length, elevation profile and grades (coloured on the map and in the profile),
   corners and hairpins, the roads it uses with their OSM tags. The elevation here is a 90 m preview
   (Open-Meteo / Copernicus); the build uses the best local terrain model.
4. **Timing** tab: start / finish lines and splits (point to point), or start/finish, sectors, laps,
   grid and pits (circuit).
5. **Detail** tab: extra AC road width, kerbs (real-world only, or racing kerbs at apexes),
   guardrails and walls (as in reality, or plus safety rails), how far out 3D trees, tree cards,
   detailed buildings and fine terrain go, markings and surface rules, CSP extras.
6. **Survey** tab (optional): *Open at the start* opens Street View in Google Maps, in its own
   window: put it beside the planner. `[` `]` move it 25 m along the route, `{` `}` 100 m (*Look
   back* reverses the view), and the yellow marker shows where you are. Record what lines the road
   per stretch (walls, guardrails, kerbs, fences, hedges, heights, width, markings, surface): what
   you record overrides Claude's own survey; whatever you leave out, Claude surveys.
7. **Notes** tab: mark landmarks to model by hand and anything special on the map; free notes.
8. **Generate brief** → *Save to tracks/<slug>/*. Copy the prompt it shows, start Claude Code in
   this folder, and paste it. For the Street View metadata, Claude's shell needs your key: add it to
   your shell profile (`~/.zshrc`, read by the desktop app's sessions too) or export it before
   starting the `claude` CLI:

   ```bash
   export GOOGLE_MAPS_API_KEY=...
   ```

   Claude replies with a plan (data sources for that country, new work needed, questions) before
   downloading anything large, then builds `builds/<slug>/` and delivers `<slug>_ac.zip` for Content
   Manager.

The planner autosaves in the browser; *Open…* reopens any saved track (or a `track.json` file).
Saved briefs stay on this computer: `tracks/` is not in git.

## Google Street View and the API key

The planner needs no Google key: Street View opens in Google Maps itself (keyless Maps URLs).
Google's Maps Platform terms do not allow Street View imagery on the same screen as a non-Google
map, nor terrain built from Google's Elevation API, so the planner neither embeds a panorama next
to its OpenStreetMap view nor uses Google heights.

Your key is for the official Street View *metadata* endpoint (panorama id, position and capture
date along the route), which Google does not charge for: export it as `GOOGLE_MAPS_API_KEY` before
starting Claude. It is optional since the image cache below finds the panoramas without it. The key
is never written into any file.

### Street View image cache

Since 2026-10-13 a build can also download the panoramas along its route, so that Claude surveys
the road from local images and can cut road textures out of them:

```bash
python3 tools/streetview_fetch.py <slug> --plan      # how many panoramas, how big, coverage gaps
python3 tools/streetview_fetch.py <slug>             # download into builds/<slug>/streetview/
builds/<slug>/.venv/bin/python tools/streetview_views.py <slug>
```

The first finds Google's panoramas within 12 m of the route and keeps about one every 50 m
(`--spacing`; 0 keeps all of them, about every 10 m) as 8192 x 4096 images, roughly 25 MB each.
The second makes, for each one, a survey sheet (ahead, back, left and right of the driving
direction, with the distance along the route and the capture month) and a top-down patch of the
road surface, 16 x 30 m at 2 cm per pixel, with its position and orientation.

This is your decision and it is outside Google's terms, which do not allow bulk downloads of Street
View images; the tool uses unofficial endpoints that can change or be blocked. Everything it
downloads is private to your own tracks: the cache is never part of a track's zip, and a track
whose textures use these pixels must not be shared. It needs `php`, `aria2`, `imagemagick` and
`exiftool` (`brew install php aria2 imagemagick exiftool`).

## Files

```
serve.py              local server: the planner + saving briefs (standard library only)
planner/              the web page (Leaflet + plain JS, no build step)
  js/geo.js           distances, resampling, corners, UTM map frame
  js/directions.js    routing through the Valhalla directions service (default)
  js/osmgraph.js      Overpass download, road graph, A* routing (circuits, paths, fallback)
  js/elevation.js     elevation profile preview (Open-Meteo, Copernicus 90 m)
  js/streetview.js    opens / steers Street View in a Google Maps window
  js/vocab.js         survey codes, annotation kinds, per-country hints (sources, markings, trees)
  js/brief.js         track.json, route.geojson, BRIEF.md, the prompt
  js/app.js           the page
tools/scaffold.py     tracks/<slug>/track.json -> builds/<slug>/ (pipeline copy, config.json, route)
pipeline/             the studio's own reference pipeline: scripts, config template, run_all.sh
                      (+ .venv and CC0 assets, not in git); see pipeline/README.md
cache/                shared downloads, not in git: Street View panoramas (private), Veneto GeoDBT
tools/streetview_fetch.py   Street View panoramas along a route -> builds/<slug>/streetview/
tools/streetview_views.py   survey sheets and top-down road patches from that cache
tools/streetview-dl/  the panorama downloader it runs (PHP, public domain, github.com/fdd4s/streetview-dl)
docs/PIPELINE.md      the reference pipeline: stages, what is generic, what to replace
docs/QUALITY.md       acceptance criteria (road, elevation, kerbs, barriers, walls, trees…)
docs/DATA_SOURCES.md  open elevation / orthophoto / topographic data by country
docs/AC_NOTES.md      Assetto Corsa file conventions (circuits, surfaces, layouts, CSP)
CLAUDE.md             how Claude builds a track from a brief
tracks/<slug>/        BRIEF.md, track.json, route.geojson (from the planner; not in git)
builds/<slug>/        the build folders (large; not in git)
```

## Data and services used by the planner

OpenStreetMap tiles and data (© OpenStreetMap contributors, ODbL); routing through the FOSSGIS
Valhalla server (as on openstreetmap.org) or, for circuits and paths, road downloads from the public
Overpass servers (4 km tiles, cached in the browser for 7 days, with fallback mirrors);
Nominatim (searches only when you press Enter, at most one request per second), Esri World Imagery
and OpenTopoMap as background maps, Open-Meteo's elevation API (Copernicus DEM GLO-90, CC BY 4.0).
They are free public services with fair-use limits: if one is busy, wait a minute and retry.

## Limits

- The planner's elevation and corner radii are previews (90 m terrain model; OSM's polygonal line).
- The reference pipeline is proven for point-to-point roads in Veneto. Circuits, true 3D trees, the
  extra roadside types (cable barriers, New Jersey barriers, racing kerbs, tyre walls…) and other
  countries' data services are new work the brief spells out for Claude.
- Very long routes (map > ~16 km across) exceed one terrain grid at 2 m and need a tiled terrain.

## Licence

The code and docs are under the MIT licence (`LICENSE`). `tools/streetview-dl/` is public domain
(its own `LICENSE`). Tracks built with the studio stay private: they use data whose licences and
terms are set out in `docs/DATA_SOURCES.md`, and Street View imagery may never be shared.
