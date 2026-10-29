# Quality bar

What "matches reality" means for a studio track, and how to show it. The brief's table holds the
user's per-track settings (extra width, kerb style, radii for trees and detail); this file holds the
rest. Dimensions vary by country: use the national standard for the track's country (the brief
names it) and say in the build README which one you used. The sourced numbers below (and many more,
with links) are in docs/AC_NOTES.md → Part B; where a country is not covered there, look its
standard up before modelling.

## Road line, width, elevation

| Item | Requirement | Measure / report |
|---|---|---|
| Centreline | ≤ 0.5 m from the real carriageway centre where the orthophoto or a topo database shows the road; ≤ 1.5 m under trees (topo DB, Street View, continuity). Never raw OSM (5–15 m off under trees, cuts hairpins). | Offset statistics vs. the traced edges; a plot of stretches where nothing was seen. |
| Width | Measured every station (orthophoto edges, topo carriageway, Street View estimate), varying as in reality; the brief's minimum and AC extra width; narrower only where a building or another leg of the road is close. | Built vs. measured width per 100 m; narrowest / widest points. |
| Elevation | From the best bare-earth DTM (LiDAR ≤ 2 m where it exists; never a surface model with trees). Median-filter spikes, then smooth along the road (σ ≈ 3–8 m by road class) so the grade has no kinks, keeping the real grade and the real crest / dip radii. | Max and mean deviation of the road from the DTM along the centreline (target mean < 0.15 m, max < 0.5 m except bridges and DTM artefacts); grade plot (no spikes); steepest 10 m and 100 m. |
| Junctions, bridges, tunnels | Side roads meet the road flush; bridge decks straight with parapets; tunnels only if real. | Listed in the README. |
| Cross-section | Crown / crossfall as the brief says (the reference uses 1.5 %; national standards say 2.5 % on straights in Italy and Sweden), superelevation on fast bends where the road is built that way (Italy ≤ 7 % on A/B/C and rural F, 5 % on D, 3.5 % urban; Sweden 2.5–5.5 %; circuits 1.5–3 % crossfall, banking ≤ 10 %), shoulders and verges shaped down into the terrain. | Cross-sections every 250 m in the README (images or numbers). |
| Mesh | Road vertices every ≤ 0.5 m along and ≥ 5 across; road and terrain share their edge vertices (no gaps, no steps, no terrain poking through: 2 cm tolerance). | `audit_ac.py` clean. |

## Surface and markings

- Photo-based CC0 asphalt with a normal map and macro variation: wheel paths, oil band, crumbling
  edges; per stretch the condition the survey saw (new / worn / patched / concrete / cobbles /
  gravel). No visible tiling at driving speed.
- Markings only where they really are (or the full standard if the brief says so), with the
  national dimensions: line widths, dash / gap lengths, distance from the edge. Worn where the road
  is worn. Painted lines are visual only (1–2 cm above the asphalt).
- Physics surface `ROAD` without artificial bumps (`SIN_HEIGHT=0`); paint and kerbs their own
  surfaces (docs/AC_NOTES.md).

## Kerbs

Real-world (always, where they exist), as real geometry so a wheel hits the step:

| Type | Shape |
|---|---|
| Kerbstone | ~150 mm wide, 250–300 mm deep, 10–20 mm chamfer, in ~1 m units. Upstand: **Sweden 100 mm** on most streets (80 residential, 120–130 refuges, 170 bus stops, 0–60 at crossings, ≤ 40–60 at driveways); Italy ≤ 150 mm; Germany 100–150 mm (Hochbord), 30 mm Rundbord at crossings. |
| Sidewalk | At kerb height, ≥ 1.5 m (Italy), normally 2.0 m (Sweden), crossfall ≤ 2 % towards the road. |
| Gutter / drainage channel | Concrete or stone, 30–50 cm wide, a shallow dish at the edge of the asphalt (dimensions *typical*). |
| Low concrete edge (rural) | As surveyed; often broken and overgrown. |

Physics: a kerb-like invalid surface, not `WALL` (docs/AC_NOTES.md → A4).

Racing kerbs (only when the brief asks for them), at the apex and exit of corners tighter than the
brief's radius. The FIA publishes no kerb dimensions; the FIM Standards for Circuits do:

| Type | Shape |
|---|---|
| Flat kerb (FIM) | **1.0 m** wide, red / white blocks **0.8 m** long, sawtooth surface from 0 to −25 mm (negative kerb) or rising to +25 mm (positive, inside of corners only), 12 m triangular ramps at the ends; wider "Misano" type 1.5 m down to −50 mm. |
| Second / sausage kerb | Modern F1 practice 50–75 mm high behind the flat kerb; old tall sausages ~100–150 mm (*unverified*). |
| Edge line, verge, gravel | White edge line 10 cm; green verge (RAL 6029) ≥ 1 m level with the kerb; gravel trap 25 cm deep, 1–2 cm below the track edge. |

Physics surfaces: the flat kerb valid (`IS_VALID_TRACK=1`), the second kerb, verge and run-off
invalid with `BLACK_FLAG_TIME` > 0; keys that are never substrings of each other (`KERBFLAT`,
`KERBSAUS`).

## Guardrails, barriers, walls, fences

Exactly where the survey saw them (plus, if the brief says so, guardrails where the ground drops
away steeply and nothing is there: list those as added). Every one is solid: an invisible collision
mesh (`WALL`) along it, no gaps a car can fall through at joints.

| Type | Shape |
|---|---|
| W-beam guardrail | Two-wave beam **310 × 83 mm**, 2–3.5 mm steel, R24 bends; 4.0 m effective beams (4.32 m with the 0.32 m lap, bolted joints). Beam top **~0.75 m** (Italy 0.70–0.81 m by class, Sweden 0.75 m, US MGS 0.79 m). Posts C 120×55 / Sigma-100 / IPE-100 every **2.0 m** (1.33 m on German / Swiss higher-containment systems, 4.0 m for Italian N2, 1.905 m US). **Italian roadside systems are often bolted to the post with no spacer**; Swiss ones use a Ø168 mm tube spacer; US blockouts 150–305 mm. Ends: terminals 5–12 m long, ramped down to the ground (the rail twisted as it descends) or a rounded "fishtail" plate. Galvanised, dirtier on the lower half. |
| Thrie-beam | 508 × 83 mm, three waves, top ~0.79–0.86 m (transitions, bridges). |
| Cable barrier (Sweden, 2+1 roads) | 4 ropes Ø19 mm at 480 / 560 / 640 / 720 mm on Sigma posts every 2.5–3 m (Safence), in a ~1.5 m median; 3-rope slope barriers every 2.5–3.5 m. |
| Tube rail (Sweden) | Two coupled tubes ~0.61 m high, posts every 3–4 m. |
| Concrete barrier (New Jersey) | **813 mm** high, 610 mm base, 152 mm top; profile per side (half-width, height in mm): (305, 0) → (305, 76) → (254, 330) → (76, 813); 6.1 m (US) or 4–6 m segments; Italian tall version 1.10 m. |
| Bridge parapet | Concrete upstand 0.5–1 m with a steel rail to 1.0–1.2 m (as seen). |
| Motorsport | Triple guardrail top ~0.93–1.09 m with ≤ 4 cm gaps; tyre walls ≥ 1 m in bolted 5–6-tyre packs faced with conveyor belt; debris fences ≥ 3.5 m with a 45° top; concrete wall blocks ≥ 4 m long, ≥ 0.76 m high. |
| Retaining wall | Its top follows the natural bank (the terrain keeps its height behind it), material and height from the survey (dry stone, mortared stone, board-formed concrete, gabions…), coping where seen. |
| Free-standing wall | Surveyed height (garden walls 0.8–1.2 m), often with a railing or hedge on top. |
| Fences, hedges | Posts and rails / mesh as seen; hedges as dense foliage of the surveyed height. |

No wall or guardrail that does not exist: remove survey-inferred walls at hairpin apexes unless
seen (a reference-project lesson).

## Markings (key numbers)

- **Italy** (DPR 495/1992): dashed lines **3 + 4.5 m** on most rural roads (50–110 km/h), **3 + 3 m**
  urban, 4.5 + 7.5 m only above 110 km/h; line width 15 cm (A/B), 12 cm (C/D/E), 10 cm (F); edge
  lines 25 / 15 / 12 cm; all white (yellow = roadworks).
- **Sweden** (TSFS 2010:171): centre line 3 + 9 m rural, 3 + 3 m urban; warning line 9 + 3 m; edge
  line 1 + 2 m (or solid); widths 0.10–0.20 m; stop line 0.40 m; all white.
- **Germany** (RMS): 0.12 / 0.25 m lines off the Autobahn, ratio 1:2.
- Others: the national standard named in the brief.

## Trees and vegetation

- **3D trees** within the brief's radius of the road: per-species models (trunk, branches,
  leaf-cluster cards) at their real positions (crowns from the orthophoto / land cover), heights
  and densities as in reality (Street View), species for the region (the brief lists typical ones);
  CSP trees with LODs are the preferred route (docs/PIPELINE.md → 3D trees). Cards beyond, out to
  the brief's card radius.
- Clear road: no opaque leaf pixel within 0.4 m of the asphalt at any height; invisible trunk
  colliders within ~15 m of the road.
- Ground cover: CSP 3D grass on meadows and verges (none on shoulders, paved ground, rock, road),
  hedgerows, shrubs, vineyards / orchards / crops in rows where they are.

## Terrain, buildings, look

- Terrain: 1 m mesh and ~13 cm material masks within the brief's corridor (gravel shoulder, grass,
  forest floor, paved, rock on steep cuttings), coarser beyond; horizon ring from Copernicus out to
  the brief's distance.
- Buildings: footprints from the topo database / OSM, heights from it or from LiDAR (DSM − DTM),
  roof shapes and colours from the orthophoto, facades in the region's styles; full detail within
  the brief's radius; hand-built landmarks where the user marked them; collision near the road.
- Look: every material's ksAmbient / ksDiffuse × `assetto_corsa.exposure` (0.5); ground colours
  measured from the orthophoto, desaturated (raw photo colours look too green / too bright).

## Performance budget

kn5 ≤ ~1 GB (aim lower), ≤ ~7 M triangles drawn, every mesh node ≤ 65,536 vertices, world chunked
into 100–250 m pieces per material, DDS textures (BC1 opaque / BC3 alpha; BC7 only if CSP-only is
fine) ≤ 4096 px with mips, `lodIn` / `lodOut` set on clutter, trees and far buildings, shadows off on
far terrain and small props.

## The build README must report

Data sources and licences; route length vs. the planner's; elevation deviation and the grade
profile; width table; corner list with radii; survey coverage (stops, dates, gaps) and what filled
the gaps; every roadside type built (and any added beyond reality); tree counts by species and
radius; kn5 size, triangles, meshes, texture memory; audits; driver's-eye previews compared with
the survey; known differences from reality.
