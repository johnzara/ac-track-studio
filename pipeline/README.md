# The studio's pipeline

Every build starts here: `tools/scaffold.py <slug>` copies `scripts/`, fills `config.json` and
`run_all.sh` in for the track, and links the build's `.venv` and assets to the ones in this folder.
Docs: `../docs/PIPELINE.md` (stages, what is generic, what to replace for other regions).

## History

- Earlier BeamNG and Assetto Corsa hill-climb projects (a pipeline built up over three tracks).
  Those projects are on the external drive;
  their READMEs explain most choices.
- 2026-10-13, first studio build (its `builds/<slug>/README.md` has the
  details). Added, all generic and driven by `config.json`:
  - `sv_strips.py`, `sv_width_sheets.py`, `sv_widths.py`, `survey_to_segments.py`: the survey
    read from the Street View cache, widths measured on the rectified road patches
    (`roads.climb_sv_measured`, `climb_sv_tol`, `climb_sv_tol_up`);
  - `ac_csp_tree_models.py` (Blender), `ac_csp_trees.py`, `ac_csp_tree_preview.py`, changes to
    `ac_trees.py`: real 3D trees near the road for Custom Shaders Patch (`assetto_corsa.csp_trees`);
  - `ac_track.py`: pit boxes / start slots before the start line (`assetto_corsa.pitboxes`);
  - `ac_scenery.py`: buildings moved off the asphalt edge; tracks without name signs;
  - `export_ac.py`: name, description, tags, country, town and time zone from `config.json`
    (`assetto_corsa.ui_*`, `timezone_id`, `studio.place`), `[WEATHER_FX]` for CSP's sun;
  - `climb_furniture.py`: no automatic guardrails when the brief wants barriers as surveyed;
    `trace_climb.py`: `roads.climb_max_grade`;
  - `build_report.py`: the numbers for the build README.
- 2026-10-14: moved into the studio (this folder), with its own Python environment and assets.
- 2026-10-14: **Assetto Corsa only.** The BeamNG stage is gone: `export_beamng.py` and everything
  only it used (level files, AI paths, timing objects, decal roads, bridge and rock props, stock
  Italy buildings / assets, the level previews, the 16-bit heightmap). What the AC stages read
  from it now comes from `scenery_inputs.py` (planted objects, ground colour, extras and signs in
  `output/scenery/`); the ground colours are a fixed palette (`scripts/ground_palette.json`)
  instead of being read from the Italy level; `config.json` lost the BeamNG keys
  (`terrain_material_names`, `spawn_points`, fog, `max_height_m` / `terrain_base_z`, …).

## What is here

| Path | What | In git |
|---|---|---|
| `scripts/` | the pipeline (Python; Blender for the `ac_tree_*`, `ac_csp_tree_models`, `ac_preview` scripts) | yes |
| `config.json` | template for a track's config: defaults, notes on every key; the scaffold fills in the track | yes |
| `run_all.sh` | every step in order (`__SLUG__` is replaced by the scaffold) | yes |
| `requirements.txt` | Python packages; `.venv/` is made from it | yes |
| `landmarks/examples/` | hand-built landmark pages (three.js) to copy from, `LANDMARKS.md` explains them | yes |
| `.venv/` | Python 3.14 environment: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` | no |
| `data/ac_assets/{polyhaven,polyhaven_tex,ambientcg}` | CC0 textures and the Poly Haven tree models (cards, CSP trees) | no |

Shared downloads live in the studio's `cache/` (not in git): `cache/streetview/panos` (Street View
panoramas, private, see `../CLAUDE.md`) and `cache/geodbt/<lot>` (Veneto GeoDBT lots).

## Improving it

Change a script in a build first, prove it there, then copy it back here when other tracks
benefit: behind a `config.json` key with a default that keeps the old behaviour, a note on the key
in `config.json`, and a line under History. A change only one road needs stays in its build.
