#!/usr/bin/env bash
# The whole pipeline, in order (as run for the first studio build, v1.0). tools/scaffold.py
# copies this into builds/<slug>/run_all.sh with the track's slug and comments out the steps that
# need a region's or a track's own version (TODO lines). Downloads are cached in data/; Street View
# images in the studio's cache/streetview (private, see the studio's CLAUDE.md).
set -euo pipefail
STUDIO="$(cd "$(dirname "$0")/../.." && pwd)"
SLUG=__SLUG__
cd "$(dirname "$0")/scripts"
PY=../.venv/bin/python
BLENDER=/Applications/Blender.app/Contents/MacOS/Blender

"$PY" fetch_dem.py            # Veneto LiDAR 5 m + DTM5 (delete data/dem/*_index.json after moving the map)
"$PY" fetch_geodbt.py         # Veneto GeoDBT lots (config.json geodbt_lots)
"$PY" fetch_osm.py
# route: from the planner (tools/scaffold.py wrote ../output/route_climb.geojson)
"$PY" fetch_corridor.py climb --buffer 40                       # 15 cm orthophoto along the route
"$PY" fetch_ortho.py fullmap -4096 -4096 4096 4096 --res 1.0    # whole map: ground colour, crowns
"$PY" build_dem.py
"$PY" build_osm.py
"$PY" geodbt_crops.py         # GeoDBT vineyards / orchards OSM does not map
"$PY" climb.py

# Street View cache and survey (studio tools; images are private, see the studio's CLAUDE.md)
python3 "$STUDIO/tools/streetview_fetch.py" "$SLUG"             # ~50 m spacing; --plan shows the size first
"$PY" "$STUDIO/tools/streetview_views.py" "$SLUG"               # survey sheets + top-down road patches
"$PY" sv_strips.py --step 2                                     # left | ahead | right strips, a stop every ~100 m
# By hand: read the strips into ../survey_stops.tsv (one line per stop), and the asphalt width of
# each stop off the ruler sheets:  "$PY" sv_width_sheets.py 0 <route length> /tmp/ws --stops
"$PY" survey_to_segments.py   # -> ../streetview_segments.json + ../streetview_reference_notes.md

"$PY" trace_climb.py          # centreline on the topo carriageway + photo; width within the survey's tolerance
"$PY" road_profiles.py
"$PY" build_terrain.py
"$PY" build_buildings.py
"$PY" roof_analysis.py
"$PY" climb_furniture.py      # walls, guardrails, fences, kerbs from the survey
"$PY" build_terrain.py        # again: natural ground behind the retaining walls
"$PY" ortho_base.py
"$PY" scenery_inputs.py       # trees / bushes / vines to plant, ground colour, signs and extras
"$PY" climb_report.py
"$PY" audit_climb.py
# Assetto Corsa
"$PY" ac_track.py
"$PY" ac_horizon.py
"$PY" ac_scenery.py
"$PY" ac_vineyards.py         # vineyard rows in 3D (OSM / GeoDBT vineyards near the road; none: nothing)
if [ ! -f ../output/ac/csp_trees/models.json ]; then           # low-polygon 3D trees from the CC0 models (~3 min)
  "$BLENDER" -b --factory-startup --python ac_csp_tree_models.py
fi
"$PY" ac_trees.py             # cards everywhere + the list of trees that are 3D in CSP
"$PY" ac_csp_trees.py         # CSP tree files (.bin) + trees.txt
"$PY" ac_replay.py
"$PY" export_ac.py            # ../output/<slug>_ac.zip
"$PY" audit_ac.py
