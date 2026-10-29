#!/usr/bin/env python3
"""Assetto Corsa track folder + zip from the parts built by ac_track.py (road, terrain, walls),
ac_horizon.py (distant mountains), ac_scenery.py (buildings, landmarks, signs) and ac_trees.py.

  output/ac/<track>/              (track = config.json level_name)
    <track>.kn5                everything: road, terrain, walls, guardrails, buildings, trees, markers
    ai/fast_lane.ai            the AI line (ac_replay.py): replay cameras, Time Attack, AI cars, apps
    data/cameras.ini, cameras_1.ini, cameras_2.ini
                               replay camera sets TV 1 / TV 2 / Helicam (ac_replay.py; F3 in a replay)
    data/surfaces.ini          ROAD (no artificial bumps) and GRASS
    data/map.ini, map.png      route map for the in-game map app
    ui/ui_track.json, ui/preview.png, ui/outline.png
    extension/ext_config.ini   Custom Shaders Patch: 3D grass (none on the shoulders), tiling fix, horizon,
                               rain (optional)
  output/<track>_ac.zip       that folder, ready for Content Manager (drag and drop) or
                              content/tracks/ of the game

Point to point: the run is timed between AC_AB_START_L/R and AC_AB_FINISH_L/R (the hill-climb
start and finish of config.json); the car spawns (AC_PIT_0, AC_START_0, AC_HOTLAP_START_0) a few
metres before the start line. Local frame -> AC: (x, y, z) -> (x, z, -y).
"""

import json
import shutil
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import dds
import kn5
from ac_track import AC_DIR, TEX_DIR, resize_bands
from common import OUTPUT, Config

TRACK = Config()["level_name"]
REPLAY = AC_DIR / "replay"
DDS_DIR = AC_DIR / "dds"
SPAWN_LIFT = 0.5              # m above the road: the car is dropped onto it


def to_ac(p):
    p = np.asarray(p, float)
    return np.stack([p[..., 0], p[..., 2], -p[..., 1]], -1)


# ----------------------------------------------------------------------------- textures, materials
PARTS = ("geometry", "horizon", "scenery", "trees", "vines")    # output/ac/<part>.npz + .json, when present
# every material's ksAmbient / ksDiffuse is scaled by this (config.json assetto_corsa.exposure). An earlier build:
# 1.0 (v0.3) was badly over-exposed, 0.7 (v0.4-v0.6) still too bright under CSP / Sol; community CSP
# tunings put multilayer grass at ksAmbient ~0.15 / ksDiffuse ~0.16, so here 0.5 (grass 0.21 / 0.31).
EXPOSURE = float(Config().raw.get("assetto_corsa", {}).get("exposure", 0.5))
DEFAULT_PROPS = {"ksAmbient": 0.5, "ksDiffuse": 0.8, "ksSpecular": 0.05, "ksSpecularEXP": 10.0,
                 "ksEmissive": (0.0, 0.0, 0.0), "ksAlphaRef": 0.0}


class Textures:
    """Texture specs -> embedded files {name: bytes}, converted once and cached in output/ac/dds."""

    def __init__(self):
        DDS_DIR.mkdir(parents=True, exist_ok=True)
        self.blobs, self.by_key, self.src_of = {}, {}, {}

    def add(self, file, fmt, alpha=None, max_px=None):
        path = Path(file) if Path(file).is_absolute() else TEX_DIR / file
        key = (str(path), fmt, alpha, max_px)
        if key in self.by_key:
            return self.by_key[key]
        ext = {"DDS": ".dds", "PNG": ".png"}.get(fmt, ".dds")
        stem = path.name.split(".")[0] + (f"_a{alpha}" if alpha is not None else "") + (f"_{max_px}" if max_px else "")
        name = stem + ext
        n = 1
        while name in self.src_of and self.src_of[name] != key:        # same stem, different source
            name = f"{stem}_{n}{ext}"
            n += 1
        cache = DDS_DIR / name
        if fmt == "DDS":
            data = path.read_bytes()
        else:
            if not cache.exists() or cache.stat().st_mtime < path.stat().st_mtime:
                im = Image.open(path)
                im = im.convert("RGBA" if (fmt == "DXT5" or alpha is not None) else "RGB")
                if max_px and max(im.size) > max_px:
                    f = max(im.size) / max_px
                    im = resize_bands(im, (max(4, round(im.width / f)), max(4, round(im.height / f))))
                if alpha is not None:
                    im.putalpha(alpha)
                if fmt == "PNG":
                    im.save(cache)
                else:
                    dds.write(im, cache, fmt, straight_alpha=True)
            data = cache.read_bytes()
        self.blobs[name] = data
        self.by_key[key] = name
        self.src_of[name] = key
        return name


def material(name, spec, textures):
    """kn5 material from a spec; ksAmbient and ksDiffuse scaled by EXPOSURE."""
    maps = []
    for t in spec["textures"]:
        if isinstance(t, dict):
            maps.append((t["sampler"], textures.add(t["file"], t["fmt"], t.get("alpha"), t.get("max_px", spec.get("max_px")))))
        else:
            maps.append((t[0], textures.add(t[1], t[2], None, spec.get("max_px"))))
    props = DEFAULT_PROPS | spec.get("props", {})
    props = props | {k: props[k] * EXPOSURE for k in ("ksAmbient", "ksDiffuse")}
    return kn5.Material(name, spec["shader"], props, maps,
                        blend=spec.get("blend", 0), alpha_tested=spec.get("alpha_tested", False),
                        depth=spec.get("depth", 0))


def load_parts():
    """Meshes and materials of every part; a material name used by two parts with different specs
    is renamed in the later one."""
    textures, mats, mat_index, spec_of, meshes, info = Textures(), [], {}, {}, [], {}
    for part in PARTS:
        if not (AC_DIR / f"{part}.json").exists():
            continue
        meta = json.loads((AC_DIR / f"{part}.json").read_text())
        G = np.load(AC_DIR / f"{part}.npz")
        rename = {}
        for name, spec in meta["materials"].items():
            final = name
            if name in spec_of and spec_of[name] != spec:
                final = f"{name}_{part}"
            rename[name] = final
            if final not in mat_index:
                mat_index[final] = len(mats)
                spec_of[final] = spec
                mats.append(material(final, spec, textures))
        for k, m in enumerate(meta["meshes"]):
            meshes.append(kn5.Mesh(
                name=m["name"], material=mat_index[rename[m["material"]]],
                pos=to_ac(G[f"m{k}_pos"]), nrm=to_ac(G[f"m{k}_nrm"]), uv=G[f"m{k}_uv"], tri=G[f"m{k}_tri"],
                cast_shadows=m.get("cast_shadows", True), renderable=m.get("renderable", True),
                lod_out=m.get("lod_out", 0.0)))
        info[part] = {k: v for k, v in meta.items() if k not in ("meshes", "materials")}
        print(f"  {part}: {len(meta['meshes'])} meshes, {len(meta['materials'])} materials")
    return textures.blobs, mats, meshes, info


# ----------------------------------------------------------------------------- markers
def dummy(name, pos, fwd, lift=0.0):
    z = to_ac(np.r_[fwd[:2], 0.0])
    z /= np.linalg.norm(z)
    y = np.array([0.0, 1.0, 0.0])
    x = np.cross(y, z)                                            # X = Y x Z
    t = to_ac(np.asarray(pos) + [0, 0, lift])
    return kn5.Dummy(name, np.array([[*x, 0], [*y, 0], [*z, 0], [*t, 1]], float))


# ----------------------------------------------------------------------------- data files
SURFACES = """[SURFACE_0]
KEY=ROAD
FRICTION=0.98
DAMPING=0
WAV=
WAV_PITCH=0
FF_EFFECT=NULL
DIRT_ADDITIVE=0
IS_VALID_TRACK=1
BLACK_FLAG_TIME=0
SIN_HEIGHT=0
SIN_LENGTH=0
IS_PITLANE=0
VIBRATION_GAIN=0
VIBRATION_LENGTH=0

[SURFACE_1]
KEY=GRASS
FRICTION=0.75
DAMPING=0
WAV=grass.wav
WAV_PITCH=0
FF_EFFECT=NULL
DIRT_ADDITIVE=1
IS_VALID_TRACK=0
BLACK_FLAG_TIME=0
SIN_HEIGHT=0.03
SIN_LENGTH=0.5
IS_PITLANE=0
VIBRATION_GAIN=0.2
VIBRATION_LENGTH=0.6

[SURFACE_2]
KEY=KERB
FRICTION=0.92
DAMPING=0
WAV=kerb.wav
WAV_PITCH=1.3
FF_EFFECT=1
DIRT_ADDITIVE=0
IS_VALID_TRACK=1
BLACK_FLAG_TIME=0
SIN_HEIGHT=0
SIN_LENGTH=0
IS_PITLANE=0
VIBRATION_GAIN=0.4
VIBRATION_LENGTH=1.5

[SURFACE_3]
KEY=SAND
FRICTION=0.8
DAMPING=0.1
WAV=sand.wav
WAV_PITCH=0
FF_EFFECT=NULL
DIRT_ADDITIVE=1
IS_VALID_TRACK=0
BLACK_FLAG_TIME=0
SIN_HEIGHT=0.04
SIN_LENGTH=0.5
IS_PITLANE=0
VIBRATION_GAIN=0.2
VIBRATION_LENGTH=0.3
"""


# Custom Shaders Patch (optional; vanilla AC ignores the file). Material names: terrain_<i><j> and
# terrain_c<k> (ksMultilayer, mask R meadow and lawns / G forest floor / B gravel shoulders, yards and
# stony ground / A paved), road (the climb), rock_face, tree_cards, horizon.
CSP_CONFIG = """; Custom Shaders Patch settings (read from the track folder)
[INCLUDE: common/materials_track.ini]

[BASIC]
SUPPORTS_WIND = 1

[LIGHTING]
ENABLE_TREES_LIGHTING = 0

; distant mountains out to 30 km: not clipped by the far plane
[Material_DistantGeometry]
Materials = horizon

; tiling fix for the photo detail textures on the terrain
[SHADER_REPLACEMENT_0_TERRAIN_TILING]
MATERIALS = terrain_?
PROP_0 = ksAlphaRef, -193

; tree cards: hide when seen edge-on, shadows from the leaves' alpha
[SHADER_REPLACEMENT_1_TREES]
MATERIALS = tree_cards
MATERIAL_FLAG_0 = 1
SEMITRANSPARENT_SHADOWS = TEXTURE

; 3D grass: mown meadows and lawns (mask R), sparse on the forest floor (G); none on the gravel
; shoulder along both edges of the climb, gravel yards, scree and rock (B), or paved ground (A).
; The mask is sharp (13 cm near the road), so it is not blurred.
[GRASS_FX]
GRASS_MATERIALS = terrain_?
OCCLUDING_MATERIALS = road, rock_face
MASK_BLUR = 0
SHAPE_SIZE = 1.0
TEXTURE = grass_fx/highlands.dds
TEXTURE_GRID = 8, 3

[GRASS_FX_CONFIGURATION_A]
SHAPE_SIZE = 1.2, 1.8
SHAPE_TIDY = 0.15
TRIM_PERIOD = 30

[GRASS_FX_CONFIGURATION_B]
MASK_MAIN_THRESHOLD = 1.1
SHAPE_SIZE = 0.8

[GRASS_FX_CONFIGURATION_D]
MASK_MAIN_THRESHOLD = 10

[GRASS_FX_ADJUSTMENT_0]
MATERIALS = terrain_?
USE_MULTILAYER_MASK = 1
MASK_R = A, 3
MASK_G = B, 3
MASK_B = D, 3
MASK_A = D, 3

[RAIN_FX]
PUDDLES_MATERIALS = road
SOAKING_MATERIALS = road
ROUGH_MATERIALS = terrain_?, rock_face
"""


def write_map(track_dir, centreline):
    """map.png (route, white on transparent) + data/map.ini, as Content Manager generates them:
    px = (X + X_OFFSET) / SCALE_FACTOR, py = (Z + Z_OFFSET) / SCALE_FACTOR (AC X, Z)."""
    ac = to_ac(np.c_[centreline, np.zeros(len(centreline))])
    X, Z = ac[:, 0], ac[:, 2]
    scale_factor, margin, size = 4.0, 20, 10                       # m per px
    w = int(np.ceil((X.max() - X.min()) / scale_factor)) + 2 * margin
    h = int(np.ceil((Z.max() - Z.min()) / scale_factor)) + 2 * margin
    xo, zo = -X.min() + margin * scale_factor, -Z.min() + margin * scale_factor
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).line(list(zip((X + xo) / scale_factor, (Z + zo) / scale_factor)),
                             fill=(255, 255, 255, 255), width=size, joint="curve")
    img.save(track_dir / "map.png")
    (track_dir / "data" / "map.ini").write_text(
        f"[PARAMETERS]\nWIDTH={w}\nHEIGHT={h}\nMARGIN={margin}\nSCALE_FACTOR={scale_factor}\n"
        f"X_OFFSET={xo:.3f}\nZ_OFFSET={zo:.3f}\nDRAWING_SIZE={size}\n")


def write_outline(path, centreline, size=(365, 192), width=4):
    xy = np.asarray(centreline)
    lo, hi = xy.min(0), xy.max(0)
    s = min((size[0] - 20) / (hi[0] - lo[0]), (size[1] - 20) / (hi[1] - lo[1]))
    off = (np.array(size) - (hi - lo) * s) / 2
    pts = [(off[0] + (p[0] - lo[0]) * s, size[1] - off[1] - (p[1] - lo[1]) * s) for p in xy]
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(img).line(pts, fill=(255, 255, 255, 255), width=width, joint="curve")
    img.save(path)


def write_ui(track_dir, cfg, meta):
    timed = meta["timed"]
    start = meta["markers"]["AC_AB_START_L"]["pos"]
    lon, lat = cfg.to_wgs84.transform(start[0] + cfg.center_e, start[1] + cfg.center_n)
    ac_cfg, place = cfg["assetto_corsa"], cfg.raw.get("studio", {}).get("place", {})
    ui = {
        "name": cfg["level_title"],
        "description": (f"{ac_cfg.get('ui_description', cfg['level_description'])} {timed['length_m'] / 1000:.1f} km, "
                        f"{timed['climb_m']} m of climbing, {timed['hairpins']} hairpin{'' if timed['hairpins'] == 1 else 's'}. "
                        "Replay cameras: TV 1, TV 2, Helicam (F3). " + ac_cfg.get("ui_credits", "")).strip(),
        "tags": ac_cfg.get("ui_tags", ["hillclimb", "point to point", "hills", "italy"]),
        "geotags": [f"{lat:.5f}", f"{lon:.5f}"],
        "country": place.get("country", "Italy"),
        "city": place.get("town", ""),
        "length": str(timed["length_m"]),
        "width": "-".join(str(v) for v in timed.get("width_m", [4, 8])),
        "pitboxes": str(sum(1 for k in meta["markers"] if k.startswith("AC_PIT_"))),
        "run": ac_cfg.get("ui_run", "Uphill"),
        "author": cfg["level_authors"],
        "version": str(ac_cfg.get("version", "1.0")),
        "year": 2026,
    }
    (track_dir / "ui" / "ui_track.json").write_text(json.dumps(ui, indent=2))
    write_outline(track_dir / "ui" / "outline.png", meta["centreline"])
    prev = AC_DIR / "preview_src.png"
    if prev.exists():                                              # render from ac_preview (optional)
        im = Image.open(prev).convert("RGB")
        w, h = im.size
        ch = int(w * 200 / 355)
        im.crop((0, (h - ch) // 2, w, (h - ch) // 2 + ch)).resize((355, 200), Image.LANCZOS).save(track_dir / "ui" / "preview.png")
    else:
        Image.new("RGB", (355, 200), (40, 44, 40)).save(track_dir / "ui" / "preview.png")


# ----------------------------------------------------------------------------- main
def main():
    cfg = Config()
    track_dir = AC_DIR / TRACK
    if track_dir.exists():
        shutil.rmtree(track_dir)
    for d in ("data", "ui", "ai"):
        (track_dir / d).mkdir(parents=True)

    textures, mats, meshes, info = load_parts()
    meta = info["geometry"]
    mk = meta["markers"]
    dummies = []
    for name, d in mk.items():
        lift = SPAWN_LIFT if name.startswith(("AC_START_", "AC_PIT_", "AC_HOTLAP_START_")) else 0.0
        dummies.append(dummy(name, d["pos"], d["fwd"], lift))
    size = kn5.write(track_dir / f"{TRACK}.kn5", textures, mats, meshes, dummies, root=TRACK)
    # real sun and time zone for CSP / Sol (docs/AC_NOTES.md A4.1); vanilla AC ignores the section
    start = meta["markers"]["AC_AB_START_L"]["pos"]
    lon, lat = cfg.to_wgs84.transform(start[0] + cfg.center_e, start[1] + cfg.center_n)
    weather = (f"\n[WEATHER_FX]\nLATITUDE = {lat:.5f}\nLONGITUDE = {lon:.5f}\n"
               f"TIMEZONE_ID = {cfg['assetto_corsa'].get('timezone_id', 'Europe/Rome')}\n")
    (track_dir / "data" / "surfaces.ini").write_text(SURFACES + weather)
    for f in sorted(REPLAY.glob("cameras*.ini")):                 # replay cameras + AI line (ac_replay.py)
        shutil.copy(f, track_dir / "data" / f.name)
    if (REPLAY / "fast_lane.ai").exists():
        shutil.copy(REPLAY / "fast_lane.ai", track_dir / "ai" / "fast_lane.ai")
    else:
        print("export_ac: no AI line (run ac_replay.py): no replay cameras switching, no Time Attack")
    (track_dir / "extension").mkdir()
    csp_trees = AC_DIR / "csp_trees"                             # 3D trees near the road (ac_csp_trees.py)
    extra = ""
    if cfg["assetto_corsa"].get("csp_trees") and (csp_trees / "extension" / "trees" / "trees.txt").exists():
        shutil.copytree(csp_trees / "extension" / "trees", track_dir / "extension" / "trees")
        extra = (csp_trees / "ext_trees.ini").read_text()
    (track_dir / "extension" / "ext_config.ini").write_text(CSP_CONFIG + extra)
    write_map(track_dir, np.asarray(meta["centreline"]))
    write_ui(track_dir, cfg, meta)

    zpath = OUTPUT / f"{TRACK}_ac.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(track_dir.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(AC_DIR))
    print(f"export_ac: {TRACK}.kn5 {size / 2**20:.0f} MB ({len(meshes)} meshes, {len(mats)} materials, "
          f"{len(textures)} textures, {len(dummies)} markers) -> {zpath} ({zpath.stat().st_size / 2**20:.0f} MB)")


if __name__ == "__main__":
    main()
