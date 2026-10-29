#!/usr/bin/env python3
"""Download OpenStreetMap data for the map area via the Overpass API.

Output: data/osm/raw.json (Overpass JSON with inline geometry). Re-run with --force to refresh.
OSM data is (c) OpenStreetMap contributors, ODbL.
"""

import argparse
import json
import time

import requests

from common import DATA, Config, ensure_dirs

QUERY = """
[out:json][timeout:300][maxsize:536870912];
(
  way["highway"]({bbox});
  way["building"]({bbox});
  relation["building"]({bbox});
  way["landuse"]({bbox});
  relation["landuse"]({bbox});
  way["natural"]({bbox});
  relation["natural"]({bbox});
  way["leisure"]({bbox});
  way["waterway"]({bbox});
  way["railway"]({bbox});
  way["barrier"]({bbox});
  way["amenity"="parking"]({bbox});
  way["man_made"]({bbox});
  node["highway"~"^(traffic_signals|stop|give_way|crossing|street_lamp|mini_roundabout)$"]({bbox});
  node["natural"="tree"]({bbox});
  node["historic"]({bbox});
  node["man_made"]({bbox});
  node["power"~"^(tower|pole)$"]({bbox});
  way["power"="line"]({bbox});
);
out geom;
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="download again even if cached")
    args = ap.parse_args()
    ensure_dirs()
    cfg = Config()
    out_dir = DATA / "osm"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / "raw.json"
    if out.exists() and not args.force:
        print(f"{out} already present (use --force to refresh)")
        return
    w, s, e, n = cfg.lonlat_bounds(cfg["osm"]["margin_m"])
    query = QUERY.format(bbox=f"{s:.6f},{w:.6f},{n:.6f},{e:.6f}")
    url = cfg["osm"]["overpass_url"]
    for attempt in range(4):
        print(f"Querying Overpass ({url})...")
        r = requests.post(url, data={"data": query}, timeout=400,
                          headers={"User-Agent": "ac-track-studio/1.0 (private track builder)"})
        if r.status_code == 200:
            break
        print(f"  HTTP {r.status_code}: {r.text[:200]}")
        time.sleep(30 * (attempt + 1))
    r.raise_for_status()
    data = r.json()
    out.write_text(json.dumps(data))
    kinds = {}
    for el in data["elements"]:
        kinds[el["type"]] = kinds.get(el["type"], 0) + 1
    print(f"Wrote {out}: {kinds}, {out.stat().st_size / 1e6:.1f} MB, "
          f"OSM timestamp {data.get('osm3s', {}).get('timestamp_osm_base')}")


if __name__ == "__main__":
    main()
