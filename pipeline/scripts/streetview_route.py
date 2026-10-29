#!/usr/bin/env python3
"""Index Google Street View panoramas along a route made by route.py.

  GOOGLE_MAPS_API_KEY=... streetview_route.py NAME [--spacing 50]

Samples the route every `spacing` metres, asks the Street View Static API *metadata*
endpoint for the nearest outdoor panorama (no image pixels are requested or stored), and
writes output/streetview_route_NAME.csv with the panorama id, its real position and date,
the distance along the route and the road heading there (for looking along the road).
"""

import argparse
import csv
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

import numpy as np

from common import OUTPUT, Config

METADATA_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"


def metadata(lat, lon, key, radius):
    q = urllib.parse.urlencode({"location": f"{lat:.7f},{lon:.7f}", "radius": radius,
                                "source": "outdoor", "key": key})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(f"{METADATA_URL}?{q}", timeout=20) as r:
                data = json.load(r)
            if data.get("status") in {"OVER_QUERY_LIMIT", "UNKNOWN_ERROR"} and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return data
        except OSError:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--spacing", type=float, default=50.0)
    ap.add_argument("--search-radius", type=int, default=30)
    args = ap.parse_args()
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        sys.exit("Set GOOGLE_MAPS_API_KEY")
    cfg = Config()
    props = json.loads((OUTPUT / f"route_{args.name}.geojson").read_text())["features"][0]
    lonlat = np.array(props["geometry"]["coordinates"])
    xy = np.array(props["properties"]["local_xy"])
    s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(xy, axis=0).T))])
    samples = np.arange(0, s[-1], args.spacing)
    lon = np.interp(samples, s, lonlat[:, 0])
    lat = np.interp(samples, s, lonlat[:, 1])
    ahead_x = np.interp(np.minimum(samples + 10, s[-1]), s, xy[:, 0])
    ahead_y = np.interp(np.minimum(samples + 10, s[-1]), s, xy[:, 1])
    here_x, here_y = np.interp(samples, s, xy[:, 0]), np.interp(samples, s, xy[:, 1])
    print(f"Route {args.name}: {s[-1] / 1000:.2f} km, {len(samples)} sample points")

    seen, rows = set(), []
    for i, (la, lo, d) in enumerate(zip(lat, lon, samples)):
        data = metadata(la, lo, key, args.search_radius)
        status = data.get("status")
        if status == "OK" and data["pano_id"] not in seen:
            seen.add(data["pano_id"])
            heading = math.degrees(math.atan2(ahead_x[i] - here_x[i], ahead_y[i] - here_y[i])) % 360
            loc = data.get("location", {})
            rows.append({
                "pano_id": data["pano_id"], "lat": loc.get("lat"), "lon": loc.get("lng"),
                "date": data.get("date"), "route_m": round(float(d)), "road_heading": round(heading),
                "google_maps_url": "https://www.google.com/maps/@?" + urllib.parse.urlencode(
                    {"api": 1, "map_action": "pano", "pano": data["pano_id"],
                     "heading": round(heading), "pitch": 0, "fov": 90}),
            })
        elif status not in {"OK", "ZERO_RESULTS"}:
            sys.exit(f"API status {status}: {data.get('error_message', '')}")
        time.sleep(0.05)
    out = OUTPUT / f"streetview_route_{args.name}.csv"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["pano_id"])
        w.writeheader()
        w.writerows(rows)
    print(f"Found {len(rows)} unique panoramas -> {out}")


if __name__ == "__main__":
    main()
