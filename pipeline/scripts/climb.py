#!/usr/bin/env python3
"""The hill-climb road as its own continuous road features.

OSM splits the climb into ~50 ways (street names change, side roads join), so build_osm.py
gives many short chains, each with its own smoothed profile pinned at its ends. Here the route
from route.py (output/route_climb.geojson, node by node) becomes a few long features instead:
one per stretch between bridges, plus one per bridge (straight deck). Every feature carries
"climb": true and "route_m0" (distance from the start line at its first node); the overlapping
pieces of the OSM chains are removed, side roads keep their ends on the climb so junctions
stay connected.

Run after build_osm.py, before trace_climb.py / road_profiles.py.
"""

import json

import numpy as np
from shapely.geometry import LineString

from build_osm import densify, road_profile
from common import DATA, OUTPUT, Config, Heightfield

ROUTE = "climb"
ON_ROUTE_M = 1.0       # an OSM node this close to the climb centreline belongs to the climb
MIN_KEEP_M = 3.0


def _arc(xy):
    return np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(xy, axis=0), axis=1))])


def climb_parts(cfg):
    """[(xy, bridge, way ids, names, route_m0)] along the route, split at bridges."""
    props = json.loads((OUTPUT / f"route_{ROUTE}.geojson").read_text())["features"][0]["properties"]
    xy = np.asarray(props["local_xy"], float)
    edge_ways = props["edge_way_ids"]
    raw = json.loads((DATA / "osm" / "raw.json").read_text())
    tags = {el["id"]: el.get("tags", {}) for el in raw["elements"] if el["type"] == "way"}
    bridge = np.array([tags[w].get("bridge", "no") != "no" for w in edge_ways])
    s = _arc(xy)
    parts, k = [], 0
    while k < len(edge_ways):
        j = k
        while j + 1 < len(edge_ways) and bridge[j + 1] == bridge[k]:
            j += 1
        ways = list(dict.fromkeys(edge_ways[k:j + 1]))
        names = list(dict.fromkeys(tags[w].get("name", "") for w in ways))
        parts.append((xy[k:j + 2], bool(bridge[k]), ways, names, float(s[k])))
        k = j + 1
    return parts, LineString(xy)


def strip_route(feats, route_line):
    """Remove the parts of OSM road features that lie on the climb."""
    out = []
    removed = 0.0
    for f in feats:
        c = np.asarray(f["geometry"]["coordinates"], float)
        line = LineString(c[:, :2])
        if line.distance(route_line) > ON_ROUTE_M:
            out.append(f)
            continue
        d = densify(c, 1.0)
        on = np.array([route_line.distance(_pt(p)) < ON_ROUTE_M for p in d[:, :2]])
        # a run of nodes on the climb longer than MIN_KEEP_M is the climb itself
        keep = np.ones(len(d), bool)
        i = 0
        while i < len(d):
            if on[i]:
                j = i
                while j + 1 < len(d) and on[j + 1]:
                    j += 1
                if _arc(d[i:j + 1, :2])[-1] >= MIN_KEEP_M:
                    keep[i + 1:j] = False          # the run's end nodes stay: junction points
                    if i == 0:
                        keep[i] = False
                    if j == len(d) - 1:
                        keep[j] = False
                i = j + 1
            else:
                i += 1
        if keep.all():
            out.append(f)
            continue
        idx = np.where(keep)[0]
        runs = np.split(idx, np.where(np.diff(idx) > 1)[0] + 1)
        for run in runs:
            seg = d[run]
            if len(seg) < 2 or _arc(seg[:, :2])[-1] < MIN_KEEP_M:
                continue
            g = dict(f, geometry={"type": "LineString", "coordinates": seg.round(3).tolist()},
                     properties=dict(f["properties"], length=round(float(_arc(seg[:, :2])[-1]), 1)))
            out.append(g)
        removed += line.length - sum(_arc(d[r][:, :2])[-1] for r in runs if len(r) > 1)
    return out, removed


def _pt(p):
    from shapely.geometry import Point
    return Point(p)


def main():
    cfg = Config()
    path = OUTPUT / "roads_local.geojson"
    gj = json.loads(path.read_text())
    feats = [f for f in gj["features"] if not f["properties"].get("climb")]
    hf = Heightfield(cfg, OUTPUT / "dem_natural.tif")
    parts, route_line = climb_parts(cfg)
    feats, removed = strip_route(feats, route_line)
    climb = []
    for n, (xy, bridge, ways, names, s0) in enumerate(parts):
        xy = densify(xy, 2.0)
        z = road_profile(xy, hf, 8.0, bridge)
        climb.append({"type": "Feature",
                      "geometry": {"type": "LineString", "coordinates": np.column_stack([xy, z]).round(3).tolist()},
                      "properties": {"id": 0, "osm_ids": ways, "highway": "tertiary", "group": "minor",
                                     "name": " / ".join(x for x in names if x) or None, "ref": None,
                                     "width": 4.0, "paved": True, "bridge": bridge, "tunnel": False,
                                     "layer": "1" if bridge else "0", "roundabout": False, "oneway": False,
                                     "length": round(float(_arc(xy)[-1]), 1), "climb": True, "climb_part": n,
                                     "route_m0": round(s0, 1)}})
    feats = climb + feats
    for k, f in enumerate(feats):
        f["properties"]["id"] = k
    gj["features"] = feats
    path.write_text(json.dumps(gj))
    print(f"Climb: {len(climb)} features ({sum(c['properties']['bridge'] for c in climb)} bridges), "
          f"{route_line.length / 1000:.2f} km; {removed / 1000:.2f} km of overlapping OSM chains removed")


if __name__ == "__main__":
    main()
