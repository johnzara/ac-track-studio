#!/usr/bin/env python3
"""Create the build folder for a track designed in the planner.

    python3 tools/scaffold.py SLUG [--reference PATH] [--dest PATH] [--update] [--force]

Reads tracks/SLUG/track.json and makes builds/SLUG/ (or --dest) from the reference pipeline
(default: the studio's own pipeline/, see pipeline/README.md):

  scripts/, requirements.txt, streetview_index.py, LANDMARKS.md     copied from the reference
  landmarks/examples/*.html                                         the reference's hand-built
                                                                    landmark pages, as examples
  .venv                                                             link to the reference's Python env
  data/geodbt/<lot>                                                 links to cache/geodbt/<lot>
                                                                    (Veneto GeoDBT, when cached)
  data/ac_assets/{polyhaven,polyhaven_tex,ambientcg}                symlinks (shared CC0 textures);
                                                                    data/ac_assets/copernicus is new
  config.json          the reference config with this track's map frame, timing, detail settings;
                       track-specific lists emptied; "_review" lists what still needs a decision
  output/route_climb.geojson   the planner's route in route.py's format (local metres, OSM node /
                       way ids); 0 marks a snapped point between nodes or an off-network edge
  streetview_segments.user.json   the user's survey segments (pipeline format)
  run_all.sh           the reference run script with route.py replaced by the planner's route and
                       region- / track-specific steps commented out with a TODO
  brief/               copies of BRIEF.md, track.json, route.geojson
  streetview/          not made here: tools/streetview_fetch.py SLUG fills it (Street View cache);
                       --update and --force leave it alone
  SCAFFOLD.md          what was done and what to review

--update rewrites only config.json's studio-managed keys, the route, the user's segments and brief/
(after the track was edited in the planner); --force overwrites an existing build folder's copies.
Standard library only (the UTM maths is inline), so any python3 runs it.
"""

import argparse
import json
import math
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

STUDIO = Path(__file__).resolve().parent.parent
DEFAULT_REFERENCE = STUDIO / "pipeline"

# run_all.sh steps by kind (script names of the reference pipeline)
REGION_STEPS = {"fetch_dem.py", "fetch_geodbt.py", "fetch_corridor.py", "fetch_ortho.py", "geodbt_crops.py",
                "roof_analysis.py"}
TRACK_STEPS = {"landmarks_climb.py", "sites.py", "landmarks.py"}
VENETO_ONLY_KEYS = ("geodbt_lots",)
TRACK_LISTS = {"name_signs": [], "landmark_overrides": [], "ponds": [], "sites": {}}
# keys of older configs the pipeline no longer reads (the BeamNG level, track-specific extras)
DROP_KEYS = ("finish_gate", "italy_town_zone", "vegetation", "terrain_material_names", "road_details", "dynamic_rocks",
             "spawn_points", "fog_height_m", "fog_density", "roadside", "max_height_m", "terrain_base_z")
# IANA time zone for CSP's sun ([WEATHER_FX]); countries with several zones need a check
TIMEZONES = {"it": "Europe/Rome", "se": "Europe/Stockholm", "no": "Europe/Oslo", "fi": "Europe/Helsinki",
             "dk": "Europe/Copenhagen", "ch": "Europe/Zurich", "at": "Europe/Vienna", "de": "Europe/Berlin",
             "fr": "Europe/Paris", "es": "Europe/Madrid", "pt": "Europe/Lisbon", "nl": "Europe/Amsterdam",
             "be": "Europe/Brussels", "gb": "Europe/London", "ie": "Europe/Dublin", "cz": "Europe/Prague",
             "si": "Europe/Ljubljana", "pl": "Europe/Warsaw", "jp": "Asia/Tokyo", "nz": "Pacific/Auckland"}


# ------------------------------------------------------------------------------------------- UTM
def to_utm(lon, lat, zone, south):
    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    phi, lam = math.radians(lat), math.radians(lon)
    n = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    t = math.tan(phi) ** 2
    c = ep2 * math.cos(phi) ** 2
    aa = math.cos(phi) * (lam - lon0)
    e4, e6 = e2 * e2, e2 ** 3
    m = a * ((1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
             - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * phi)
             + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * phi)
             - (35 * e6 / 3072) * math.sin(6 * phi))
    east = k0 * n * (aa + (1 - t + c) * aa ** 3 / 6 + (5 - 18 * t + t * t + 72 * c - 58 * ep2) * aa ** 5 / 120) + 500000
    north = k0 * (m + n * math.tan(phi) * (aa * aa / 2 + (5 - t + 9 * c + 4 * c * c) * aa ** 4 / 24
                                           + (61 - 58 * t + t * t + 600 * c - 330 * ep2) * aa ** 6 / 720))
    return east, north + (10000000 if south else 0)


# ------------------------------------------------------------------------------------- helpers
def load_spec(slug):
    path = STUDIO / "tracks" / slug / "track.json"
    if not path.exists():
        sys.exit(f"{path} not found: save the brief from the planner first (serve.py → Generate brief → Save).")
    spec = json.loads(path.read_text())
    if not str(spec.get("schema", "")).startswith("ac-track-studio/track@"):
        sys.exit(f"{path} is not an AC Track Studio track.json")
    if not spec.get("route"):
        sys.exit("track.json has no route")
    return spec, path.parent


def is_veneto(spec):
    p = spec.get("place") or {}
    return p.get("country_code") == "it" and "veneto" in (p.get("region") or "").lower()


def route_geojson(spec, frame):
    """The planner's route in the format of the reference route.py (output/route_<name>.geojson)."""
    r = spec["route"]
    zone, south = frame["utm_zone"], frame["hemisphere"] == "S"
    ce, cn = frame["center_utm"]
    half = frame["extent_m"] / 2
    xy = [to_utm(lon, lat, zone, south) for lon, lat in r["coordinates"]]
    local = [[round(e - ce, 2), round(n - cn, 2)] for e, n in xy]
    length = sum(math.dist(a, b) for a, b in zip(local[:-1], local[1:]))
    inside = all(abs(x) < half and abs(y) < half for x, y in local)
    nodes = [int(n) if n is not None else 0 for n in r["osm_nodes"]]
    edges = [int(w) if w is not None else 0 for w in r["edge_way_ids"]]
    streets = []
    for s in r.get("streets", []):
        name = s.get("name") or s.get("ref") or s.get("highway") or "?"
        if not streets or streets[-1] != name:
            streets.append(name)
    return {"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": r["coordinates"]},
        "properties": {
            "name": "climb", "length_m": round(length, 1), "inside_map": inside, "closed": bool(r.get("closed")),
            "local_xy": local, "osm_way_ids": sorted({w for w in edges if w}), "osm_nodes": nodes,
            "edge_way_ids": edges, "streets": streets,
            "virtual_nodes": [] if all(n is None for n in r["osm_nodes"]) else [i for i, n in enumerate(r["osm_nodes"]) if n is None],
            "off_network_edges": [i for i, w in enumerate(r["edge_way_ids"]) if w is None],
            "source": "AC Track Studio planner (tools/scaffold.py)",
        },
    }]}, inside, length


def make_config(ref_cfg, spec, slug, veneto, existing=None):
    """Reference config + this track's values. With `existing`, only studio-managed keys change."""
    fr, tr, d = spec["map_frame"], spec["track"], spec["detail"]
    cfg = json.loads(json.dumps(existing if existing is not None else ref_cfg))
    review = []
    cfg["_comment"] = (f"Map definition for {tr['name']} (Assetto Corsa track). Generated by AC Track Studio "
                       f"(tools/scaffold.py) from tracks/{slug}/track.json on the reference pipeline's config; "
                       "keys starting with _ are notes.")
    cfg.update({
        "level_name": slug, "level_title": tr["name"],
        "level_description": tr.get("description") or f"Private recreation of real roads: {tr['name']}.",
        "level_authors": tr.get("author") or "private build (AC Track Studio)",
        "crs": f"EPSG:{fr['epsg']}", "center_utm": fr["center_utm"],
        "terrain_size_px": fr["terrain_size_px"], "meters_per_px": fr["meters_per_px"],
    })
    if existing is None:
        if not veneto:
            for k in VENETO_ONLY_KEYS:
                cfg.pop(k, None)
            cfg["dem"] = {"source_margin_m": cfg.get("dem", {}).get("source_margin_m", 300), "fallback": "copernicus_glo30",
                          "_todo": "national / regional DTM for this country (docs/DATA_SOURCES.md); fetch_dem.py is Veneto-only"}
            review.append("dem / geodbt: the reference fetchers are Veneto-only; write fetchers for this country's DTM, "
                          "orthophoto and topographic data (keep the output formats)")
        cfg.update(json.loads(json.dumps(TRACK_LISTS)))
        for k in DROP_KEYS:
            cfg.pop(k, None)
        half = fr["extent_m"] / 2
        cfg["detail_areas"] = {"_comment": "Measured carriageway widths / crop map for every paved road in these boxes.",
                               "areas": [{"name": "whole map", "bounds": [-half, -half, half, half],
                                          "measured_road_widths": veneto, "geodbt_crops": veneto, "shrines": True}]}
        cfg.setdefault("roads", {})["route"] = "climb"
        for k in ("beamng_materials", "shoulder_m", "falloff_m"):
            cfg["roads"].pop(k, None)
    t = spec["timing"]
    if tr["type"] == "circuit":
        cfg.pop("hillclimb", None)
        cfg["circuit"] = {
            "_comment": "Closed loop: the reference pipeline is point to point; ac_track.py / export_ac.py / ac_replay.py "
                        "need circuit support (docs/PIPELINE.md → Circuits).",
            "start_finish_m": t["start_finish"]["at_m"],
            "sectors": [{"name": s["name"], "route_m": s["at_m"]} for s in t.get("sectors", [])],
            "laps": t.get("laps", 5), "grid_slots": t.get("grid_slots", 12), "grid_spacing_m": t.get("grid_spacing_m", 8),
            "pits": t.get("pits"),
        }
        review.append("circuit: closed road mesh, AC_TIME_n gates, grid / pits and a closed AI line are new work")
    else:
        cfg.pop("circuit", None)
        cfg["hillclimb"] = {"_comment": "Timed run along the route (metres from its first point).",
                            "start_m": t["start"]["at_m"], "finish_m": t["finish"]["at_m"],
                            "splits": [{"name": s["name"], "route_m": s["at_m"]} for s in t.get("splits", [])]}
    ac = cfg.setdefault("assetto_corsa", {})
    ac["widen_m"] = d.get("road_widen_m", ac.get("widen_m", 1.0))
    ac.setdefault("exposure", 0.5)
    if existing is None:
        place = spec.get("place") or {}
        cc = place.get("country_code") or ""
        if cc in TIMEZONES:
            ac["timezone_id"] = TIMEZONES[cc]
        else:
            review.append(f"assetto_corsa.timezone_id: set the IANA time zone of the track (country '{cc or '?'}'), "
                          "for the sun position in CSP")
        if place.get("country"):
            ac["ui_tags"] = [t for t in ac.get("ui_tags", []) if t != place["country"].lower()] + [place["country"].lower()]
        if tr["type"] == "circuit":
            ac["pitboxes"] = int(t.get("pits", {}).get("count") or t.get("grid_slots", 12))
            ac["ui_run"] = "clockwise" if "counter" not in tr.get("direction", "") else "counter-clockwise"
            ac["ui_tags"] = ["circuit"] + [x for x in ac.get("ui_tags", []) if x not in ("hillclimb", "point to point")]
    cfg["studio"] = {
        "_comment": "Settings from the AC Track Studio planner: the quality bar is docs/QUALITY.md.",
        "track_json": str(STUDIO / "tracks" / slug / "track.json"),
        "type": tr["type"], "place": spec.get("place"), "detail": d,
        "data_sources": spec.get("data_sources"),
        "scaffolded": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if existing is None:
        cfg["_review"] = review
    return cfg, review


def run_script(ref_text, spec, veneto):
    ref_text = ref_text.replace("__SLUG__", spec["track"]["slug"])
    out = []
    country = (spec.get("place") or {}).get("country") or "this country"
    for line in ref_text.splitlines():
        m = re.search(r'"\$PY"\s+([\w.]+\.py)', line)
        script = m.group(1) if m else None
        if script == "route.py":
            out.append("# route: from the planner (tools/scaffold.py wrote ../output/route_climb.geojson). To re-route on")
            out.append("# fresh OSM data instead, run route.py with the waypoints in brief/track.json.")
            out.append("# " + line)
        elif script in REGION_STEPS and not veneto:
            out.append(f"# TODO(region): Veneto-only source; replace with the {country} equivalent (docs/DATA_SOURCES.md)")
            out.append("# " + line)
        elif script in TRACK_STEPS:
            out.append("# TODO(track): specific to the reference track; adapt or drop")
            out.append("# " + line)
        else:
            out.append(line)
    text = "\n".join(out) + "\n"
    header = (f"# {spec['track']['name']} (Assetto Corsa track): the studio pipeline's run script,\n"
              "# adapted by tools/scaffold.py. Lines marked TODO need this track's / region's version.\n"
              "# Street View: tools/streetview_fetch.py + streetview_views.py (studio; images in its cache/streetview,\n"
              "# linked from streetview/), or\n"
              "# metadata-only indexing with GOOGLE_MAPS_API_KEY in the environment.\n")
    text = re.sub(r"^(#!/usr/bin/env bash\n)(#[^\n]*\n)*", lambda m: m.group(1) + header, text, count=1)
    return text


def link(dst, target):
    """dst -> target; relative when both are inside the studio, so the studio can move as a whole."""
    if dst.is_symlink() or dst.exists():
        return False
    if not Path(target).exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    t = Path(target).resolve()
    inside = t.is_relative_to(STUDIO) and dst.resolve().parent.is_relative_to(STUDIO)
    dst.symlink_to(os.path.relpath(t, dst.parent.resolve()) if inside else t)
    return True


SEG_KEYS = ("from_m", "to_m", "width_m", "lanes", "edge_lines", "centre_line", "left", "right",
            "left_height_m", "right_height_m", "surface", "vegetation", "notes")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    ap.add_argument("--dest", type=Path)
    ap.add_argument("--update", action="store_true", help="refresh config / route / brief of an existing build")
    ap.add_argument("--force", action="store_true", help="overwrite copied files in an existing build")
    a = ap.parse_args()

    spec, track_dir = load_spec(a.slug)
    ref = a.reference.resolve()
    if not (ref / "scripts").is_dir() or not (ref / "config.json").exists():
        sys.exit(f"{ref} does not look like the reference pipeline (scripts/ and config.json expected)")
    dest = (a.dest or STUDIO / "builds" / a.slug).resolve()
    exists = (dest / "config.json").exists()
    if exists and not (a.update or a.force):
        sys.exit(f"{dest} already exists: use --update (config / route / brief only) or --force (re-copy scripts too)")
    if a.update and not exists:
        sys.exit(f"{dest} has no config.json to update: run without --update first")
    veneto = is_veneto(spec)
    frame = spec["map_frame"]
    notes = []

    dest.mkdir(parents=True, exist_ok=True)
    (dest / "output").mkdir(exist_ok=True)
    (dest / "data").mkdir(exist_ok=True)

    if not a.update:
        if (dest / "scripts").exists():
            shutil.rmtree(dest / "scripts")
        shutil.copytree(ref / "scripts", dest / "scripts", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for f in ("requirements.txt", "streetview_index.py", "LANDMARKS.md"):
            if (ref / f).exists():
                shutil.copy2(ref / f, dest / f)
        src_pages = next((d for d in (ref / "landmarks" / "examples", ref / "landmarks" / "src") if d.is_dir()), None)
        if src_pages:
            ex = dest / "landmarks" / "examples"
            ex.mkdir(parents=True, exist_ok=True)
            for f in src_pages.glob("*.html"):
                shutil.copy2(f, ex / f.name)
            (dest / "landmarks" / "src").mkdir(exist_ok=True)
            (dest / "landmarks" / "glb").mkdir(exist_ok=True)
            lj = dest / "landmarks" / "landmarks.json"
            if not lj.exists():
                lj.write_text("{}\n")
        if link(dest / ".venv", ref / ".venv"):
            notes.append(f"linked .venv → {(ref / '.venv').resolve()}")
        assets = ref / "data" / "ac_assets"
        if assets.exists():
            (dest / "data" / "ac_assets").mkdir(parents=True, exist_ok=True)
            for sub in ("polyhaven", "polyhaven_tex", "ambientcg"):
                if link(dest / "data" / "ac_assets" / sub, assets / sub):
                    notes.append(f"linked data/ac_assets/{sub} (shared CC0 textures: do not write into it)")
            (dest / "data" / "ac_assets" / "copernicus").mkdir(exist_ok=True)
        if veneto:
            for lot in sorted(set(json.loads((ref / "config.json").read_text()).get("geodbt_lots", {}).values())):
                if link(dest / "data" / "geodbt" / lot, STUDIO / "cache" / "geodbt" / lot):
                    notes.append(f"linked data/geodbt/{lot} → cache/geodbt/{lot} (shared download)")
        rs = (ref / "run_all.sh").read_text()
        (dest / "run_all.reference.sh").write_text(rs)
        (dest / "run_all.sh").write_text(run_script(rs, spec, veneto))
        (dest / "run_all.sh").chmod(0o755)

    ref_cfg = json.loads((ref / "config.json").read_text())
    existing = json.loads((dest / "config.json").read_text()) if a.update else None
    cfg, review = make_config(ref_cfg, spec, a.slug, veneto, existing)
    (dest / "config.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")

    gj, inside, length = route_geojson(spec, frame)
    (dest / "output" / "route_climb.geojson").write_text(json.dumps(gj))
    if not inside:
        notes.append("WARNING: the route leaves the map frame: enlarge terrain_size_px / meters_per_px")
    segs = [{k: g.get(k) for k in SEG_KEYS} for g in spec.get("streetview", {}).get("segments", [])]
    (dest / "streetview_segments.user.json").write_text(json.dumps(segs, indent=1, ensure_ascii=False) + "\n")

    (dest / "brief").mkdir(exist_ok=True)
    for f in ("BRIEF.md", "track.json", "route.geojson"):
        if (track_dir / f).exists():
            shutil.copy2(track_dir / f, dest / "brief" / f)

    r = spec["route"]
    lines = [
        f"# Scaffold: {spec['track']['name']}",
        "",
        f"Made by `tools/scaffold.py {a.slug}` on {datetime.now().strftime('%Y-%m-%d %H:%M')} from `{track_dir}/track.json`,",
        f"on the reference pipeline `{ref}`. The brief is in `brief/BRIEF.md`.",
        "",
        "## What is here",
        "",
        f"- `config.json`: map frame EPSG:{frame['epsg']}, centre {frame['center_utm']}, "
        f"{frame['terrain_size_px']} px × {frame['meters_per_px']} m; "
        + ("hill-climb timing" if spec['track']['type'] != 'circuit' else "a `circuit` section (new)") + "; `studio` holds the planner's detail settings.",
        f"- `output/route_climb.geojson`: the planner's route, {length / 1000:.2f} km in the local frame "
        f"(planner: {r['length_m'] / 1000:.2f} km), {'inside' if inside else 'NOT inside'} the map; "
        + ("no OSM node ids (the planner used the directions service: way ids only, nodes 0); "
           if all(n is None for n in r['osm_nodes']) else
           f"{sum(1 for n in r['osm_nodes'] if n is None)} snapped points between OSM nodes marked 0; ")
        + f"{sum(1 for w in r['edge_way_ids'] if w is None)} off-network edges marked 0.",
        f"- `streetview_segments.user.json`: {len(segs)} segment(s) surveyed by the user (authoritative).",
        "- `run_all.sh`: the reference run script; `route.py` and the region / track-specific steps are commented out with TODOs.",
        "- `scripts/`: a copy of the reference pipeline (edit freely here).",
        f"- Street View cache (not made yet): `python3 tools/streetview_fetch.py {a.slug} --plan` from the studio folder shows the "
        "count and size; after the user's go-ahead run it without `--plan`, then `.venv/bin/python ../../tools/streetview_views.py "
        f"{a.slug}` for the survey sheets and road patches in `streetview/` (rule in the studio's CLAUDE.md).",
        "",
        "## Review before running",
        "",
        *[f"- {x}" for x in review],
        *[f"- {x}" for x in notes],
        "- `climb.py` reads OSM tags for every `edge_way_ids` entry: 0 (off-network) needs handling, and the ids must exist in the fresh `data/osm/raw.json`.",
        "- The reference's tree cards and vegetation species are Italian; the brief asks for 3D trees of this region's species.",
        "",
    ]
    (dest / "SCAFFOLD.md").write_text("\n".join(lines))
    print(f"{'Updated' if a.update else 'Created'} {dest}")
    print(f"  route {length / 1000:.2f} km ({'inside' if inside else 'OUTSIDE'} the {frame['extent_m'] / 1000:.2f} km map), "
          f"{len(segs)} user segment(s); see SCAFFOLD.md for the review list")


if __name__ == "__main__":
    main()
