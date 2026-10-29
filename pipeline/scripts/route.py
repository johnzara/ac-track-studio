#!/usr/bin/env python3
"""Find a driving route through the OSM road network and check it is inside the map.

  route.py NAME LAT,LON LAT,LON [LAT,LON ...]

Routes through the given waypoints (snapped to the nearest drivable road node), using all
drivable OSM ways in data/osm/raw.json (shortest distance, tracks penalised). Writes
output/route_NAME.geojson (lon/lat and local coordinates) and prints the streets used,
the length, and whether every point lies inside the terrain.
"""

import heapq
import json
import sys
from collections import defaultdict

import numpy as np

from common import DATA, OUTPUT, Config

PENALTY = {"track": 50.0, "service": 3.0, "path": 8.0, "bridleway": 8.0, "footway": 8.0, "cycleway": 8.0}


def load_graph(cfg):
    raw = json.loads((DATA / "osm" / "raw.json").read_text())
    drivable = set(cfg["roads"]["drivable_classes"])
    coords, edges = {}, defaultdict(list)
    for el in raw["elements"]:
        t = el.get("tags", {})
        if el["type"] != "way" or t.get("highway") not in drivable:
            continue
        nodes, geom = el["nodes"], el["geometry"]
        for nid, g in zip(nodes, geom):
            coords[nid] = (g["lon"], g["lat"])
        lon = np.array([g["lon"] for g in geom])
        lat = np.array([g["lat"] for g in geom])
        x, y = cfg.lonlat_to_local(lon, lat)
        seg = np.hypot(np.diff(x), np.diff(y)) * PENALTY.get(t["highway"], 1.0)
        oneway = t.get("oneway") == "yes"
        label = t.get("name") or t.get("ref") or t["highway"]
        for a, b, w in zip(nodes[:-1], nodes[1:], seg):
            edges[a].append((b, w, label, el["id"]))
            if not oneway:
                edges[b].append((a, w, label, el["id"]))
    return coords, edges


def nearest(cfg, coords, lon, lat):
    ids = np.array(list(coords))
    pts = np.array([coords[i] for i in ids])
    x, y = cfg.lonlat_to_local(pts[:, 0], pts[:, 1])
    px, py = cfg.lonlat_to_local(lon, lat)
    k = int(np.argmin(np.hypot(x - px, y - py)))
    return int(ids[k]), float(np.hypot(x[k] - px, y[k] - py))


def dijkstra(edges, start, goal):
    dist, prev = {start: 0.0}, {}
    heap = [(0.0, start)]
    while heap:
        d, u = heapq.heappop(heap)
        if u == goal:
            break
        if d > dist.get(u, np.inf):
            continue
        for v, w, label, wid in edges[u]:
            nd = d + w
            if nd < dist.get(v, np.inf):
                dist[v], prev[v] = nd, (u, label, wid)
                heapq.heappush(heap, (nd, v))
    if goal not in dist:
        return None
    path, labels = [goal], []
    while path[-1] != start:
        u, label, wid = prev[path[-1]]
        labels.append((label, wid))
        path.append(u)
    return path[::-1], labels[::-1]


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    name = sys.argv[1]
    waypoints = [tuple(map(float, a.split(","))) for a in sys.argv[2:]]
    cfg = Config()
    coords, edges = load_graph(cfg)
    snapped = []
    for lat, lon in waypoints:
        nid, off = nearest(cfg, coords, lon, lat)
        snapped.append(nid)
        print(f"waypoint {lat:.5f},{lon:.5f} -> road node {nid} ({off:.0f} m away)")
    full, streets = [snapped[0]], []
    for a, b in zip(snapped[:-1], snapped[1:]):
        res = dijkstra(edges, a, b)
        if res is None:
            sys.exit(f"No road connection between nodes {a} and {b}")
        full += res[0][1:]
        streets += res[1]
    lonlat = np.array([coords[n] for n in full])
    x, y = cfg.lonlat_to_local(lonlat[:, 0], lonlat[:, 1])
    length = float(np.sum(np.hypot(np.diff(x), np.diff(y))))
    inside = bool(np.all((np.abs(x) < cfg.half) & (np.abs(y) < cfg.half)))
    print(f"Route '{name}': {length / 1000:.2f} km, {'entirely inside' if inside else 'LEAVES'} the map")
    seq = []
    for label, wid in streets:
        if not seq or seq[-1][0] != label:
            seq.append([label, wid])
    print("Streets: " + " -> ".join(s[0] for s in seq))
    out = OUTPUT / f"route_{name}.geojson"
    out.write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": lonlat.tolist()},
        "properties": {"name": name, "length_m": round(length, 1), "inside_map": inside,
                       "local_xy": np.column_stack([x, y]).round(2).tolist(),
                       "osm_way_ids": sorted({wid for _, wid in streets}),
                       "osm_nodes": [int(n) for n in full],
                       "edge_way_ids": [int(wid) for _, wid in streets],
                       "streets": [s[0] for s in seq]},
    }]}))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
