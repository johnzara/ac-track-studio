# The reference pipeline, and how to reuse it for any track

The reference is the studio's own **`pipeline/`** (see `pipeline/README.md`): a Python pipeline that
builds an Assetto Corsa track from open data, its `.kn5` written directly by `kn5.py`. It started as
an earlier hill-climb project, which descends from two before it (the oldest one was driven in AC)
and also built BeamNG levels; the studio is Assetto Corsa only, and the BeamNG stage was removed on
2026-10-14. It was extended on the first studio build (Street View
widths and survey, CSP 3D trees, pit boxes, location). The old projects are on the external drive;
their READMEs explain the reasons behind most choices, and
the last build's `builds/<slug>/README.md` the latest ones.

`tools/scaffold.py SLUG` copies the pipeline into `builds/SLUG/`, writes `config.json` with the map
frame and timing from the planner, and the planner's route as `output/route_climb.geojson`. From
there, the build is ordinary work in that folder.

## Frames and conventions

- Local frame: metres from the map centre in the UTM zone of `config.json` (`crs`, `center_utm`),
  +X east, +Y north, Z = real altitude. AC: `(x, y, z) → (x, z, −y)`.
- Terrain grid: `terrain_size_px` (power of two) × `meters_per_px`, centred on `center_utm`: the
  rasters (`dem_final.tif`, land-cover layers) are on it; the AC terrain is a distance-adaptive
  triangulation of `dem_final.tif` (1 m near the road, coarser away from it).
- Route metres (`route_m`) run along `output/route_climb.geojson` from its first point. The internal
  route name is `climb` for every track (many scripts read `route_climb.*`), even for a circuit.
- Survey sides `left` / `right` are in the driving direction.

## Stages

| Stage | Script(s) | Generic? | Notes |
|---|---|---|---|
| Elevation download | `fetch_dem.py` | **Veneto** | LiDAR 5 m + DTM5 tiles from the Veneto geoportal; Copernicus GLO-30 fallback (generic). Replace with the country's DTM (docs/DATA_SOURCES.md), same outputs under `data/dem/`. |
| Topographic DB | `fetch_geodbt.py`, `geodbt.py`, `geodbt_line.py`, `geodbt_crops.py`, `measured_widths.py` | **Veneto** | GeoDBT carriageway polygons (road widths and true centreline under trees), building volumes with eave heights, crop map. Elsewhere: the national topo DB if it has road surfaces (BGT NL, BD TOPO FR, swissTLM3D, GeoDanmark, NVDB widths SE…), else ortho tracing only. |
| OSM | `fetch_osm.py`, `build_osm.py` | yes | Overpass with retries and a User-Agent. Roads, buildings, land cover, water in the local frame. |
| Route | `route.py` | yes | Replaced by the planner's route (scaffold). Routed by the directions service it has OSM way ids but no node ids (`osm_nodes` 0); by the downloaded-roads router it has both. Re-run `route.py` only to re-route on fresh OSM. |
| Orthophoto | `fetch_ortho.py`, `fetch_corridor.py`, `ortho_crop.py` | **Veneto WMS** | AGEA 2024 via the Veneto WMS in EPSG:32632. Swap the WMS URL / layer / CRS for the country's service; keep the tiling and GeoTIFF output. |
| DEM build | `build_dem.py` | mostly | Stacks sources by priority with bias matching and seam blending; edit the source list. |
| Climb road | `climb.py` | yes (p2p) | The route as a few continuous features (split at bridges), `route_m0` per feature. Needs every `edge_way_ids` id in `raw.json`; 0 = off-network edge (planner "direct" leg) must be handled. |
| Road line + width | `trace_climb.py`, `trace_roads.py` | partly | Orthophoto cross-profiles (light grey, unsaturated = asphalt) + GeoDBT carriageway fit (dynamic programming over lateral offsets). Without a topo DB: photo where visible, Street View widths, class defaults. |
| Profiles | `road_profiles.py` | yes | Widths, smoothed heights (median + class-dependent smoothing), junction precedence, bridges. |
| Terrain | `build_terrain.py`, `ortho_base.py` | yes | Roads flattened into the ground with falloff; land-cover masks; base colour: one colour per land-cover layer (`ground_palette.json`) modulated by the photo (roofs removed). |
| Buildings | `build_buildings.py`, `buildings_mesh.py`, `roof_analysis.py`, `textures_hd.py`, `textures.py` | partly | GeoDBT volumes + OSM; our own facade / roof generator with normal maps. Styles and textures are Italian (plaster, coppi tiles): add the region's styles (e.g. Swedish timber cladding, falu red, sheet-metal roofs). `roof_analysis.py` reads a Veneto WMS. |
| Landmarks | `landmarks.py`, `landmark_server.py`, `LANDMARKS.md` | yes | Hand-built three.js pages → GLB → placed on the OSM footprint. Examples in `landmarks/examples/`. |
| Track extras | `landmarks_climb.py`, `sites.py`, `signs.py`, `vineyards.py`, `ac_vineyards.py` | **track** | Summit gate, base fence, Italian locality signs, Veneto vineyards. Use as patterns; signs need the country's sign design. |
| Roadside | `climb_furniture.py`, `boundaries.py`, `road_geometry.py` | yes | From `streetview_segments.json` (survey codes below), plus guardrails where the ground drops away unless the brief wants barriers exactly as surveyed; walls, fences and hedges along traced roads (`boundaries.py`). |
| Vegetation | `vegetation.py`, `vineyards.py` | partly | Positions from OSM land cover + orthophoto crowns; type names are a planting catalogue (from the old BeamNG Italy level) that `ac_trees.SPECIES` maps to kinds and heights: map them to the region's species. |
| Scenery inputs | `scenery_inputs.py` (+ `landmarks_climb.py`, `signs.py`, `ortho_base.py`) | yes | `forest_instances.npz` (everything planted), `terrain_base_final.png` (ground colour), `output/scenery/` (extras and signs as Collada + DDS for `ac_scenery.py`). |
| Checks | `climb_report.py`, `audit_climb.py` | yes | Length, grades, bumps in the ground heights, widths, hairpin radii (`ac_track.py` reads them), guardrail gaps. |
| AC road + terrain | `ac_track.py`, `ac_textures.py` | yes (p2p) | Road every 0.5 m, 5 vertices across, crown, `widen_m`; 8-strip 4096 px macro surface (worn / patched per survey); terrain 1 m near the road with 13 cm masks; markings; walls, guardrails, fences, kerbs with collision; timing markers. |
| AC horizon | `ac_horizon.py` | yes | Copernicus ring to 30 km (tiles in `data/ac_assets/copernicus`: fetch the ones for the new area). |
| AC scenery | `ac_scenery.py` | yes | Buildings, landmarks, signs, collision shells. |
| AC trees | `ac_tree_cards.py`, `ac_tree_tops.py`, `ac_csp_tree_models.py` (Blender), `ac_trees.py`, `ac_csp_trees.py` | partly | Cards everywhere (images rendered from CC0 Poly Haven tree models) + CSP 3D trees near the road (`assetto_corsa.csp_trees`, see below). Species are the reference's stand-ins. |
| AC replay / AI | `ac_replay.py` | yes (p2p) | `fast_lane.ai` v7 (points lifted 0.4 m: AC crashed with points under the asphalt), three camera sets. |
| AC export | `export_ac.py`, `kn5.py` | yes (p2p) | `.kn5`, `ai/`, `data/*.ini`, `ui/`, CSP `extension/ext_config.ini`, zip. `assetto_corsa.exposure` scales every material. |
| AC checks | `audit_ac.py`, `ac_preview.py` (Blender) | yes | Nothing in the driving space; no leaves near the asphalt; driver's-eye renders with AC's shaders emulated. |

## Survey codes (`streetview_segments.json`)

One entry per stretch: `from_m`, `to_m`, `width_m`, `lanes`, `edge_lines` (none / left / right / both),
`centre_line` (none / dashed / solid), `left`, `right`, `left_height_m`, `right_height_m`, `surface`
(new / worn / patched), `vegetation`, `notes`.

Built today by `climb_furniture.py` / `ac_track.py`: `none`, `guardrail_steel`, `guardrail_wood`,
`drystone_wall`, `stone_wall`, `retaining_stone`, `retaining_concrete`, `concrete_kerb`, `rock_face`,
`rock_netting`, `fence`, `hedge`, `concrete_parapet`, `building`.

The planner also offers (each needs new code when used): `cable_barrier`, `concrete_barrier`
(New Jersey / F-shape), `plastered_wall`, `sidewalk`, `noise_barrier`, `ditch`, `delineators`,
`racing_kerb`, `armco`, `tyre_wall`, `catch_fence`, `gravel_trap`, `tarmac_runoff`; centre lines
`double` / `dashed_solid`; surfaces `concrete` / `cobbles` / `gravel`. Build them to the dimensions in
docs/QUALITY.md, as meshes generated in the same style as the existing guardrail / wall code.

## Street View cache (`tools/streetview_fetch.py`, `tools/streetview_views.py`)

Studio tools (`tools/`), used by the pipeline's `sv_*.py` and `survey_to_segments.py`. They work on
`builds/SLUG/streetview/` (images kept once in the studio's `cache/streetview/panos`), or on any
route with `--route FILE --out DIR`. See `CLAUDE.md` for the
rule (the user's decision, outside Google's terms) and docs/DATA_SOURCES.md 5.5 for the endpoints.

1. `python3 tools/streetview_fetch.py SLUG --plan`, then without `--plan` after the user's
   go-ahead. It reads `output/route_climb.geojson` (or `tracks/SLUG/route.geojson` before the
   scaffold), lists Google's own panoramas within `--max-offset` (12 m) of the line and keeps
   about one per `--spacing` (50 m; 0 = all, about every 10 m). `index.json` has, per panorama:
   `at_m` (route metres, scaled to the route's stated length so it matches `route_m`),
   `lateral_m` (+ right of the route line), position, id, capture month, the heading of the
   panorama's centre, pitch and roll, the route's bearing there; and `no_coverage`, the stretches
   over 75 m with no panorama at all. Images: `panos/<id>-stm.jpg`, 8192 × 4096 (`--keep-large`
   adds the 16000 × 8000 one). Re-running after a route edit reuses what is already there;
   `--reuse DIR` links panoramas downloaded elsewhere; `--offline` downloads nothing.
2. `.venv/bin/python tools/streetview_views.py SLUG` writes `views/<at_m>m.jpg` (the survey sheet:
   ahead, back, left, right of the driving direction, whichever way Google's car was going) and
   `ground/<at_m>m.png` + `ground/index.json` (the road plane from above, 16 × 30 m at 2 cm / px,
   driving direction up, transparent where Google hid its car).

**Survey.** Read the sheets in order and write `streetview_segments.json` as before; `at_m` is the
sheet's file name. The side views show what lines the road, the ahead / back views its width,
markings and surface. Check the capture month in the caption: one road is often a mix of years.
The route line is the planner's (OSM), so a sheet can be a few metres off the final centreline.

**Road textures.** The ground patches are georeferenced (centre = the panorama's position, up =
`up_bearing_deg`, `m_per_px`), so once `trace_climb.py` has the real centreline and widths they can
be resampled into the road's own (across, along) space: for each road texel take the patch whose
camera is nearest but not blurred there. They carry the real markings, repairs, cracks, stains,
manholes and colour; keep the asphalt grain from the CC0 detail texture, since the patches are
soft beyond 6 m. Each panorama hides its own car, and every point of the road is also seen from
the neighbours only when they are about 10 m apart: use `--spacing 0` on stretches meant for this.
**Not wired into `ac_textures.py` / `ac_track.py` yet**: the macro road surface is still the eight
procedural strips; replacing it with a mosaic of these patches is new code in the build, and the
build README must then say the road texture is Street View imagery.

Limits: flat-road assumption (banks, walls and cars smear outwards), camera height 2.0 m ± 5 %
(measured, see the tool), lighting and shadows baked in and different between capture dates.

**Widths: measure them on the patches.** First done on the first studio build;
the helpers are in `pipeline/scripts`:
- `sv_strips.py` makes compact strips (left | ahead | right, three stops per image) for reading
  the roadside; one line per stop goes into `survey_stops.tsv`, and `survey_to_segments.py` turns
  that into `streetview_segments.json` (each stop covers halfway to its neighbours) + readable notes.
- `sv_width_sheets.py` puts the road patches side by side under a metre ruler: read the asphalt
  width of every stop off it (good to ~0.3 m) into the last column of `survey_stops.tsv`.
  `sv_widths.py` tries it automatically from the white edge lines; it found both lines on a third
  of the stops and was up to 1 m off on some, so it is only a cross-check.
- `config.json` → `roads.climb_sv_measured: true`, `climb_sv_tol: 0.4`: `trace_climb.py` then keeps
  the built width within that of the survey's. **Without it the GeoDBT carriageway was 1.5–2.5 m
  too wide** on the first studio build (it takes in shoulders, lay-bys and yards): never give the trace its
  own width back as the survey's `width_m`, which removes the only independent check.
- Centreline check: the road centre between the edge lines in each patch, placed with the
  panorama's position, against the traced line. Expect a constant offset of about 1 m to the
  north-east (Google's positions are WGS 84 today, the Italian data ETRS89: plate motion since
  1989), harmless inside the game; what is left after removing it (0.55 m median there) is about
  the accuracy of Google's own positions, so this check cannot prove the 0.5 m target.

Other switches added in that build (now in `pipeline/`): `roads.climb_max_grade` (the reference's 0.17 limit over 10 m
flattened real 19–20 % ramps), barriers exactly as surveyed when the brief says `barriers: survey`
(no automatic guardrails on drops), track name / description / location from `config.json` in
`export_ac.py` (+ `[WEATHER_FX]` in `surfaces.ini`), and CSP 3D trees (below).

## Gaps to close for the studio's tracks

1. **Other countries.** Replace the Veneto fetchers (above) and keep their outputs. Region-specific
   look: building styles and textures, tree species, road-marking dimensions, sign designs, kerb and
   guardrail types (e.g. Sweden: cable barriers on 2+1 roads, granite kerbs, yellow-less markings
   with 1+2 / 3+9 m patterns: check the national standard), ground colours (from the orthophoto).
2. **Circuits.** Everything is point to point today. A closed loop needs: the road spline closed
   (no seam at the start), terrain and verges closed, `AC_TIME_0_L/R` at the start/finish plus
   `AC_TIME_n_L/R` per sector instead of `AC_AB_*`, a grid of `AC_START_n` behind the line,
   `AC_PIT_n` boxes (on the grid for a street circuit, or in the pit area the user marked) and
   `AC_HOTLAP_START_0`, a closed `fast_lane.ai`, `ui_track.json` `run` / `pitboxes`, and a
   `pit_lane.ai` only if a pit lane is built. At most two intermediate lines (`AC_TIME_1`,
   `AC_TIME_2`); `AC_PIT_n` / `AC_START_n` contiguous from 0, as many grid slots as pit boxes;
   unknown `AC_*` names stop the loader. Details and sources: docs/AC_NOTES.md.
3. **3D trees.** `ac_trees.py` places alpha-tested cards (crossed planes + canopy layers near the
   road). The studio asks for real 3D trees near the road. Two routes (docs/AC_NOTES.md → Trees):
   - **CSP trees** (CSP ≥ 0.1.79, `[TREES]` in `extension/ext_config.ini`, a `trees.txt` list of
     positions): real instanced 3D models with LODs (~10k / 6k / 3k triangles) and automatic
     billboards far away, meant for hundreds of thousands of trees. Models per species from
     Blender (the CC0 Poly Haven tree models used for the card renders, or generated), exported as
     FBX and converted to `.bin` with Content Manager's tree converter (or written directly). Best
     look and performance, but CSP-only, and CSP geometry carries no physics.
   - **Baked kn5 trees**: trunk / branch meshes + alpha-tested leaf clusters, 2–8k triangles,
     merged per tile with `lodIn` / `lodOut` (no instancing in the kn5): works in vanilla AC, heavy.
   Recommended: CSP trees within the 3D radius and beyond (the user already runs CSP), the
   existing kn5 cards as the vanilla fallback only if asked. Keep the clear-road rule (no leaves
   within 0.4 m of the asphalt, any height) and the invisible trunk colliders in the kn5.
   **Built** (first on the first studio build, now in `pipeline/scripts`): `ac_csp_tree_models.py`
   (Blender: decimated wood + foliage clusters as crossed cards with pictures of real clusters,
   three levels of detail, ~9k / 4.7k / 2k triangles), `ac_csp_trees.py` (writes the `.bin` tree
   files directly, in the layout of Content Manager's converter, + `trees.txt`), `ac_trees.py`
   (chooses the trees: within the 3D radius, crown clear of the asphalt; their cards stay in the
   kn5 as `trees_cspfb_*` meshes that the CSP config hides, so vanilla AC still shows cards).
   **Not yet confirmed in the game**: check the first track that uses it, and see that build's
   README for how to switch it off.
4. **New roadside types and racing kerbs** (above): profile meshes + collision + the right physics
   surface (kerbs: a `KERB` surface with rumble in `surfaces.ini`).
5. **Off-network legs** (planner "direct" legs): trace them on the orthophoto, give them a width
   and a profile, and make `climb.py` accept 0 way ids.
6. **Location everywhere** (done): `ui_track.json` geotags as strings, `country` / `city` from the
   brief and `[WEATHER_FX] LATITUDE / LONGITUDE / TIMEZONE_ID` in `data/surfaces.ini`, so CSP puts
   the sun where it really is; `tools/scaffold.py` sets the time zone for the countries it knows
   (else it asks in `_review`).
7. **Ponds**: `build_terrain.py` carves the basin; the water surface is not built in AC yet.

## Lessons already paid for (do not repeat)

- Exposure: every AC material's ksAmbient / ksDiffuse × `assetto_corsa.exposure` (0.5); 0.7 and 1.0
  looked over-exposed in game. The cockpit view looks brighter because of AC's auto-exposure, not
  the track.
- `fast_lane.ai` points 0.4 m above the asphalt: points a hair under the road crashed acs.exe
  (`AISpline::calculateNormals`).
- Mesh nodes ≤ 65,536 vertices; textures as DDS (BC1/BC3) embedded in the kn5; big textures cost
  loading time (an earlier build: kn5 978 MB, 700 MB of it textures).
- OSM's line is 5–15 m off under trees and cuts the inside of hairpins: never build the road on raw OSM.
- Raw orthophoto colours look "low res, very bright green": match to a palette, desaturate.
- Walls that exist only on the map: remove surveyed walls within 25 m of hairpin apexes unless seen.
- A flat card leaning over the road reads as a wall of leaves: no opaque card pixel within 0.4 m of
  the asphalt.
- Smooth the DEM along the road (it has spikes and steps), but keep the real grade; measure bumps
  (`audit_climb.py`, `climb_report.py`).
