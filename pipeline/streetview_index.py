#!/usr/bin/env python3
"""Find Google Street View panorama IDs near coordinates and save viewer links.

Google Maps Platform terms prohibit bulk downloading or storing Street View images.
This script uses the official Street View Static API metadata endpoint and saves
only panorama IDs, user-specified sample coordinates, and Google Maps viewer URLs.
It does not request or save image pixels.
"""

import argparse
import csv
import getpass
import json
import math
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

METADATA_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"
VIEWER_URL = "https://www.google.com/maps/@"
EARTH_RADIUS_M = 6_371_008.8


def sample_points(lat, lon, radius_m, spacing_m):
    """Yield a square grid clipped to the requested circle, center first."""
    n = math.ceil(radius_m / spacing_m)
    offsets = [(x * spacing_m, y * spacing_m)
               for y in range(-n, n + 1)
               for x in range(-n, n + 1)
               if math.hypot(x * spacing_m, y * spacing_m) <= radius_m]
    offsets.sort(key=lambda xy: xy[0] ** 2 + xy[1] ** 2)
    for east_m, north_m in offsets:
        sample_lat = lat + math.degrees(north_m / EARTH_RADIUS_M)
        cos_lat = math.cos(math.radians(sample_lat))
        if abs(cos_lat) < 1e-6:
            raise ValueError("sampling too close to a pole")
        sample_lon = lon + math.degrees(east_m / (EARTH_RADIUS_M * cos_lat))
        sample_lon = (sample_lon + 180) % 360 - 180
        yield sample_lat, sample_lon


def get_metadata(lat, lon, key, search_radius_m, source, timeout_s=15):
    query = urlencode({
        "location": f"{lat:.7f},{lon:.7f}",
        "radius": search_radius_m,
        "source": source,
        "key": key,
    })
    url = f"{METADATA_URL}?{query}"
    for attempt in range(4):
        try:
            with urlopen(url, timeout=timeout_s) as response:
                data = json.load(response)
            status = data.get("status")
            if status in {"OVER_QUERY_LIMIT", "UNKNOWN_ERROR"} and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return data
        except HTTPError as exc:
            if exc.code in {429, 500, 502, 503, 504} and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"metadata request failed (HTTP {exc.code})") from exc
        except (URLError, TimeoutError) as exc:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"metadata request failed: {exc}") from exc
    raise RuntimeError("metadata request failed after retries")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lat", type=float, required=True, help="center latitude")
    parser.add_argument("--lon", type=float, required=True, help="center longitude")
    parser.add_argument("--radius-m", type=float, default=250, help="area radius in meters (default: 250)")
    parser.add_argument("--spacing-m", type=float, default=40, help="grid spacing in meters (default: 40)")
    parser.add_argument("--search-radius-m", type=int, default=25,
                        help="nearest-panorama search radius per grid point (default: 25)")
    parser.add_argument("--source", choices=("outdoor", "default"), default="outdoor")
    parser.add_argument("--delay-s", type=float, default=0.2, help="delay between requests (default: 0.2)")
    parser.add_argument("--max-requests", type=int, default=500,
                        help="safety limit on metadata requests (default: 500)")
    parser.add_argument("--output", type=Path, default=Path("streetview_index.csv"))
    parser.add_argument("--dry-run", action="store_true", help="show request count without using the API")
    args = parser.parse_args()

    if not (-90 < args.lat < 90) or not (-180 <= args.lon <= 180):
        parser.error("latitude must be between -90 and 90; longitude between -180 and 180")
    if not all(math.isfinite(v) for v in (args.lat, args.lon, args.radius_m, args.spacing_m, args.delay_s)):
        parser.error("numeric inputs must be finite")
    if args.radius_m <= 0 or args.spacing_m <= 0 or args.delay_s < 0:
        parser.error("radius and spacing must be positive; delay cannot be negative")
    if args.search_radius_m < 0 or args.max_requests < 1:
        parser.error("search radius must be nonnegative; max requests must be positive")
    if args.radius_m >= (90 - abs(args.lat)) * math.pi * EARTH_RADIUS_M / 180:
        parser.error("the requested area reaches a geographic pole")
    if args.radius_m / args.spacing_m > args.max_requests:
        parser.error("grid exceeds --max-requests; increase --spacing-m")

    try:
        points = list(sample_points(args.lat, args.lon, args.radius_m, args.spacing_m))
    except ValueError as exc:
        parser.error(str(exc))
    if len(points) > args.max_requests:
        parser.error(f"grid needs {len(points)} requests; increase --spacing-m or --max-requests")
    print(f"Grid points / maximum metadata requests: {len(points)}")
    if args.dry_run:
        return 0

    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        if not sys.stdin.isatty():
            parser.error("set GOOGLE_MAPS_API_KEY (with Street View Static API enabled)")
        key = getpass.getpass("Google Maps API key: ").strip()
    if not key:
        parser.error("an API key is required")

    found = {}
    for index, (sample_lat, sample_lon) in enumerate(points, 1):
        try:
            data = get_metadata(sample_lat, sample_lon, key, args.search_radius_m, args.source)
        except RuntimeError as exc:
            print(f"Stopped at request {index}: {exc}", file=sys.stderr)
            return 1
        status = data.get("status")
        if status == "OK":
            pano_id = data.get("pano_id")
            if not pano_id:
                print(f"Stopped at request {index}: OK response missing pano_id", file=sys.stderr)
                return 1
            if pano_id not in found:
                viewpoint = f"{sample_lat:.7f},{sample_lon:.7f}"
                viewer = VIEWER_URL + "?" + urlencode({
                    "api": 1, "map_action": "pano", "pano": pano_id,
                    "viewpoint": viewpoint,
                })
                found[pano_id] = (pano_id, f"{sample_lat:.7f}", f"{sample_lon:.7f}", viewer)
        elif status != "ZERO_RESULTS":
            print(f"Stopped at request {index}: API status {status}: {data.get('error_message', '')}",
                  file=sys.stderr)
            return 1
        if index < len(points):
            time.sleep(args.delay_s)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("pano_id", "sample_lat", "sample_lon", "google_maps_url"))
        writer.writerows(found.values())
    print(f"Saved {len(found)} unique panorama links to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
