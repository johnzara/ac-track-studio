# Assetto Corsa notes for the studio's builds

Track-format conventions the reference pipeline does not cover yet (circuits, pits, surfaces,
layouts, CSP features), researched on 2026-10-13 for this studio. Kunos never published a full
track-format specification: these come from tools that read and write the files (Content Manager's
AcTools, server managers, CSP's bakery), the CSP documentation and community guides. Every claim
carries its evidence level; test anything marked [U] in game before relying on it.

The reference pipeline's own, already proven choices (kn5 writer, fast_lane.ai v7 with points
lifted 0.4 m, exposure, texture formats) are in docs/PIPELINE.md and the reference README.

Research date: 2026-10-13. Target: original Assetto Corsa (2014) + Content Manager (CM) + Custom Shaders Patch (CSP).

**Evidence legend** (used on every claim that matters):

- **[V-src]**: verified by reading source code: CM = `gro-ove/actools`, ASM = `JustaPenguin/assetto-server-manager`, AS = `compujuckel/AssettoServer`, BAKERY = `ac-custom-shaders-patch/acc-bakeryoptix`.
- **[V-doc]**: verified in official CSP documentation (GitHub wiki `ac-custom-shaders-patch/acc-extension-config`, mirrored at `cup.acstuff.club/docs/csp`).
- **[C]**: community source such as a forum moderator, a modding wiki or a server-host wiki. Plausible but not official.
- **[U]**: unverified. My inference or memory, or sources contradict each other. Test it in-game before relying on it.

---

# PART A: Assetto Corsa track conventions for circuits

## A1. Dummy / empty nodes

In a kn5, a "dummy" is a Base node (node class 1, transform only, no geometry) whose name the engine recognises [C: https://site.hagn.io/assettocorsa/modding/tracks/track-kn5]. The engine reads its position and orientation.

| Node name | Purpose | Orientation needed? | Source |
|---|---|---|---|
| `AC_START_n` (n = 0, 1, 2…) | Race grid slot n. In a race, every car needs its own slot. | Yes. Car faces the dummy's local **+Z**, local **+Y** is up. | [C] basic guide (link below), hagn |
| `AC_PIT_n` | Pit box n. Cars spawn here in practice/qualifying/hotlap and return here after "back to pits". | Yes (same convention) | [C], [V-src CM] |
| `AC_HOTLAP_START_0` | Spawn point for Hotlap mode. If missing, the car spawned mid-track in hotlap mode in a GTPlanet report. | Yes | [C] |
| `AC_TIME_0_L` / `AC_TIME_0_R` | Start/finish timing line. The engine draws an imaginary line between the two centres; crossing it counts. | **No** | [C] |
| `AC_TIME_1_L/R`, `AC_TIME_2_L/R` | Intermediate split lines (sectors) | No | [C] |
| `AC_AB_START_L/R`, `AC_AB_FINISH_L/R` | Point-to-point start and finish lines. The guide says "no starting grid" for A-to-B stages. | No | [C] |
| `AC_POBJECT_<suffix>` | Movable physics object, e.g. a cone. Kunos devs reportedly advised "not more than 15". | n/a | [C] guide |
| `AC_AUDIO_<suffix>` | Position of an external track sound, linked by `data/audio_sources.ini` | n/a | [C] guide; file format [U] |
| `AC_CREW…` | **Not found in any source.** Pit crew placement appears to come from the pit box plus `data/crew.ini` (see A4). Do not emit `AC_CREW` nodes. | n/a | [U] |

Basic guide: https://assettocorsamods.net/threads/build-your-first-track-basic-guide.12/

**Orientation, from the guide's own wording:** the pivot's Y axis must point UP and its Z axis must point forward. Place start and pit objects **1–2 m above the track surface**; closer than that "might create problems" [C]. The usual symptom of a wrong pivot is a car spawning with "broken wheels".

In kn5 terms: the dummy's world matrix maps local +Z to the driving direction and local +Y to world up. You already do this for `AC_START_0` and `AC_PIT_0` on point-to-point tracks, so reuse the same matrix code.

CSP adds some spawn handling:
- `[_EXTENSION] ALIGNED_CARS_POSITIONING = 1` in `surfaces.ini` is on by default. It orients cars along the surface normal when positioning them.
- `RANDOMIZED_CARS_POSITIONING = 0.1, 0.1` adds slight randomisation (defaults shown).
- Source: [V-doc] https://github.com/ac-custom-shaders-patch/acc-extension-config/wiki/Tracks-%E2%80%93-General-extended-physics-options

**Timing gates**
- `_L` goes on the left edge of the road and `_R` on the right, relative to the driving direction [C guide]. RacetrackDesign warns that getting the L/R handedness wrong "silently breaks the lap counter" [C: https://racetrackdesign.com/guides/how-to-make-a-track-for-assetto-corsa].
- Put the poles a little beyond the drivable edges, including run-off, so the line spans the full width a car can use. That last point is my recommendation [U].
- The basic guide states **"You can place only 2 intermediates"**. That means `AC_TIME_0` plus `AC_TIME_1` and `AC_TIME_2`, i.e. 3 sectors [C]. Whether AC accepts more is [U]; stay at 3 sectors.
- Whether AC requires all intermediate gates to be crossed in order for a lap to count is [U].

**Naming rules**
- Exact upper case, no zero padding: `AC_PIT_1`, never `AC_PIT_01` [C guide].
- CM counts pits by probing `AC_PIT_0`, `AC_PIT_1`, … and **stops at the first missing index**. Numbering must be contiguous [V-src CM `SelectedTrackPage.xaml.cs: CountPits`].

**Loader pitfalls** [C hagn track-kn5 page]:
- An `AC_*` logical object the engine does not recognise makes it "stop loading" the rest of the track. Do not invent `AC_` names.
- A **mesh** node with children crashes the engine. Only Base (dummy) nodes may have children.
- `AC_PIT_0` is required for the track to load at all [C, moderator on assettocorsamods: https://www.assettocorsamods.net/goto/post?id=3329].

**How many pit boxes vs grid slots**
- Pit box count is the hard capacity of the track:
  - `ui_track.json "pitboxes"` is what CM uses to cap AI opponents and to size server presets [V-src CM: `SpecsPitboxesValue`, used for `MaximumCapacity`].
  - Server `MAX_CLIENTS` "must be <= track's number of pits" [V-src ASM `config_ini.go`].
  - CSP can override a wrong JSON value with `[BASIC] PITBOXES = n` in `ext_config.ini` [V-doc].
- In a race, every car needs its own `AC_START_n` [C hagn]. So **create at least as many `AC_START_n` as `AC_PIT_n`**, with matching indices 0…N‑1.
- If `pitboxes` disagrees with the dummies, you can get errors like "CANNOT HAVE 0 CARS IN RACE.INI" [C guide troubleshooting].
- Grid spacing: put slots too close together and AI cars "slow down heavily just after the start" [C, GTPlanet summary]. Use realistic spacing. The FIA grid geometry is in Part B, and is [U] if not given there.

**Can pit boxes and grid slots be the same spots (street circuit)?**
- `AC_PIT_n` and `AC_START_n` are independent nodes, so nothing in the format stops them sharing coordinates [U, logical inference; no source found].
- What changes is what a pit box *on the racing surface* implies:
  1. If the surface under the boxes is `IS_PITLANE=1`, cars get the forced 80 km/h limiter and lose collisions there (A2). That is wrong for a grid on the main straight. So the grid/pit area must stay a normal `IS_PITLANE=0` road.
  2. Without a pitlane surface, "back to pits" and session restarts put cars on the racing line, and pit stops happen on track. That is acceptable for hotlap and solo use, hazardous online [U].
  3. Drive-through penalties assume a pit lane to drive through. CSP has options around this (`UNFIT_FOR_DRIVETHROUGH_PENALTY`) [V-doc server options].
- **Recommendation for a street circuit:**
  - Model a separate pit road or paddock strip: a side street or parking area beside the main straight, with a `PIT`-type surface (`IS_PITLANE=1`).
  - Put `AC_PIT_n` there and `AC_START_n` on the grid.
  - Add a `pit_lane.ai` that leaves the fast lane before the boxes and rejoins after them.
  - If that is impossible, it is acceptable for pits and grid to coincide on a normal road surface, but expect the caveats above.

## A2. Pit lane, limiter and pit detection

**Pit surface**
- Any mesh whose surface has `IS_PITLANE=1` counts as pit lane.
- A moderator says pit lane speed "is automatically handled by AC (80kmph)" once the surface is correctly marked, and that "you cannot customize pitlane speed" from the track [C: https://assettocorsamods.net/threads/rtb-pitlane-assignment-pit-limiter-etc.1705/].
- Kunos example (Nürburgring Sprint B, see A4): `KEY=PITS`, `FRICTION=0.95`, `IS_VALID_TRACK=1`, `IS_PITLANE=1`.

**Collisions:** AC disables car-to-car collisions in the pits by default. CSP's server option `KEEP_COLLISIONS = 1` exists to "activate collisions between cars in pits" [V-doc]. A GTPlanet modder reported that naming meshes `1PIT…` made "ghosting" apply there [C].

**CSP server-side pit options** (welcome-message INI, needs CSP clients) [V-doc: https://cup.acstuff.club/docs/csp/misc/server-extra-options]:

```ini
[PITS_SPEED_LIMITER]          ; since 0.1.76
DISABLE_FORCED = 1            ; disable the forced limiter
KEEP_COLLISIONS = 1           ; collisions between cars in pits
SPEED_KMH = 80                ; "Alter pits speed limiter value; default is 80"
SPEEDING_PENALTY = DRIVE_THROUGH          ; or TELEPORT_TO_PITS
SPEEDING_PENALTY_LAPS = 3
SPEEDING_SUBSEQUENT_PENALTY = TELEPORT_TO_PITS
SPEEDING_SUBSEQUENT_PENALTY_TIME = 30

[EXTRA_RULES]
INVALIDATE_LAP_TIME_IN_PITS = 1   ; 0.1.78: for tracks whose pitlane surface is NOT marked invalid
UNFIT_FOR_DRIVETHROUGH_PENALTY = X, Y, Z, radius
REQUIRE_NEW_LAP_FOR_DRIVETHROUGH_PENALTY = 1
```

**`ai/pit_lane.ai`**
- Same binary format as `fast_lane.ai` (A7). CM looks for it at `<layoutDir>/ai/pit_lane.ai` [V-src CM `TrackObjectBase.AiLanePitFilename`].
- Recording procedure from the Steam RTB thread: start "just before the entry to the pit lane" on the racing line, drive the pit lane past the boxes, rejoin, and stop just after pit exit. The pit line should "meet the fast line smoothly" [C: https://steamcommunity.com/app/388980/discussions/0/485624149166109832].
- To generate it procedurally: start on the fast-lane centreline about 100–200 m before the pit entry, follow the pit-lane centreline past every box, and merge back onto the fast lane after the exit blend line [U recommendation].

**What happens with no pit lane**
- **[U] (no authoritative source found):**
  - The track still loads, since only `AC_PIT_0` is required.
  - With no `IS_PITLANE` surface there is no limiter and no pit ghosting.
  - With no `pit_lane.ai`, AI cars cannot make pit stops. A Steam thread notes that AI support for "track day" needs both an AI line and a pit line [C, summary].
  - Make sure every `AC_PIT_n` lies on or near the fast lane, so AI spawning in a box can join the spline.
- **[C] fallback:** CM's map renderer and length tools only need `fast_lane.ai`.

## A3. `ui/ui_track.json`

Fields as actually read by CM [V-src CM `AcJsonObjectNew.cs`, `TrackObjectBase.cs`] and ASM [V-src ASM `content_tracks.go`]:

| Field | Type expected | Notes |
|---|---|---|
| `name` | string | Display name, trimmed. |
| `description` | string | CM may extract a version string from it. |
| `tags` | array of strings | Filtering. CM derives `country` from tags if `country` is missing. Common Kunos-style tags are "circuit", "original", "street", "point to point" and the country name [U for exact Kunos tag set]. |
| `geotags` | **array of exactly 2 STRINGS** `["lat","lon"]` | CM rejects numbers: its parser requires `JTokenType.String` [V-src CM `JsonExtension.GetGeoTagsValueOnly`]. It accepts decimal degrees (`"45.6683"`), DMS (`"45°40'06\" N"`), and `S`/`W` letters for negatives [V-src CM `GeoTagsEntry.cs`]. CM passes these to CSP via `race.ini [LIGHTING] __TRACK_GEOTAG_LAT/LONG` and works out the time zone from them [V-src CM `TrackDetails.cs`]. **Wrong or missing geotags mean wrong sun position and time zone under CSP.** |
| `country` | string | |
| `city` | string | |
| `length` | **string** | CM parses `"3450"` as metres; also `"3.45 km"`, `"2.1 mi"`, `"3450m"` [V-src CM regex `SpecsLengthFix`]. CM can recompute it from `fast_lane.ai`. |
| `width` | string | Free text, e.g. `"9"` or `"8-12"`. CM parses a range `a-b` and can recompute it from the AI lane side distances [V-src CM]. |
| `pitboxes` | string (or number) | CM `TryParseInt`, **defaulting to 2 if unparsable** [V-src CM]. Must equal the number of `AC_PIT_n` (CM "recalculate" counts them in the kn5s listed in `models*.ini`). |
| `run` | string | **Free text, display only.** The hagn wiki: shown on mouse hover, "No other change observed" [C]. CM does not parse it; ASM stores it as a plain string [V-src]. Any value is valid. For circuits I suggest `"clockwise"` / `"counter-clockwise"`; the exact strings Kunos used are [U]. |
| `author`, `version`, `url` | string | CM [V-src]. |
| `year` | **integer** | CM `GetIntValueOnly("year")` [V-src]. |
| `priority` | number | CM-only: sort order of layouts in the selector [V-src]. |

Example for a circuit layout:

```json
{
  "name": "Example Street Circuit",
  "description": "Generated by …",
  "tags": ["circuit", "street", "italy"],
  "geotags": ["45.7123", "11.8456"],
  "country": "Italy",
  "city": "…",
  "length": "2860",
  "width": "8-11",
  "pitboxes": "20",
  "run": "clockwise",
  "author": "…",
  "version": "1.0",
  "url": "",
  "year": 2026
}
```

Same folder: `ui/preview.png` (selection image) and `ui/outline.png` (layout outline overlay) [V-src CM `InitializeLocationsInner`]. For layouts, put them in `ui/<layout>/` (A5).

## A4. `data/` folder

### A4.1 `surfaces.ini`: keys

Each surface is a `[SURFACE_n]` section numbered from 0. Number them contiguously, and "increment the surface number from the current last surface" [C emperorservers].

| Key | Meaning | Source |
|---|---|---|
| `KEY` | Surface name, matched against mesh names (see naming rules below). | [C] |
| `FRICTION` | Grip multiplier: 1.0 = 100 %, 0.9 = 90 %. | [C] |
| `DAMPING` | Speed-scrubbing drag on the car, the "slow-down factor". Gravel traps use about 0.1. | [C] |
| `WAV`, `WAV_PITCH` | Rolling sound (`kerb.wav`, `grass.wav`, `sand.wav`, `extraturf.wav`) and its pitch scale. CSP also reads `WAV` to guess the surface type. | [C], [V-doc] |
| `FF_EFFECT` | Force-feedback effect id. Kunos uses `NULL` for none and `1` on kerbs. | [C]; meaning of other ids [U] |
| `DIRT_ADDITIVE` | How much dirt the tyres pick up: 1 = grass/sand. CSP SurfacesFX auto-activates at ≥0.7, or ≥0.3 when combined with low friction or a grass/gravel/sand wav. | [C], [V-doc] |
| `IS_VALID_TRACK` | 1 = valid, 0 = off-track. Feeds cut detection (A6). | [C] |
| `BLACK_FLAG_TIME` | Hagn lists it as "unknown". **In practice, invalid surfaces need a value > 0 (Emperor uses 5) or cuts are NOT reported to the server in race sessions** (practice/qualifying still report them). | [C emperorservers] |
| `SIN_HEIGHT` | Amplitude in metres of a procedural sine bump on the surface. Physics, not just FFB. | [C] |
| `SIN_LENGTH` | Spatial frequency of that sine: 1 → 1 m wavelength, 2 → 0.5 m, 0.5 → 2 m. Despite its name, it is cycles per metre. | [C hagn] |
| `IS_PITLANE` | 1 = pit lane: forced limiter and no collisions. | [C] |
| `VIBRATION_GAIN`, `VIBRATION_LENGTH` | Rumble/FFB vibration strength and length. Kunos kerbs use 1.0/1.0; Kunos grass 0.2/0.6. | [C], exact semantics [U] |

**CSP extensions** in the same `[SURFACE_n]` sections [V-doc https://cup.acstuff.club/docs/csp/tracks/surface-tweaks]:
- `_EXT_SURFACE_TYPE` = `GRASS`/`GRAVEL`/`SAND`/`KERB`/`EXTRATURF`/`OLD` (wiki also lists ICE/SNOW)
- `_EXT_SURFACE_TYPE_MODIFIER` = `LOOSE`/`REGULAR`/`FIRM`
- `_EXT_PERLIN_NOISE` = 0/1. With 1, the sine bump becomes Perlin noise, still scaled by `SIN_HEIGHT`/`SIN_LENGTH`.
- `_EXT_PERLIN_OCTAVES` = 1–10
- `_EXT_PERLIN_PERSISTENCE`

Whether these need "extended physics" enabled is [U].

Also valid in `surfaces.ini` without enabling extended physics. **Put these in every generated track:**

```ini
[WEATHER_FX]
LATITUDE = 45.7123        ; decimal degrees (number)
LONGITUDE = 11.8456
TIMEZONE_ID = Europe/Rome ; IANA tz name; DST handled automatically
```

[V-doc https://github.com/ac-custom-shaders-patch/acc-extension-config/wiki/Tracks-%E2%80%93-General-extended-physics-options]. This overrides any other CSP config for the track and keeps time-of-day in sync online.

**Extended physics warning.** Setting `WAV_PITCH=extended-0` in `[SURFACE_0]` turns on CSP extended track physics. It **intentionally crashes vanilla AC**, so the track becomes CSP-only. Do not set it unless you need those features [V-doc Tracks – Enabling extended physics].

**Online integrity.** Servers checksum `system/data/surfaces.ini`, the track's `data/surfaces.ini` (or `<layout>/data/surfaces.ini`), `models.ini`/`models_<layout>.ini`, and every `.kn5` in the track root [V-src AS `ChecksumManager.cs`]. Regenerating a kn5 means redistributing it to every client.

### A4.2 How mesh names map to surfaces

- **Syntax:** `<digits><KEY><anything>`, for example `1ROAD`, `1ROAD_003`, `2KERB_T1`, `1GRASS_inner_04`.
  - A leading number greater than 0 makes the mesh a physics object.
  - No number, or 0, means visual only [C guide; hagn].
  - The number "need not be unique" and its meaning is unclear [C hagn]. Kunos and community use 1, 2, 30…
- **Matching is by substring of the KEY. Exactly one key may match.**
  - The log error `Mesh 02KERBHIGH03 CAN BE: KERB KERBHIGH` shows that a key which is a substring of another key, or a mesh name containing two keys, is ambiguous [C: https://assettocorsamods.net/threads/how-to-use-surfaces-in-surfaces-ini.1683/].
  - Example from another thread: `1MAIN_ROAD_BRIDGE` matched both `MAIN` and `ROAD` [C: https://assettocorsamods.net/threads/proper-key-for-surface-ini.3757/].
  - **Rule for the generator:** choose KEYs that are never substrings of one another (e.g. `ROAD`, `KERBFLAT`, `KERBSAUS`, not `KERB` + `KERBHIGH`). Never put free text in physics mesh names that might contain another key, such as `ROAD` inside `RAILROAD_GRASS`.
- **`WALL`** anywhere in a physics mesh name makes it a hard collider (guard rails, walls, buildings). The guide recommends `1WALL…` [C guide; hagn].
- **System defaults:** `system/data/surfaces.ini` predefines `ROAD`, `GRASS`, `KERB`, `SAND`, plus the special `WALL` [C hagn].
  - **Contradiction:** a moderator says `1GRASS…` meshes "need no entry" in the track file. Emperor Servers found that `GRASS` "is missing completely from the default surfaces.ini" for race-session cut reporting [C both].
  - **Recommendation:** define every KEY you use, including ROAD/KERB/GRASS/SAND, in the track's own `data/surfaces.ini`. A track entry with the same KEY overrides the system one (a custom SAND changed behaviour in the first thread above [C]).
- **Visual vs physics:** a common pattern is a render mesh named without a digit (e.g. `road_vis_003`) plus a separate physics mesh `1ROAD_003` with the kn5 `isRenderable=false` or `isVisible=false` flag [U]. CM's map generator by default draws meshes whose name has `ROAD` at character index 1, i.e. `1ROAD…` [V-src CM `TrackMapRenderer.UpdateFiltered`]. A two-digit prefix such as `12ROAD` would not be picked up by CM's map tool.

### A4.3 Real Kunos values (Nürburgring Sprint B `surfaces.ini`)

Reproduced on Emperor Servers, with only `BLACK_FLAG_TIME` changed to 5 on invalid surfaces and GRASS/SAND added: https://wiki.emperorservers.com/assetto-corsa-server-manager/fixing-track-cut-detection-in-race-sessions [C].

| KEY | FRICTION | DAMPING | WAV / PITCH | FF | DIRT | VALID | BLACK_FLAG | SIN_H / SIN_L | PIT | VIB G / L |
|---|---|---|---|---|---|---|---|---|---|---|
| ASPH-NURB (main asphalt) | 0.99 | 0 | – / 0 | NULL | 0 | 1 | 0 | 0 / 0 | 0 | 0 / 0 |
| CONCRETE | 0.85 | 0 | – | NULL | 0 | 1 | 0 | 0 / 0 | 0 | 0 / 0 |
| PAINT (lines) | 0.92 | 0 | – | NULL | 0 | 1 | 0 | 0 / 0 | 0 | 0 / 0 |
| PITS | 0.95 | 0 | – | NULL | 0.05 | 1 | 0 | 0 / 0 | **1** | 0 / 0 |
| CURB (valid kerb) | 0.85 | 0 | kerb.wav / 1.3 | 1 | 0 | 1 | 0 | 0 / 0 | 0 | 1.0 / 1.0 |
| CUTKRB (invalid kerb) | 0.95 | 0.01 | kerb.wav / 1.2 | 1 | 0 | **0** | 5 | 0 / 0 | 0 | 1.0 / 1.0 |
| OUT (invalid paved run-off) | 0.95 | 0.012 | – | NULL | 0 | 0 | 5 | 0 / 0 | 0 | 0 / 0 |
| CARPET (astroturf) | 0.75 | 0 | extraturf.wav / 0 | NULL | 0.1 | 1 | 0 | 0 / 0 | 0 | 0 / 0 |
| GRASS / GRS-CUT-B | 0.60 | 0 | grass.wav / 0 | NULL | 1 | 0 | 5 | 0.03 / 0.5 | 0 | 0.2 / 0.6 |
| SAND (gravel trap) | 0.80 | 0.10 | sand.wav / 0 | 0 | 1 | 0 | 5 | 0.04 / 0.5 | 0 | 0.2 / 0.3 |

Other community values [C]:
- Hagn's KERB example: 0.92 friction, kerb.wav, pitch 1.3, FF 1, VIB 0.1/0.2.
- A "GRAVEL" (0.9 friction / 0.02 damping / sand.wav / SIN 0.01/0.5) and a "TRAP" (0.9 / 0.1 / SIN 0.05/0.7) with `DIRT_ADDITIVE=1`.

**Recommended starting set for the generator** [derived from the above; tune in-game]:

```ini
[SURFACE_0]   ; main asphalt
KEY=ROAD
FRICTION=0.99
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
; KERBFLAT (valid flat/rumble kerb): FRICTION=0.88, WAV=kerb.wav, WAV_PITCH=1.3, FF_EFFECT=1,
;   IS_VALID_TRACK=1, SIN_HEIGHT=0.005..0.01 with SIN_LENGTH≈1/(block pitch) for a rumble-strip feel [U],
;   VIBRATION_GAIN=1.0, VIBRATION_LENGTH=1.0, _EXT_SURFACE_TYPE=KERB
; KERBSAUS (sausage / outer invalid kerb): IS_VALID_TRACK=0, BLACK_FLAG_TIME=5, otherwise as kerb.
;   Model a real ~100-150 mm hump instead of relying on SIN_HEIGHT.
; GRASS: 0.60 / 0 / grass.wav / DIRT 1 / VALID 0 / BLACK_FLAG 5 / SIN 0.03 0.5 / VIB 0.2 0.6, _EXT_SURFACE_TYPE=GRASS
; GRAVEL trap: 0.80 / DAMPING 0.10 / sand.wav / DIRT 1 / VALID 0 / BLACK_FLAG 5 / SIN 0.04 0.5 / VIB 0.2 0.3, _EXT_SURFACE_TYPE=GRAVEL
; PIT: 0.95 / 0 / DIRT 0.05 / VALID 1 / IS_PITLANE=1
;   (set VALID 0 + BLACK_FLAG 5 instead only if the pit lane is a shortcut; otherwise rely on CSP INVALIDATE_LAP_TIME_IN_PITS)
; SIDEWALK / CURBSTONE (street circuit): treat as KERB-like, VALID 0, BLACK_FLAG 5
```

### A4.4 Other `data/` files: required vs optional

| File | Status | Format notes |
|---|---|---|
| `surfaces.ini` | **Required.** ASM only treats a layout folder as valid if `<layout>/data/surfaces.ini` exists [V-src ASM]. Servers checksum it [V-src AS]. | See above. |
| `cameras.ini` (+ `cameras_1.ini`, …) | Recommended (replay/TV cameras; auto-switching needs the AI line [C guide]). | You already write it. |
| `map.ini` + layout-root `map.png` | Recommended (in-game track map). The map image is `<layoutDir>/map.png`, **not** inside `data/` [V-src CM `MapImage`]. | `[PARAMETERS]` `WIDTH`, `HEIGHT` (image px including margins), `MARGIN`, `SCALE_FACTOR` (= metres per pixel, written by CM as 1/scale), `X_OFFSET = -minX + MARGIN/scale`, `Z_OFFSET = -minZ + MARGIN/scale` (world metres), `DRAWING_SIZE` (10), `MAX_SIZE` (1600 per hagn). So `pixel = (world + OFFSET) / SCALE_FACTOR` [V-src CM `TrackMapRenderer.Prepare`; hagn]. CM can generate both ("Update map"). |
| `lighting.ini` | Optional; vanilla sun direction. | `[LIGHTING] SUN_PITCH_ANGLE=…`, `SUN_HEADING_ANGLE=…` (heading is counter-clockwise rotation) [C hagn; keys V-src CM]. With CSP/WeatherFX the sun comes from geotags/`[WEATHER_FX]`, date and time. |
| `drs_zones.ini` | Optional (DRS cars only). Servers keep it [V-src CM `TrackDataToKeep`]. | `[ZONE_n] DETECTION=, START=, END=` as **normalised fast-lane positions 0..1** [V-src CM `TrackMapRenderer` reads these keys and plots them on the AI spline]. A zone may wrap across 0 [C]. |
| `sections.ini` | Optional; corner/section names shown in-game. | `[SECTION_n] IN=, OUT=` (normalised spline positions) [V-src CM]. Name key `TEXT=` [U]. |
| `crew.ini` | Optional. | Guide: pit crew side, left (1) or right (−1) of the car [C]; exact keys [U]. |
| `audio_sources.ini` | Optional. | Links `AC_AUDIO_*` dummies to sounds [C]; format [U]. CSP's FMOD alternative is `extension/sfx/*.bank` + `[SOUNDBANK_0]`/`[EVENT_n]` in `ext_config.ini` [V-doc Tracks – Audio]. |
| `groove.ini` | Optional. | Rubber "groove" (skid-wear build-up), "not the racing line" [C, GTPlanet summary]; format [U]. Skip it. |

The basic guide also says a missing `ai/` folder is fine for loading, but then "AI will NOT work" [C]. Folder and file names must be lowercase latin/digits/underscore. Track folder ≤ 32 chars, layout folder ≤ 15 chars [C guide]. The main kn5 must be named after the track folder (single-layout tracks) [C].

## A5. Multiple layouts in one track folder

Verified against CM's layout discovery [V-src CM `TrackObject.GetLayouts`, `TrackObjectBase.ModelsFilename`/`LayoutDataDirectory`], ASM [V-src] and AssettoServer [V-src]:

```
content/tracks/<track_id>/
├── models_<layout>.ini            ; one per layout; lists the kn5s to load
├── <anything>.kn5                 ; shared + layout-specific kn5s (all in the root)
├── <layout>/                      ; per-layout "LayoutDataDirectory"
│   ├── data/surfaces.ini, cameras.ini, map.ini, …   (surfaces.ini makes the layout valid)
│   ├── ai/fast_lane.ai, ai/pit_lane.ai
│   └── map.png
├── ui/
│   ├── <layout>/ui_track.json, preview.png, outline.png
│   └── (ui_track.json here ONLY if there is also a "default" root layout)
├── extension/ext_config.ini       ; CSP config shipped with the track
└── skins/…                        ; CM track skins (optional)
```

- **Single-layout track:** `<track_id>.kn5` (or a `models.ini`), `data/`, `ai/`, `map.png`, `ui/ui_track.json` all at the root.
- **Multi-layout:** CM collects `ui/*/ui_track.json`.
  - If `ui/ui_track.json` also exists, the root is an extra "main" layout whose data lives in the root `data/`. Otherwise every layout is a subfolder.
  - The layout id is the folder name, and `race.ini [RACE] CONFIG_TRACK=<layout>` selects it.
- **`models_<layout>.ini`:** consecutive `[MODEL_0]`, `[MODEL_1]` … sections, each with `FILE=<file>.kn5` relative to the track root. Iteration stops at the first missing index [V-src BAKERY `iterate_break("MODEL")`, CM].
  - Kunos files also carry `POSITION=0,0,0` and `ROTATION=0,0,0` [U: whether non-zero values are honoured]. Keep them at zero and bake transforms into the kn5.
  - Using it, put shared scenery in one kn5 and layout-specific pieces (start-line gantry, layout-specific walls, `AC_*` dummies) in small per-layout kn5s.
  - CM's pitbox recount sums `AC_PIT_n` over all kn5s listed in the models ini [V-src CM].
- **CSP per-layout config:**
  - Downloaded configs are named `<trackid>__<layoutid>.ini`, falling back to `<trackid>.ini` [V-src CM `PatchTracksDataUpdater.cs`, per the CSP research agent].
  - VAO patches follow `<kn5 or models_x>.vao-patch`.
  - A per-layout `<track>/<layout>/extension/ext_config.ini` is reported by users but not documented [U].

## A6. Track cuts / track limits

**Vanilla mechanism** [C, plus V-src for the server keys]:
- Each wheel's contact surface carries `IS_VALID_TRACK`.
- A wheel on a surface with 0 is "outside". A cut is registered when **more than the allowed number of wheels** are outside, and the lap is invalidated.
- Server `server_cfg.ini [SERVER] ALLOWED_TYRES_OUT` sets the allowance; -1 disables it [C hosting guides; key exists in AS `ServerConfiguration.cs` and ASM, ASM default value 3].
  - Offline, a 2021 app author reports that `race.ini`'s value is ignored and "AC will always use 2 tyres" [C: https://assettocorsamods.net/posts/24003].
  - Practical meaning: 2 wheels may be off, and the 3rd wheel off is a cut.
- Server `RACE_GAS_PENALTY_DISABLED`: "0 = any cut will be penalized with the gas cut message; 1 = no penalization… cuts will be saved in the race result json" [V-src ASM help text].
- **Race sessions online need `BLACK_FLAG_TIME > 0`** on invalid surfaces for cuts to register [C emperorservers]. So the generator should write `BLACK_FLAG_TIME=5` on every `IS_VALID_TRACK=0` surface.

**Consequences for geometry**
- Mark kerbs that are "part of the track" valid (`IS_VALID_TRACK=1`). Make everything beyond them invalid: grass, gravel, paved run-off (Kunos `OUT`), outer/sausage kerbs (Kunos `CUTKRB`).
- With the 2-wheels rule, a car with two wheels on a valid kerb may be entirely past the white line and still be legal [C overtake summary].
- For stricter white-line limits:
  - make only the asphalt up to the white line plus the inner part of the kerb valid;
  - split the kerb into a valid inner strip and an invalid outer strip (two meshes, two KEYs);
  - or use an invalid paved `OUT` surface right behind the line.

**CSP**
- No `[TRACK_LIMITS]` section exists in CSP docs; validity stays with `IS_VALID_TRACK` [V-doc absence, CSP agent].
- CSP exposes per-wheel `isValidTrack` / `isPitlane` to Lua [V-src acc-lua-sdk `ac_car_cphys.lua`] and `wheelsOutside` to Lua modes (used in CSP's overtake mode).
- Server-side options: `INVALIDATE_LAP_TIME_IN_PITS`, wrong-way rules (`ALLOW_WRONG_WAY`, `MAX_METERS_WRONG_WAY`), teleport invalidation [V-doc].
- Stricter limits in leagues are usually enforced by third-party apps or plugins (Real Penalty, Kissmyrank, InvalidateLap app) [C].

## A7. `fast_lane.ai`: closed loop vs point-to-point; use in timing

- **Format** [V-src CM `AiSpline.cs`]:
  - Header: `int32 version (=7)`, `int32 pointCount`, `int32 lapTime`, `int32 sampleCount`.
  - Per point: `vec3 pos, float length, int32 id`.
  - Then `int32 extraCount` and per point `speed, gas, brake, obsoleteLatG, radius, sideLeft, sideRight, camber, direction, vec3 normal, length, vec3 forward, tag, grade` (floats).
  - Then `int32 hasGrid` plus an optional grid.
- **There is no "closed loop" or "point-to-point" flag in the file** [V-src]. Third-party readers infer it from geometry: AssettoServer closes the loop when the first and last points are less than 50 m apart, otherwise it treats the spline as open [V-src AS `FastLaneParser.cs`: `closedLoop = Distance(points[0], points[^1]) < 50`].
  - Point-to-point vs circuit in AC itself is decided by the presence of `AC_AB_*` vs `AC_TIME_0_*` dummies [C guide; U as to the exact engine rule].
- **For a circuit:**
  - Start the spline at (or just after) the `AC_TIME_0` line, going in race direction. End it just before the line, with roughly equal spacing to the first point; the official recorder records exactly one lap from the line [C].
  - Do **not** duplicate the first point at the end. Leave the closing gap at about the normal point spacing (~1 m in Kunos files [U]).
- **What the spline is used for:**
  - AI driving (speed, gas/brake hints, sides).
  - Normalised lap position (0..1), used for race positions and leaderboard ordering, DRS zones and sections (`drs_zones.ini`/`sections.ini` are expressed in it [V-src CM]), auto TV-camera switching [C], and CSP Lua's spline position.
  - CM's length and width recalculation [V-src].
- **Lap timing does not come from the spline.** Laps and sectors are counted when crossing the `AC_TIME_n_L/R` lines [C guide]. Whether AC also checks spline progress to reject laps (e.g. reversing over the line) is [U].
- `sideLeft`/`sideRight` in the extra block are the drivable half-widths used by AI and by CM's width estimate [V-src CM `AiPointExtra.Width => SideLeft + SideRight`]. Fill them from the generator's actual road edges. Include valid kerb width; exclude grass.

## A8. kn5 LODs, mesh and texture limits, performance, CSP features

### kn5 mesh fields [V-src CM `Kn5Writer.cs`, BAKERY `load_util.cpp`]

Mesh node fields, in order: `castShadows, isVisible, isTransparent`, vertices (`pos3f, normal3f, uv2f, tangent3f`), **`ushort` indices**, `materialId`, `layer`, **`lodIn` (float)**, **`lodOut` (float)**, bounding sphere centre and radius, `isRenderable`.

- **LOD semantics:**
  - Distances are in metres. The mesh draws when `lodIn ≤ distance(camera, mesh centre) ≤ lodOut`. `lodIn = lodOut = 0` means always drawn [V-src CM renderer: it measures to the bounding-box centre].
  - **Set both values explicitly.** In CM's implementation, `lodIn>0, lodOut=0` would never draw. AC's handling of `lodOut=0` with `lodIn>0` is [U].
  - Use `lodOut` (e.g. 150–400 m) on small props, poles, signs and kerb detail. Use `lodIn`/`lodOut` pairs to swap hi/lo versions of big objects.
  - CSP's VAO bakery treats meshes with `lodIn > 0` as non-shadow-casting [V-src BAKERY]. CSP shader replacements can also set `LOD_IN`/`LOD_OUT` per mesh [V-doc].
- **Vertex limit:** indices are 16-bit, so **≤ 65,536 vertices per mesh** [V-src]. The ksEditor warns at the limit [C moderator: "the limit for a single mesh is 65k"]. Split long roads into chunks of, say, 100–200 m. That also helps culling, which is per mesh.

### Textures

- AC uses DDS embedded in the kn5.
  - Vanilla-safe: **DXT1 (BC1)** for opaque and **DXT5 (BC3)** for alpha, plus uncompressed RGBA8888 (what ksEditor saves).
  - **BC7 is NOT readable by original AC or ksEditor, only with CSP** [V-src CM `Bc7Upgrader.xaml`: "Original Assetto Corsa (or ksEditor) can't read this format, but Custom Shaders Patch adds support for it"].
  - BCn textures need **dimensions divisible by 4**, ideally powers of two, with full mip chains. AC/CSP generate missing mips at load [V-src CM same file].
  - Note that the CM text calls DXT5 "aka BC5". That is wrong: DXT5 = BC3.
- No AC-specific maximum texture size was found. The D3D11 limit is 16384 px [V-doc Microsoft]. Keep track textures ≤ 4096.

### Big-track performance tips [U unless marked; standard practice]

1. Chunk the world spatially, by road segment or terrain tiles about 100–250 m. Merge everything that shares a material inside a chunk, which cuts draw calls without breaking frustum culling.
2. Turn `castShadows` off for terrain far from the road, grass/ground clutter, decals, lines and small props.
3. Use separate low-density invisible physics meshes for walls and terrain. Keep road physics meshes dense enough for smooth contact. Kunos roads are dense laser-scan meshes [U on exact density]; aim for ≤ 1–2 m spacing along and across in curves.
4. Use `lodOut` on all clutter. Prefer CSP trees (below) over thousands of individual tree meshes.
5. CSP `[KN5] DISALLOW_MESH_OPTIMIZATIONS` exists, which implies CSP already merges or optimises meshes at load [V-doc General options]. Add a VAO patch: "CSP is described as expecting VAO" for tracks [V-doc Vertex AO].

### CSP features worth generating (track-local `extension/ext_config.ini`)

All [V-doc] unless marked.

**Location:** `content/tracks/<track>/extension/ext_config.ini`. Relative paths (trees, textures) resolve from `extension/`. Include common libraries with `[INCLUDE] INCLUDE = common/conditions.ini, common/materials_track.ini`.

**GrassFX**: https://github.com/ac-custom-shaders-patch/acc-extension-config/wiki/Tracks-%E2%80%93-Grass-FX

```ini
[GRASS_FX]
GRASS_MATERIALS = grass_mat, verge_mat
OCCLUDING_MATERIALS = road_mat, kerb_mat, line_mat
OCCLUDING_MATERIALS_ALPHA = blend_mat
ORIGINAL_GRASS_MATERIALS =          ; empty keeps original ksGrass meshes
MASK_MAIN_THRESHOLD = -1            ; these four = spawn regardless of texture colour
MASK_RED_THRESHOLD = 0
MASK_MIN_LUMINANCE = -1
MASK_MAX_LUMINANCE = 1
SHAPE_SIZE = 1.0
TRIM_PERIOD = 365
```

Further knobs: `[GRASS_FX_CONFIGURATION_A..D]`, `[GRASS_FX_ADJUSTMENT_...]` with local `EXTRA_n_*` areas, and `[GRASS_FX_TEXTURE_GROUP_0..3]`.

**RainFX**: https://github.com/ac-custom-shaders-patch/acc-extension-config/wiki/Tracks-%E2%80%93-RainFX

```ini
[RAIN_FX]
PUDDLES_MATERIALS = road_mat, kerb_mat, line_mat   ; drivable, gets puddles + wet physics
SOAKING_MATERIALS = road_mat                        ; darkens strongly when wet
SMOOTH_MATERIALS = barrier_metal, glass             ; drops, no darkening
ROUGH_MATERIALS = grass_mat, gravel_mat, verge_mat  ; no wet reflections
LINES_MATERIALS = line_mat                          ; painted lines (slippier)
```

Each `*_MATERIALS` key has a `*_MESHES` twin. Rain occluders: `[TRACK_OCCLUDER_BOX_n]`, `[TRACK_OCCLUDER_WALL_n]`.

**Trees (CSP ≥ 0.1.79): real 3D, instanced, multi-LOD models with automatic distant billboards. Not 2D-only.**
- Source: https://github.com/ac-custom-shaders-patch/acc-extension-config/wiki/Tracks-%E2%80%93-Trees. The page says it aims at "hundreds of thousands of trees" through "LODs, billboards for distant trees and heavy use of instancing" [V-doc, read directly].
- **Model requirements:**
  - One mesh per LOD; suggested ~10k / 6k / 3k triangles.
  - Pivot at the middle of the base.
  - Diffuse alpha is the cutout; normal-map alpha is local AO.
- Convert the FBX to a `.bin` with CM: Content → Tools → Creator tools → tree converter.

```ini
[TREES]
LIST_0 = trees/trees.txt            ; relative to extension/
SURFACE_MATERIALS = terrain_mat     ; snap trees to ground + align lighting (also SURFACE_MESHES)
```

```
configure: size variance = 0.8, 1.2
configure: angle variance = 0, 360
tree: oak.bin; 208.4, -1, -53
tree: pine.bin; pos=-83.3, -0.99, -74.14; angle = 180; size = 1; width = 1; color = 2, 2, 2
```

- Other `configure:` keys: `width variance`, `color variance`, `brightness variance`, `fake shadow`, `seed`. Randomness is seeded deterministically.
- **Writing `.bin` tree models directly from Python** (format read by the CSP agent from CM's converter source `AcTools.ExtraKn5Utils/Helpers/AcTreeModelConverter.cs`, https://raw.githubusercontent.com/gro-ove/actools/master/AcTools.ExtraKn5Utils/Helpers/AcTreeModelConverter.cs) [V-src, second-hand]:
  - The `.bin` is a ZIP holding `tree.ini` (`[BASIC] HEIGHT, WIDTH`, `[SHADING]`, `[LOD_n] MODEL=<x>.mesh, DISTANCE`, …), one `.mesh` per LOD, and the textures.
  - `.mesh` stores ushort indices and per-vertex pos/normal/uv/packed tangent/UV2/AO bytes.
  - Positions are normalised: x and z by half-width, y by height.
  - The simpler route is to generate FBX LODs and convert them once in CM.
- **Arbitrary instancing:**
  - No generic CSP "instance this kn5 mesh N times" section was found [U: none documented]. The trees system accepts any geometry, so bushes and shrubs work too, though they won't deform.
  - `[MODEL_REPLACEMENT_...]` can insert extra kn5 files (`INSERT`, `INSERT_AFTER`/`INSERT_IN`, `OFFSET`, `ROTATION`, `SCALE`) [V-doc https://github.com/ac-custom-shaders-patch/acc-extension-config/wiki/General-%E2%80%93-Model-replacements]. It is **not** an instancing system:
    - `MULTIPLE=1` only allows insertion into every matching node.
    - It does not work for names that start with `AC_` or a digit, or that contain `WALL`. That rule is anti-cheat, so **CSP-inserted geometry can never carry physics**.
    - The doc reminds you to set LOD distances on inserted models.
  - For lamp posts, guard-rail posts and similar repeats, the reliable path is still to bake merged meshes per chunk into the kn5.

**Other useful CSP sections**
- `[BASIC] PITBOXES`, `SUPPORTS_WIND`
- `[LIGHTING] ENABLE_TREES_LIGHTING` / `BOUNCED_LIGHT_MULT`
- `[CONDITION_...]` with `NAME`, `INPUT=SUN|TIME|…`, `LUT`, plus the common `NIGHT_SMOOTH` etc.
- `[LIGHT_...]` / `[LIGHT_SERIES_...]` for street lights (`MESHES`/`MATERIALS` + `DIRECTION=NORMAL`, `COLOR`, `SPOT`, `RANGE`, `CONDITION`)
- `[MATERIAL_ADJUSTMENT_...]` for night emissives on lamp glass
- `[SHADER_REPLACEMENT_...]`:
  - `SHADER = ksPerPixel_tilingfix` breaks texture tiling on big terrain.
  - `ksPerPixel_ppfog` gives per-pixel fog on large low-poly meshes.
- `[ALIGN_TREES_...]` for old-style `ksTree` billboards
- VAO (per-vertex AO, "zero FPS cost"): drag `models_<layout>.ini` or the kn5 onto CSP's bakery exe to produce `<name>.vao-patch` next to it. Running it as a post-export step is the easiest way to get AO on a generated track.
  - The bakery can alternatively write AO into the kn5 itself, in the tangent data. Patches survive texture and material edits, which makes them better [V-doc https://github.com/ac-custom-shaders-patch/acc-extension-config/wiki/Tracks-%E2%80%93-Vertex-AO; V-src CM `CustomTracksBakery/MainBakery.cs`].
  - Bakery settings go in a `baked shadows params.txt` next to the kn5s.

---

# PART B: Real-world dimensions for roadside furniture

Values in this part come from documents the research agents opened directly, unless marked. The markers mean:
- **[snippet]**: only a search-result summary was seen.
- **[derived]**: arithmetic from sourced numbers.
- **[U]**: no source found.

## B1. W-beam (2-wave) steel guardrail

### US / AASHTO M180 profile

Source: FDOT Standard Plans Index 536-001, sheet 4, drawn from AASHTO TF13. https://fdotwww.blob.core.windows.net/sitefinity/docs/default-source/design/standardplans/2025/idx/536-001.pdf?sfvrsn=8ee2b6f5_1

| Item | Value |
|---|---|
| Overall height | 12¼ in = **311 mm** |
| Depth | 3¼ in = **83 mm** (mid-depth line 41 mm from the back) |
| Crest centres | 59 mm below the top edge. The panel centreline is 156 mm below the top, so the crests sit **±97 mm** from the centreline [derived] |
| Valley | Central back flat, where the post bolt goes |
| Web angle | **35°** to horizontal |
| Edge lips | **10°**, 26 mm horizontal projection |
| Bend radii | 24 mm at crests and valleys, 9.5 mm at the lip |
| Post bolt slot | 19 × 64 mm. Post slots repeat every 952.5 mm |
| Splice | 2 columns × 4 slots of 23 × 29 mm. Lap 12.5 in = **318 mm** [derived] |
| Effective (lay) lengths | 1905 / 2858 / **3810** / 4763 / **7620** mm |
| Thickness | Class A 12 ga = **2.67 mm** (0.105 in); Class B 10 ga = **3.43 mm** [snippet: Indiana, Iowa, Arizona]. Ohio gives Class A 0.111 in = 2.82 mm: https://www.dot.state.oh.us/Divisions/ConstructionMgt/Materials/Certification%20Training/DeepBeamRail.PDF. Flat sheet before rolling: 19 in for W-beam, 29½ in for thrie. |

**Cross-check, HIASA (Spain) AASHTO-type card B-03.** https://construnews.com/archivos/27606/barrera_metalica_segun_norma_aashto.pdf
- Profile: 310 × 81 mm, R24 bends, lip 27 mm at 10°, crest centre 96 mm from the centreline.
- Thickness 3 or 2.67 mm.
- Panel **4128 mm overall / 3810 mm effective**. Hole pitch 108 mm.
- Posts C-150 (150×75×5 mm, L 1820 mm) or UPN/IPE-140, at **1905 mm**.
- Spacer C-150, 350 mm long.
- **Top of beam 700 +50/−0 mm.**

**US mounting heights and posts**
- **MGS (Midwest Guardrail System):**
  - **787 mm (31 in) to the top of the rail.**
  - **305 mm (12 in) blockouts.**
  - Posts at **1905 mm (6'-3")**, reducible to 953 or 476 mm.
  - Source: https://epg.modot.org/index.php/606.1_Guardrail
- **Older NCHRP 350 G4(1S):** **27¾ in = 705 mm** minimum, 8 in = 203 mm blockouts (MoDOT).
- **Posts:** W6x8.5 or W6x9 steel, or 6x8 in timber, with 6x8 wood blockouts (Oregon RD404 https://www.oregon.gov/ODOT/Engineering/202601/RD404.pdf).
- **FDOT:** posts 1981 mm long, embedded at least 1168 mm. W-beam centreline at 635 mm (2'-1").
- **MGS splices and embedment [snippet]:** splices at mid-span, post embedment 1016 mm.

### European practice

EN 1317 is performance-based and does **not** define a profile. These are manufacturer and authority drawings.

| System | Beam (h × d × t) | Beam length (overall / effective) | Beam top above ground | Post | Post spacing | Spacer | Source |
|---|---|---|---|---|---|---|---|
| Marcegaglia H1-W3 bordo laterale, 2 waves (Italy) | 310 mm × (overall width 205 mm) × **2 mm**, S355JR | 4316 / **4000** (316 lap) | **770 mm**. Bolt at beam centre 615 mm; post top 720 mm | C 120×55×30×4, L 1700, driven 980 | **2000 mm** | **None**: beam bolted directly to the post | https://www.hellopro.fr//documentation/pdf_prod/7/7/8/2497287_a60ce0d10d09077b69bec015cb4fc1e7_6585877.pdf |
| Marcegaglia H2-W1, 2 waves | 310 × **3.5 mm**, S500MC | 4816 / 4500 | **805 mm**. Bolt at 650; post top 760 | C 120×80×30×6, L 1650, driven 890 | **1500 mm** | None (M16×160 through-bolt) | https://www.marcegagliabuildtech.it/wp-content/uploads/2025/11/Marcegaglia-Buildtech-guardrail-BL-H2-W1-2waves.pdf |
| Marcegaglia N2-W4 [snippet] | 2 mm | 4316 | **700 ± 30 mm** | embedment 1050 | 4000 mm | – | https://pdf.archiexpo.com/pdf/marcegaglia/class-n2-roadside-2-waves-w4/3929-266042.html |
| Marcegaglia H2-W5, 3 waves + rope [snippet] | thrie + Ø20 mm wire rope | – | 940 mm (920 on shoulder) | C 120×80×30×5, L 2250 | 2250 mm | – | https://buildtech.studiochiesa.it/wp-content/uploads/2024/09/Marcegaglia-Buildtech-guardrail-BL-H2-W5-3-waves-with-wire-rope.pdf |
| Swiss "Profil A" (Graubünden 22a11) | **306 × 88 mm** | 4000 | **750 ± 25 mm** | IPE 100 | **1333 mm** | **Round tube Ø168.3×4.5** | https://gr.ch/DE/institutionen/verwaltung/diem/tba/dokumentation/DokumentationPlan/201ta2012-013.pdf |
| Germany "Schutzplanke" (Wikipedia) | 3 mm typical | 4 m rails | (≈0.75 m [U]) | 55×100: IPE-100 or **SIGMA-100** | **1.33 / 2.00 / 4.00 m** | – | https://de.wikipedia.org/wiki/Schutzplanke |
| India DGS&D [snippet] | 311 × 83 × 3 | 4318 | – | L 1800 | – | – | (manufacturer listing) |

Notes on the Swiss Thurgau drawing (https://tiefbauamt.tg.ch/public/upload/assets/136388/281.221%20FZRS%2022%2C%20LS%20A-60%60140%201.33m%20H1.pdf?fp=3):
- Top +0.75 m, posts at 1.33 m.
- **System transitions 8 m**, minimum run length 36 m, posts driven at least 1.10 m.

### Modelling defaults for a generic EU W-beam

These are synthesised from the rows above:
- **Profile:** 310 × 83 mm, 3 mm thick, R24 bends.
- **Beam:** 4.318 m beam = 4.0 m effective + ~0.316 m lap.
- **Height:** beam top **0.75 m** (Italy 0.70–0.81 m depending on class).
- **Posts:** C 120×55 or Sigma-100 posts every **2.0 m**; 1.33 m for higher-containment German/Swiss systems; 4.0 m for N2.
- **Spacer:** Italian bordo-laterale systems often have none. US/Swiss systems use blockouts or tubes of 150–305 mm.

### Terminals

Sweden, Trafikverket approved list "Skyddsanordningar", revised 2025-11-03: https://bransch.trafikverket.se/contentassets/b9fadac8692d4b45b3308b87f604ab8e/skyddsanordningar_20251103rf.pdf

| Terminal | Class | For | Length | Max height | Other |
|---|---|---|---|---|---|
| ABC Terminal | P4 | W-beam | 12.4 m | – | Centre height 595 mm, U-posts c/c 1.33 m |
| SafeEnd | P4 | W-profile | 8.5 m | 0.68 m | Transition 4.5 m |
| SafeEnd P2 | P2 | W-profile | 5.2 m | 0.68 m | – |
| OBEX P4 | P4 | 2-wave | 7.015 m | 0.75 m | – |
| OBEX P2 | P2 | 3-wave | 3.09 m | 0.75 m | – |
| Z T1 | P4 | Tube rail | 17.5 m | – | Sigma posts c/c 1.33 m |

- Turned-down ("ramped") terminals twist the rail 90° as it descends (en.wikipedia "Traffic barrier").
- German ESP "Absenkung" length of 12 m [U]. EN 1317-4/-7 P1–P4 test speeds [U]. The flared/fishtail end-piece ("buffer end") dimensions were not sourced [U]. A typical visual model is a rounded "fishtail" plate about 0.3–0.5 m long bolted to the last beam [U].

### Sweden: cable barriers (vajerräcke), 2+1 roads, tube rails

- **2+1 median:** typically **1.5 m** wide, with the cable barrier in it (Colorado DOT report https://rosap.ntl.bts.gov/view/dot/88633/dot_88633_DS1.pdf).
- **Safence (Blue Systems AB, Sweden), 4 ropes, median models 4RI-96 / 4RC-96:** posts at **2.5 m or 3.0 m**. Working widths: W4 = 1.3 m (2.5 m spacing, N2), W5 = 1.6 m (3.0 m spacing, N2), W5 = 1.5 m (2.5 m spacing, H1). Source: https://en.bluesystems.se/products/safety-barriers/safence-4ri-96/
- **Rope heights (Safence manual, AU/NZ):** **480 / 560 / 640 / 720 mm**, rope Ø **19 mm**. Sigma posts 1230 mm long, in HDPE sleeves in concrete footings. Tolerances ±20 mm height, ±30 mm spacing. Source: https://webassets.valmont.com/valmontstaging/product-manuals/safence_product_manual.pdf?sfvrsn=2
  - Older Safence 350 [snippet]: posts 780 mm above ground. Ropes at 480–720 mm, or in an older version 480 / 630 / 780 / 930 mm with 2500 mm spacing.
- **Swedish slope barriers** (same Trafikverket list):

  | System | Ropes | Posts | c/c |
  |---|---|---|---|
  | Safence 4RU | 4 | Tube | 3 m |
  | Safence 3RU | 3 | Tube | 3.5 m |
  | Safetyline | 3 | U | 2.5 m |
  | Brifen | 4 woven | S/Z | 3.2 m |

- **Tube rails (rörräcke):**
  - **Safeline-R:** two coupled tubes, **610 mm above road**, posts c/c 4 m, 1500 mm embedded, 0.22 m wide.
  - **Rörprofil Z:** two C-tubes in 8 m elements, Sigma posts c/c 3 m.
- **Swedish W-beam (balkräcke) height per VGU [U].** Only indirect evidence: terminal max heights of 0.68–0.75 m. Use **0.75 m** to the beam top.

## B2. Thrie-beam, motorsport barriers, concrete barriers, fences

### Thrie-beam

Profile from FDOT 536-001 (same URL as B1):
- **508 mm (20 in) tall × 83 mm deep.**
- Three crests: 59 mm from each edge, plus one at the centre.
- Two valleys ±97 mm from the centreline, each with a post-bolt slot (194 mm apart) [derived].
- Radii, angles and slots as for the W-beam. 12 ga; 10 ga for transition panels.

Mounting heights:
- MASH thrie guardrail: **864 mm (34 in)** to the top [snippet: MwRSF https://mwrsf.unl.edu/researchhub/files/Report359/TRP-03-367-19-R1.pdf].
- FDOT puts the thrie centreline at 533 mm, so the top is ≈ 787 mm (31 in) [derived]. Most approach transitions are at 31 in nominal.

### Motorsport

FIA Appendix O 2023: https://www.fia.com/sites/default/files/appendix_o_2023_published_31.08.2023.pdf

- **Guardrail (Appendix O §12.3):**
  - Gap between the ground and the bottom rail, and between rails, **≤ 4 cm**.
  - Circuits licensed from 2019 must use **metal supports only**.
  - Advertising on concrete walls in lengths of ≤ 4 m, aligned with the block joints.
- **Triple guardrail ("triple Armco"):**
  - The standard FIA geometry is only in the non-public *FIA Internal Guidelines for Motor Racing Course Construction and Safety* (Appendix [1]), cited in FIA 3501-2017 §3.3.
  - Derived envelope: 3 × 310 mm + ≤ 4 × 40 mm gaps, so the top is **≈ 0.93–1.09 m** [derived]. Post spacing [U]; use 2 m.
- **Energy-absorbing barrier systems (TecPro type), FIA 3501-2017:** height **1.0–1.2 m**. Source: https://api.fia.com/sites/default/files/fia_standard_3501-2017_safety_barrier_1_0.pdf
  - Module size per Wikipedia: ≈1.2 m high × 1 m wide (uncited); per an old brochure [snippet]: 1.5 L × 1.2 H × 0.6 W m. Sources conflict [U].
- **Tyre walls, FIA Safety Bulletin 41:**
  - Uniform touring-car tyres, at least **1 m** high.
  - Bolted in **5- or 6-tyre packs**, faced with **conveyor belting**, strapped to the permanent barrier.
  - May be sunk about 10 cm into the ground.
  - Source: https://autourheilu.fi/site/assets/files/49408/fia_safety_bulletin_41.pdf
- **Debris fences, FIA 3502-2018 §4.1:**
  - At least **3.5 m** high, including a **45° top extension** adding 20 cm.
  - Posts on the non-track side, which may be mounted on a guardrail or wall.
  - Source: https://FIA.com/sites/default/files/fia_standard_3502-2018_debris_fences.pdf
  - A Geobrugg 6 m fence with 6 m post spacing is FIA-homologated [snippet].
- **FIA concrete walls (drag strips, 2022), the only public FIA concrete-block spec:**
  - Height ≥ 760 mm (first 304 m), then ≥ 990 mm. Face vertical ±2°.
  - Thickness: freestanding ≥ 200 mm, earth-backed ≥ 120 mm.
  - Temporary blocks: ≥ 1000 kg/m, base ≥ 500 mm, **length ≥ 4 m**. Joints ≈ 20 mm, connected with pins through rebar loops.
  - Source: https://FIA.com/sites/default/files/fia_specifications_of_concrete_walls_for_drag_racing_strip_-_en.pdf
  - Monaco and street-circuit block dimensions [U].
- **Circuit geometry (Appendix O 2023):**
  - New circuits: track width **≥ 12 m** (§7.3); width may change at most 1 m per 20 m.
  - **Starting grid ≥ 15 m wide; grid length per car ≥ 6 m (8 m for F1)** (§7.7). Use this for `AC_START_n` spacing: a staggered 1×1 grid with slots **8 m** apart longitudinally alternating left and right is realistic. The exact stagger used in F1 sporting rules is [U].
  - Crossfall on straights 1.5–3%; banking in curves ≤ 10% (§7.5).
  - Run-off slope ≤ 25% up, ≤ 3% down. Verges 1–5 m wide, with no step from the track (§7.6).
  - Edge lines continuous white anti-skid, **≥ 10 cm** (§7.6).

### Concrete barriers

- **New Jersey (NJDOT portable, MwRSF NJPCB-6):**
  - **813 mm (32 in) high, 610 mm (24 in) base, 152 mm (6 in) top.**
  - Segment **6096 mm (20 ft)** long. Segments ≥ 3.66 m have drainage slots.
  - Source: https://mwrsf.unl.edu/researchhub/files/Report353/NJPCB-6_R5.pdf
  - Break point **330 mm (13 in)** including a **76 mm (3 in)** vertical reveal at the bottom. Faces at **55°** (lower) and **84°** (upper) to horizontal [snippet: FHWA]. The geometry closes: 6 + 2·(19/tan84° + 10/tan55°) = 24 in [derived].
  - **Profile polyline for modelling** (half-width x from the centreline, z up, mm) [derived from the above]: (305, 0) → (305, 76) → (254, 330) → (76, 813) on each side. That gives a 610 mm base, 152 mm top, a 3 in reveal, 55° then 84°.
- **Taller versions:**
  - Ontario Tall Wall 1067 mm (42 in) (https://en.wikipedia.org/wiki/Jersey_barrier).
  - **Italy "New Jersey" tall version 110 cm** (https://it.wikipedia.org/wiki/Barriera_New_Jersey). The 100 cm Italian NJ is [U].
- **Germany "Betonschutzwand"** (de.wikipedia): half-wall 30 cm at the base, 12.5 cm at 33 cm height, top 7.5 cm. So base 60 cm, top 15 cm, total 77 or 111 cm [derived; the article may contain a typo]. Source: https://de.wikipedia.org/wiki/Betonschutzwand
- **F-shape** (break at 254 mm / 10 in, otherwise as NJ) [U]. **Single slope** (Texas 10.8°, California 9.1°, 813–1067 mm) [U]. **UK concrete step barrier** (~990 mm) [U]. **Typical precast EU element lengths** (4 m / 6 m) [U]; NJ US 6.1 m confirmed. **Swedish betongbarriär** [U].

## B3. Racing kerbs

- **This contradicts the common assumption:** **FIA Appendix O 2023 contains no kerb profiles, heights or widths, and no "max 50 mm" rule.** The only kerb text is maintenance: check for damage, avoid thick paint build-up (§12.6). Source as above.
- **Actual kerb drawings are in the FIM Standards for Circuits §4.7.** 2021: https://www.fim-moto.com/fileadmin/library/FIM_Standards_for_CIrcuits_2021.pdf. 2024: https://www.motorsport-total.com/styles/mst/reglement/motorrad/FIM_Standards_for_Circuits_2024.pdf
  - **FIM negative kerb:**
    - **1.0 m wide** across the track direction.
    - **Red/white painted segments 80 cm long** (not 1 m).
    - Sawtooth surface stepping **0 to −25 mm**.
    - **12 m triangular ramp** at each end, painted white.
    - Block thickness illegible (~200 mm?) [U].
  - **FIM positive kerb:** same layout rising to **+25 mm**; inside corners only.
  - **"Misano 200 N" negative kerb:** ≈ **1.5 m wide**, segments ≈ 0.8 m, sloping to **−50 mm** at the outer edge. Corroborated only by press reports [snippet].
  - **Verges (§4.8):** level with the track or the low side of the kerb. Negative step ≤ 2.5 cm (2024). ≥ 1 m wide, with a 1 m concrete/asphalt band where the kerb meets the run-off. Painted **green RAL 6029**. The FIA-FIM double kerb's second part is green/white.
  - **Gravel beds (§4.9):** **25 cm deep**, 1–2 cm below the track edge, 8–20 mm grains.
  - **Track markings (§4.11):** edge lines 10 cm, white.
- **F1 practice (FIA event notes and press):**
  - 2016 Hungary: a "double kerb", then a **50 mm** steel "Abu Dhabi" kerb behind a 50 mm positive kerb, replacing sausage kerbs. Source: https://www.fia.com/sites/default/files/21.07_-_event_notes_7.pdf
  - Yas Marina 2023: "50 mm kerb followed by 75 mm kerb" (hedged quote). Source: https://motorsportmagazine.com/articles/single-seaters/f1/aggressive-kerbs-return-at-yas-marina-after-qatar-gp-tyre-failures
  - 50 mm sausage at Yas T20 [snippet].
- **Sausage kerbs:** dimensions such as 150 mm high or 1500×250×150 mm are **[U]**. Model them as 50–75 mm (modern "Abu Dhabi"/sausage), with older taller sausages ~100–150 mm [U].
- **"Italian/Monza" kerbs:** dimensions [U].
- **Modelling defaults:**
  - Inner/flat kerb 1.0–1.5 m wide, flush with the track (0 to −25/−50 mm sawtooth).
  - Paint blocks 0.8 m (FIM) to ~1.0 m [U].
  - White edge line 10 cm.
  - Green verge ≥ 1 m beyond the kerb, then gravel 25 cm deep.
  - For AC surfaces: the flat kerb is `IS_VALID_TRACK=1`; the second/outer kerb, sausage, and verge are invalid (A6).

## B4. Road kerbs / curbstones (EN 1340 concrete, EN 1343 stone)

- **Italy:**
  - Sidewalk **≥ 1.50 m** clear (DM 5/11/2001 §3.4.6: https://www.mit.gov.it/nfsmitgov/files/media/normativa/2016-02/Decreto_Ministeriale_numero_6792_05-11-2001_all_1.pdf).
  - Urban E/F roads: non-mountable sidewalk kerb **≤ 15 cm** high (§4.1).
  - Sidewalk–carriageway level difference **≤ 15 cm** (DPR 503/1996 art. 5: https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:decreto.del.presidente.della.repubblica:1996-07-24;503~art5).
  - Unit sizes 12×25 / 15×25 cm [U].
- **UK, BS EN 1340** (RPC datasheet https://www.specifiedby.com/ruthin-precast-concrete-ltd/british-standard/ruthin-precast-concrete-ltd_british-standard_techspec_7_bs-kerb-data-sheet.pdf):
  - **HB2 half-batter 125 × 255 mm, 914 mm long** (600 mm also exists).
  - HB1 150 × 305; HB3/BN3 125 × 150; SP2 125 × 255.
  - Radius kerbs 780 mm.
  - Typical upstand 100–125 mm [U].
- **Germany** (https://de.wikipedia.org/wiki/Bordstein):
  - **Hochbord 15–18 cm wide × 25–30 cm high, upstand 10–15 cm.**
  - Rundbord upstand 3 cm at crossings.
  - Flachbord 10×20 → 5 cm, 20×20 → 7 cm, 30×25 → 15 cm visible.
  - Tiefbord 5–10 × 15–50 cm.
  - Units **1 m** long, plus ½ and ¼ units for curves.
  - DIN EN 1340 replaced DIN 483.
- **Sweden (kantsten), Malmö Teknisk handbok:** https://malmo.se/Teknisk-handbok/Gatubyggnad/Kantstenar.html

  | Location | Visible height |
  |---|---|
  | Main, collector and local streets | **100 mm** |
  | Residential streets | 80 mm |
  | Industrial streets | 100–120 mm |
  | Refuges | 120–130 mm |
  | Bus stops | 170 mm |
  | Marked crossings | 0 or 60 mm |
  | Driveways | ≤ 40–60 mm |

  - **This contradicts the often-assumed Swedish standard of 12 cm.**
  - Sidewalks normally 2.0 m, minimum 1.5 m (https://malmo.se/Teknisk-handbok/Gatubyggnad/Gatusektioner.html).
  - Granite type codes (GR/GF/GB, 30×15 cm) [U].
- **Modelling defaults:**
  - Kerb 150 mm wide × 250–300 mm deep, **upstand 100 mm (SE) / 120–150 mm (IT, DE)**, 1 m units, 10–20 mm chamfer/batter.
  - Sidewalk surface = kerb top. Sidewalk crossfall ≤ 2% toward the road (Malmö).
- **AC note:** model the kerb face as real geometry, so cars hit a 100–150 mm step. Use a WALL-free physics mesh with a KERB-like invalid surface (A4).

## B5. Road markings

### Italy, DPR 495/1992 (Regolamento CdS), verbatim from Normattiva

URL pattern: https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:decreto.del.presidente.della.repubblica:1992-12-16;495~art138. Change the article number for art. 35, 137, 140, 141, 144, 145, 149.

- **Art. 138, line widths (other than edge lines):** **15 cm** on A/B roads, **12 cm** on C/D/E, **10 cm** on F (local).
- **Art. 138, dashed lines (dash + gap) — this corrects common assumptions:**

  | Type | Dash + gap | Use |
  |---|---|---|
  | a | **4.5 + 7.5 m** | Design speed **> 110 km/h** only |
  | b | **3.0 + 4.5 m** | 50–110 km/h, i.e. most extra-urban C roads |
  | c | **3.0 + 3.0 m** | ≤ 50 km/h (urban) and tunnels |
  | d | 4.5 + 1.5 m | Warning of a continuous line ahead |
  | e | 3 + 3 m | Acceleration/deceleration lanes |
  | f | 1 + 1 m | Edge-line breaks at accesses |
  | g | 1 + 1.5 m | Junction guide lines |
  | h | 4.5 + 3 m | Reversible lanes |

  A continuous line must be ≥ 30 m long.
- **Art. 141, edge lines (linea di margine):** white continuous. **25 cm on A/B** (except ramps), **15 cm on ramps and C/D/E**, **12 cm on F**. Rumble edge lines up to 6 mm thick.
- **Art. 140:**
  - Lane width is measured **between line centres**. Modules 2.75 / 3.00 / 3.25 / 3.50 / 3.75 m.
  - Emergency lane 2–3.5 m; 2.5 m allowed at urban junction approaches.
  - Reserved and cycle lanes: 12 cm white + 30 cm yellow, 12 cm apart.
- **Art. 144:**
  - Stop line **≥ 50 cm**.
  - Give-way triangles (base × height): 60×70 cm (C/D), 50×60 cm (E), 40×50 cm (F), spaced ≈ ½ base.
- **Art. 145, zebra crossings:** bars 50 cm, gaps 50 cm. Length ≥ 2.5 m on local roads, ≥ 4 m elsewhere.
- **Art. 149, parking lines:** 12 cm. White = free, blue = paid, yellow = reserved.
- **Colours:**
  - White, yellow, light blue, yellow/black (art. 137). Permanent markings ≤ 3 mm proud.
  - **Temporary (roadworks) markings are yellow**, ≤ 5 mm (art. 35).

### Sweden, TSFS 2010:171 (Transportstyrelsen)

Source: https://transportstyrelsen.se/TSFS/TSFS%202010_171.pdf. It is still cited as current in Malmö's handbook (May 2026).

- **M1 mittlinje / körfältslinje:**
  - **3 m dashes.** Ratio **1:3 outside built-up areas → 3 + 9 m**; **1:1 inside → 3 + 3 m**.
  - Width 0.10 / 0.15 / 0.20 m as a centre line; up to 0.30 / 0.40 m as a lane line.
  - Required outside built-up areas when the carriageway is > 6.0 m and the limit is ≥ 60 km/h. Not used below 5.5 m.
- **M2 kantlinje:** **1 m dashes, ratio 1:2 → 1 + 2 m**, width 0.10–0.30 m (https://www.transportstyrelsen.se/sv/vagtrafik/trafikregler-och-vagmarken/vagmarken/vagmarkeringar/kantlinje/).
- **M3 varningslinje:** **9 m dashes, 3:1 → 9 + 3 m**, width 0.10 / 0.15 m (https://www.transportstyrelsen.se/sv/vagtrafik/trafikregler-och-vagmarken/vagmarken/vagmarkeringar/varningslinje/).
- **M8 heldragen linje (spärrlinje):** 0.10–0.30 m. In a double line, the gap equals the line width.
- **M13 stop line: 0.40 m.** This differs from IT/DE, which use 0.50 m.
- **M14 give-way:** triangles 0.70 m high × 0.50 m base, 0.50 m apart.
- **M15 zebra:** 0.50 m bars / 0.50 m gaps. Length ≥ 2.5 m at ≤ 60 km/h, else ≥ 4.0 m.
- **Colour:** all permanent lines **white**. Yellow is for temporary markings (conflicting white must be removed) and for bus-stop/parking-related marks (sv.wikipedia https://sv.wikipedia.org/wiki/Vägmarkering). The year yellow centre lines were abolished (1971?) is [U].

### Germany (RMS, via de.wikipedia)

Source: https://de.wikipedia.org/wiki/Fahrbahnmarkierung
- Narrow / wide lines: **0.15 / 0.30 m** on Autobahnen, **0.12 / 0.25 m** on other roads.
- Leitlinie ratio 1:2: Autobahn 6 + 12 m, down to 3 m dashes in towns.
- Haltlinie 0.50 m. Roadworks markings yellow.

### Where to find each country's standard

- **Framework:** Vienna Convention on Road Signs and Signals (1968) and its 1973 European Agreement / Protocol on Road Markings, which set general rules on colours and dash ratios [U for exact clauses].
- **EN 1436** covers marking performance (retroreflection, skid resistance), **not geometry**.
- **National geometry:**
  - Italy: DPR 495/1992 art. 137–155 + DM 5/11/2001.
  - Sweden: TSFS 2010:171 + Trafikverket VGU (TRVINFRA-00396 etc.).
  - Germany: RMS (Richtlinien für die Markierung von Straßen).
  - UK: TSRGD 2016 + Traffic Signs Manual Chapter 5.
  - France: IISR 7e partie.
  - Spain: Norma 8.2-IC.
  - The UK/FR/ES values were not fetched [U].

## B6. Lane widths, crossfall, superelevation

### Italy, DM 5/11/2001 Tab. 3.4.a

Source: https://www.mit.gov.it/nfsmitgov/files/media/normativa/2016-02/Decreto_Ministeriale_numero_6792_05-11-2001_all_1.pdf (PDF pp. 27–28). All widths in metres.

| Road type | Lane | Median | Left shoulder | Right shoulder | Emergency lane |
|---|---|---|---|---|---|
| A extra-urban | 3.75 | 2.60 | 0.70 | 2.50 (if no emergency lane) | 3.00 |
| A urban | 3.75 | 1.80 | 0.70 | 2.50 | 3.00 |
| B | 3.75 | 2.50 | 0.50 | 1.75 | – |
| C1 | 3.75 | – | – | 1.50 | – |
| C2 | 3.50 | – | – | 1.25 | – |
| D | 3.25 (3.50 with buses) | 1.80 | 0.50 | 1.00 | – |
| E | 3.00 | – | – | 0.50 | – |
| F1 extra-urban | 3.50 | – | – | 1.00 | – |
| F2 extra-urban | 3.25 | – | – | 1.00 | – |
| F urban | 2.75 | – | – | 0.50 | – |

- **Crossfall on straights: 2.5%** for all road types (tunnel minimum 1%).
- **Maximum superelevation:**
  - **7%** on A, B, C and extra-urban F.
  - **5%** on D.
  - **3.5%** on urban E/F.
  - Capped at **6%** where snow is frequent.
- **Edge elements:**
  - Edge marking strip 0.25 (A–B) / 0.15 (C–E) / 0.12 m (F).
  - Carriageway edge strip ≥ 0.30 m.
  - Verge (arginello) ≥ 0.75 m (A–D) or ≥ 0.50 m (E–F).
  - Ditch ≥ 0.80 m.

### Sweden

- **Malmö:**
  - Carriageway crossfall **2–4%, normally 2.5%**. Curve superelevation **2.5–5.5%**.
  - Sidewalks ≤ 2%, cycle paths ≤ 2.5%.
  - Lanes ≥ 3.5 m between obstacles.
  - Source: https://malmo.se/Teknisk-handbok/Gatubyggnad/Lutningar-och-avvattningar.html
- **Trafikverket VGU "Krav Grundvärden":** uses **5.5%** superelevation as the reference curve (https://trafikverket.diva-portal.org/smash/get/diva2:1909331/FULLTEXT01.pdf).
  - Lane widths are in TRVINFRA-00396 (not readable, JS-only) [U].
  - The 6% max is [U]; 5.5% is what the sources show.
- **2+1 roads:**
  - Classic "13 m road" = two 3.5 m lanes + two 3 m shoulders, later converted to 2+1 (https://en.wikipedia.org/wiki/2%2B1_road). A 14 m 2+1 section type also exists.
  - Lane width 3.25 m + 1 m [U].

### Germany

Sources: https://de.wikipedia.org/wiki/Straßenquerschnitt and https://de.wikipedia.org/wiki/Querneigung
- Lanes 2.75–3.75 m. Minimum crossfall 2.5%.
- Superelevation up to 8% (textbook, not a guideline). RAL/RAA exact maxima [U].

### Circuits (FIA Appendix O)

- Crossfall 1.5–3% on straights. Banking ≤ 10%. Width ≥ 12 m (new circuits).

---

# Summary of facts that contradict common assumptions

1. **`geotags` must be an array of two STRINGS**, not numbers, or CM ignores them. CSP then guesses the sun and time zone. Also put `[WEATHER_FX] LATITUDE/LONGITUDE/TIMEZONE_ID` in `surfaces.ini`.
2. **`run` is free display text** with no gameplay effect. **`pitboxes` defaults to 2 in CM** if unparsable, and must match the contiguous `AC_PIT_0..N-1` count. `MAX_CLIENTS` must be ≤ the number of pits.
3. **`BLACK_FLAG_TIME=0` on invalid surfaces stops cuts being reported in online race sessions.** Use ~5.
4. **Surface KEY matching is by substring, and only one key may match a mesh name** (`KERB` + `KERBHIGH` → load error).
5. **`fast_lane.ai` has no closed-loop flag.** Closure is inferred from geometry (AssettoServer: first and last points < 50 m apart). Timing comes from `AC_TIME_n` lines; the spline gives normalised position for DRS, sections and race order.
6. **BC7 textures do not load in vanilla AC (only with CSP).** Use DXT1/DXT5 (BC1/BC3) with dimensions divisible by 4.
7. **CSP trees are real instanced 3D multi-LOD models with automatic billboards.** CSP model replacements are **not** an instancing system and can never carry physics (names with `AC_`, a leading digit, or `WALL` are refused).
8. **FIA Appendix O has no kerb dimensions or 50 mm rule.** Kerb drawings are in the FIM Standards. FIM paint blocks are **80 cm**, not 1 m. The minimum debris fence is **3.5 m**.
9. **Italian dashed centre lines are 3 + 4.5 m on most extra-urban roads.** 4.5 + 7.5 m applies only above 110 km/h design speed; urban roads use **3 + 3 m**. Edge lines are 25 cm on A/B, 15 cm on C/D/E, 12 cm on F.
10. **Many Italian guard rails have no spacer** (beam bolted to the C-post). Beam top is 0.70–0.81 m by class; beam thickness can be 2 mm or 3.5 mm, not always 3 mm.
11. **Swedish kerb upstand is normally 100 mm (Malmö), not 120 mm.** The Swedish stop line is 0.40 m, not 0.50 m.
