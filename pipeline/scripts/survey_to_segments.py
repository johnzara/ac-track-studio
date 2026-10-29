#!/usr/bin/env python3
"""survey_stops.tsv (one line per Street View stop, read from the strips made by sv_strips.py)
-> streetview_segments.json (the pipeline's survey format) and streetview_reference_notes.md.

Each stop stands for the road from halfway to the previous stop to halfway to the next one, so a
wall or guardrail starts and ends within about 50 m of where it really does. width_m is the
asphalt width read off the stop's top-down road patch (streetview/ground, true to scale) with a
metre ruler (sv_width_sheets.py), good to about 0.3 m; where the patch is hidden (the car's blur,
junctions) it is interpolated from the neighbouring stops and the notes say so. trace_climb.py
keeps the built width within roads.climb_sv_tol of it.
"""

import json

import numpy as np

from common import OUTPUT, ROOT


def main():
    rows = []
    for line in (ROOT / "survey_stops.tsv").read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        at, date, left, lh, right, rh, edge, centre, surface, notes, width = (line.split("\t") + [""])[:11]
        rows.append(dict(at=float(at), date=date, left=left, lh=float(lh) if lh else None, right=right,
                         rh=float(rh) if rh else None, edge=edge, centre=centre, surface=surface, notes=notes,
                         width=float(width) if width else None))
    route = json.loads((OUTPUT / "route_climb.geojson").read_text())["features"][0]["properties"]
    length = route["length_m"]
    known = [(r["at"], r["width"]) for r in rows if r["width"]]
    segs = []
    for i, r in enumerate(rows):
        a = 0.0 if i == 0 else (rows[i - 1]["at"] + r["at"]) / 2
        b = length if i == len(rows) - 1 else (r["at"] + rows[i + 1]["at"]) / 2
        width = r["width"] or round(float(np.interp(r["at"], [k[0] for k in known], [k[1] for k in known])), 1)
        if not r["width"]:
            r["notes"] += " Width not measurable here: interpolated from the neighbouring stops."
        segs.append({"from_m": round(a), "to_m": round(b), "width_m": width, "lanes": 2 if width >= 5.6 else 1,
                     "edge_lines": r["edge"], "centre_line": r["centre"], "left": r["left"], "right": r["right"],
                     "left_height_m": r["lh"], "right_height_m": r["rh"], "surface": r["surface"],
                     "vegetation": "see notes", "notes": f"Stop at {r['at']:.0f} m ({r['date']}). {r['notes']}"})
    (ROOT / "streetview_segments.json").write_text(json.dumps(segs, indent=1, ensure_ascii=False) + "\n")
    title = json.loads((ROOT / "config.json").read_text()).get("level_title", "")
    md = [f"# Street View survey: {title}", "",
          f"{len(rows)} stops, about one every 100 m, read from the Street View cache (`streetview/strips/`: left, ahead and",
          "right of the driving direction at each stop). Imagery: September 2023 and July 2026, mixed along the road.",
          "Sides are in the driving direction. Widths are the asphalt measured on the top-down road patches", "(`streetview/ground/`), good to about 0.3 m.", "",
          "| At m | Date | Width m | Left | Right | Lines | Surface | Notes |", "|---|---|---|---|---|---|---|---|"]
    for r, s in zip(rows, segs):
        side = lambda c, h: c + (f" {h:g} m" if h else "")
        lines = f"edge {r['edge']}" + ("" if r["centre"] == "none" else f", centre {r['centre']}")
        md.append(f"| {r['at']:.0f} | {r['date']} | {s['width_m']} | {side(r['left'], r['lh'])} | {side(r['right'], r['rh'])} | "
                  f"{lines} | {r['surface']} | {r['notes']} |")
    (ROOT / "streetview_reference_notes.md").write_text("\n".join(md) + "\n")
    from collections import Counter
    print(len(segs), "segments;", "left", dict(Counter(s["left"] for s in segs)), "right", dict(Counter(s["right"] for s in segs)))
    print("width min/median/max", min(s["width_m"] for s in segs), float(np.median([s["width_m"] for s in segs])), max(s["width_m"] for s in segs))


if __name__ == "__main__":
    main()
