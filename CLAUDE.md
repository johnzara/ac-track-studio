# AC Track Studio: instructions for Claude

This folder turns real roads into private **Assetto Corsa** tracks, anywhere in the world. The user
draws the route in the planner (`planner/`, served by `serve.py`) and saves a brief into
`tracks/<slug>/`; you build the track from it.

## When you are given a brief

`tracks/<slug>/BRIEF.md` + `track.json` (+ `route.geojson`). `track.json` is authoritative: route
geometry with OSM node / way ids, timing, the user's detail settings, survey segments, annotations,
the proposed map frame and the planner's whole state (`planner_state`, only for reopening).

1. Read the brief, then `docs/PIPELINE.md`, `docs/QUALITY.md`, `docs/DATA_SOURCES.md` (and
   `docs/AC_NOTES.md` for circuits / AC file details), then `pipeline/README.md` and the last
   build's README (`builds/<slug>/README.md`).
2. Reply with a plan before doing anything large: the data sources you will use for this country
   (with licences and how you will access them), what the reference pipeline covers as is, what
   needs new code, rough download sizes, and the open questions in the brief. Wait for the user's
   go-ahead.
3. `python3 tools/scaffold.py <slug>` makes `builds/<slug>/` from the studio's `pipeline/` (scripts,
   `config.json`, `run_all.sh`, links to its `.venv` and assets, the planner's route as
   `output/route_climb.geojson`, `SCAFFOLD.md` with a review list). Work there. After the user
   edits the track in the planner, `--update` refreshes config / route / brief.
4. Build, verify against `docs/QUALITY.md`, and deliver `builds/<slug>/output/<slug>_ac.zip` with a
   README in the build folder (sources and licences, numbers, known limits, what differs from
   reality and why).

## Rules

- Private tracks for the user's own use: never publish them or their derived data.
- Open data only, used within its licence; credit every source in the build README. Esri World
  Imagery (the planner's satellite layer) is a live reference to look at only: never sample its
  colours, bake it into textures or download its tiles.
- Textures: CC0 (Poly Haven, ambientCG), procedural, or cut from the build's Street View cache
  (next rule). Derive edges, colours, crowns or roofs only from orthophotos whose licence allows
  it; restricted ones (AGEA in most of Italy, Sweden's application-only orthophotos) are a visual
  reference unless the user decides otherwise.
- Street View imagery (the user's decision of 2026-10-13, knowingly outside Google's terms: see
  `docs/DATA_SOURCES.md` 5.5). `tools/streetview_fetch.py` may download the panoramas along a
  track's route into `builds/<slug>/streetview/`, and `tools/streetview_views.py` cuts survey
  sheets and top-down road patches out of them: the reference for the survey and a texture source
  for that track only. Show the `--plan` count and size and ask before downloading; never put the
  cache in the zip; a build whose textures use Street View pixels says which ones in its README
  and is never shared with anyone. Still never: Google satellite imagery, Google 3D tiles, Google
  elevation, and nothing Google next to a non-Google map in the planner.
- Ask before downloads over ~2 GB or anything that costs money. Decline optional cookies in browsers.
- `pipeline/` is the studio's reference pipeline: every build starts from it. When a build
  improves a script in a way other tracks benefit from (behind a `config.json` key, not
  hard-coded for one road), copy the change back into `pipeline/` (scripts, `config.json`
  template, `run_all.sh`) and note it in `pipeline/README.md`. Do not write into
  `pipeline/data/` or `cache/` from a build except through the documented tools.
- The original BeamNG projects the pipeline came from are on the
  external drive: history and reasons only, never modify
  them; nothing in the studio may depend on that drive being connected.
- Assetto Corsa only: no BeamNG (or other game) output; the pipeline's BeamNG stage was removed
  on 2026-10-14.
- Stay generic: anything specific to a country (data services, markings, signs, kerbs, barriers,
  buildings, trees) goes behind the track's `config.json` / a per-country table, not hard-coded.
  When you add support for a new country or a new roadside type, also update `docs/` (and the
  planner's `planner/js/vocab.js` if the user should be able to pick it).

## Working on the studio itself

- Planner: plain HTML + JS, no build step (`planner/index.html`, `planner/js/*.js`, Leaflet from
  unpkg). Run `python3 serve.py` and open http://127.0.0.1:8770. Check JS with `node --check`.
- `tools/scaffold.py` is standard-library Python so it runs with any `python3`.
- `builds/` holds large build folders; `tracks/` holds the briefs.
