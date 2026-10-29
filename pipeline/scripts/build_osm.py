#!/usr/bin/env python3
"""Turn the raw OSM download into features in local coordinates.

Input:  data/osm/raw.json, output/dem_natural.tif
Output (all local metres, see common.py for the frame):
  output/roads_local.geojson      drivable roads, merged into chains, with smoothed z profile
  output/buildings_local.geojson  footprints with base / top heights
  output/landcover_local.geojson  land-cover polygons with a `class` property
  output/water_local.geojson      waterway centre lines (streams, canals, ditches)
"""

import json
import re
from collections import defaultdict

import numpy as np
import shapely
from scipy.ndimage import gaussian_filter1d
from shapely.geometry import LineString, MultiPolygon, Polygon, box, mapping
from shapely.ops import linemerge, polygonize, unary_union

from common import DATA, OUTPUT, Config, Heightfield

MAJOR = {"motorway", "trunk", "primary", "secondary", "motorway_link", "trunk_link",
         "primary_link", "secondary_link"}
UNPAVED = {"unpaved", "gravel", "fine_gravel", "dirt", "ground", "grass", "compacted", "earth",
           "mud", "sand", "pebblestone", "rock", "woodchips"}
BUILDING_HEIGHTS = {
    "house": 7, "detached": 7, "residential": 8, "semidetached_house": 7, "terrace": 8,
    "apartments": 13, "industrial": 9, "warehouse": 9, "factory": 10, "manufacture": 10,
    "commercial": 10, "retail": 7, "office": 12, "school": 10, "hospital": 16,
    "church": 14, "chapel": 6, "cathedral": 20, "farm_auxiliary": 5, "barn": 6,
    "hut": 3, "shed": 3, "garage": 3, "garages": 3, "carport": 3, "roof": 4,
    "wayside_shrine": 2.5, "greenhouse": 3.5, "ruins": 3, "collapsed": 2,
    "service": 4, "transformer_tower": 8, "bell_tower": 25, "tower": 20,
}
LANDCOVER = [  # (class, predicate on tags); later classes win where polygons overlap
    ("farmland", lambda t: t.get("landuse") in {"farmland", "farmyard", "allotments"}),
    ("meadow", lambda t: t.get("landuse") in {"meadow", "grass", "village_green",
                                              "recreation_ground", "greenfield"}
     or t.get("natural") == "grassland" or t.get("leisure") in {"park", "garden", "pitch"}),
    ("orchard", lambda t: t.get("landuse") == "orchard"),
    ("vineyard", lambda t: t.get("landuse") == "vineyard"),
    ("scrub", lambda t: t.get("natural") in {"scrub", "heath"}
     or t.get("landuse") in {"wasteland", "brownfield"}),
    ("forest", lambda t: t.get("landuse") == "forest" or t.get("natural") == "wood"),
    ("urban", lambda t: t.get("landuse") in {"residential", "commercial", "retail", "construction",
                                             "cemetery", "religious", "railway", "garages"}
     or t.get("amenity") == "parking"),
    ("industrial", lambda t: t.get("landuse") == "industrial"),
    ("rock", lambda t: t.get("landuse") == "quarry"
     or t.get("natural") in {"bare_rock", "scree", "cliff"}),
    ("water", lambda t: t.get("natural") == "water" or t.get("waterway") == "riverbank"
     or t.get("landuse") in {"reservoir", "basin"}),
]
LANDCOVER_ORDER = [name for name, _ in LANDCOVER]


def parse_number(value):
    if value is None:
        return None
    m = re.match(r"^\s*([0-9]+(?:[.,][0-9]+)?)", str(value))
    return float(m.group(1).replace(",", ".")) if m else None


class Converter:
    def __init__(self, cfg):
        self.cfg = cfg

    def coords(self, geometry):
        lon = np.array([p["lon"] for p in geometry])
        lat = np.array([p["lat"] for p in geometry])
        x, y = self.cfg.lonlat_to_local(lon, lat)
        return np.column_stack([x, y])


# ---------------------------------------------------------------- roads

def road_key(tags):
    surface = tags.get("surface")
    paved = not (surface in UNPAVED or (tags.get("highway") == "track" and surface is None
                                        and tags.get("tracktype", "grade2") != "grade1"))
    return (tags["highway"], tags.get("width"), paved, tags.get("bridge", "no") != "no",
            tags.get("tunnel", "no") not in {"no", "building_passage"}, tags.get("layer", "0"))


PAVED = {"asphalt", "paved", "concrete", "paving_stones", "sett", "concrete:plates", "metal", "wood",
         "cobblestone", "unhewn_cobblestone", "chipseal"}


def is_trail(tags):
    """OSM paths worth driving off-road: path/bridleway unless paved, footway/cycleway only
    when tagged with an unpaved surface; never steps, areas or tunnels."""
    hw, surface = tags.get("highway"), tags.get("surface")
    if tags.get("area") == "yes" or tags.get("tunnel", "no") != "no" or hw == "steps":
        return False
    if surface in PAVED:
        return False
    if hw in {"path", "bridleway"}:
        return True
    return surface in UNPAVED


def merge_chains(ways):
    """Join ways end-to-end where exactly two compatible ways meet and nothing else touches
    the node. `ways` is a list of dicts with 'nodes', 'xy', 'key'."""
    touches = defaultdict(int)
    ends = defaultdict(list)
    for i, w in enumerate(ways):
        for nid in w["nodes"]:
            touches[nid] += 1
        ends[w["nodes"][0]].append(i)
        ends[w["nodes"][-1]].append(i)
    used = [False] * len(ways)
    chains = []

    def neighbour(node, current):
        cands = [j for j in ends[node] if j != current]
        if touches[node] != 2 or len(cands) != 1:
            return None
        j = cands[0]
        return j if not used[j] and ways[j]["key"] == ways[current]["key"] else None

    def oriented(j, start_node):
        w = ways[j]
        if w["nodes"][0] == start_node:
            return w["nodes"], w["xy"]
        return w["nodes"][::-1], w["xy"][::-1]

    for i, w in enumerate(ways):
        if used[i]:
            continue
        used[i] = True
        nodes, xy, ids = list(w["nodes"]), [w["xy"]], [w["id"]]
        for forward in (True, False):  # grow at the tail, then at the head
            cur = i
            while True:
                end_node = nodes[-1] if forward else nodes[0]
                j = neighbour(end_node, cur)
                if j is None or j == i:
                    break
                used[j] = True
                jn, jxy = oriented(j, end_node)
                if forward:
                    nodes += jn[1:]
                    xy.append(jxy[1:])
                else:
                    nodes = jn[::-1][:-1] + nodes
                    xy.insert(0, jxy[::-1][:-1])
                ids.append(ways[j]["id"])
                cur = j
        chains.append({"xy": np.vstack(xy), "ids": ids, "tags": w["tags"], "key": w["key"]})
    return chains


def densify(xy, spacing):
    """Keep every original vertex; insert evenly spaced points on segments longer than spacing."""
    out = [xy[0]]
    for a, b in zip(xy[:-1], xy[1:]):
        n = int(np.ceil(np.linalg.norm(b - a) / spacing))
        for k in range(1, n + 1):
            out.append(a + (b - a) * k / n)
    return np.array(out)


def arc_length(xy):
    return np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(xy, axis=0), axis=1))])


def road_profile(xy, hf, sigma_m, bridge):
    """z for each vertex of xy: terrain sampled every 2 m, smoothed along the road."""
    s = arc_length(xy)
    if s[-1] < 0.5:
        return hf.sample(xy[:, 0], xy[:, 1])
    fine_s = np.arange(0, s[-1] + 2, 2.0)
    fine_xy = np.column_stack([np.interp(fine_s, s, xy[:, 0]), np.interp(fine_s, s, xy[:, 1])])
    z = hf.sample(fine_xy[:, 0], fine_xy[:, 1])
    if bridge:
        z = np.linspace(z[0], z[-1], len(z))  # deck runs straight between the abutments
    else:
        z = gaussian_filter1d(z, sigma_m / 2.0, mode="nearest")
    return np.interp(s, fine_s, z)


def build_roads(cfg, elements, conv, hf):
    rcfg = cfg["roads"]
    drivable = set(rcfg["drivable_classes"])
    offroad = set(rcfg.get("offroad_classes", []))
    ways = []
    for el in elements:
        t = el.get("tags", {})
        if el["type"] != "way" or "geometry" not in el:
            continue
        hw = t.get("highway")
        if hw not in drivable and not (hw in offroad and is_trail(t)):
            continue
        if t.get("area") == "yes" or t.get("access") in {"private_no_motor"}:
            continue
        ways.append({"id": el["id"], "nodes": el["nodes"], "xy": conv.coords(el["geometry"]),
                     "tags": t, "key": road_key(t)})
    chains = merge_chains(ways)
    clip = box(-cfg.half + 2, -cfg.half + 2, cfg.half - 2, cfg.half - 2)
    feats = []
    for ch in chains:
        t = ch["tags"]
        hw, _, paved, bridge, tunnel, layer = ch["key"]
        width = parse_number(t.get("width"))
        lanes = parse_number(t.get("lanes"))
        if width is None:
            width = rcfg["default_widths_m"].get(hw, 4.0)
            if lanes and lanes >= 2 and hw not in {"track", "service"}:
                width = max(width, lanes * 3.0)
        # every public road is asphalt in the game (even where OSM says unpaved: a smooth,
        # consistent surface was asked for); only tracks, unpaved service ways and paths are dirt
        group = ("major" if hw in MAJOR else "trail" if hw in offroad
                 else "track" if (hw == "track" or (hw == "service" and not paved)) else "minor")
        geom = LineString(ch["xy"]).intersection(clip)
        parts = [geom] if geom.geom_type == "LineString" else list(getattr(geom, "geoms", []))
        for part in parts:
            if part.is_empty or part.length < 3:
                continue
            xy = np.asarray(part.simplify(0.25).coords)
            xy = densify(xy, rcfg["node_spacing_m"])
            z = road_profile(xy, hf, rcfg["profile_sigma_m"], bridge)
            feats.append({
                "type": "Feature",
                "geometry": {"type": "LineString",
                             "coordinates": np.column_stack([xy, z]).round(3).tolist()},
                "properties": {
                    "id": len(feats), "osm_ids": ch["ids"], "highway": hw, "group": group,
                    "name": t.get("name"), "ref": t.get("ref"), "width": round(width, 2), "lanes": lanes,
                    "paved": paved, "bridge": bridge, "tunnel": tunnel, "layer": layer,
                    "roundabout": t.get("junction") in {"roundabout", "circular"},
                    "oneway": t.get("oneway") in {"yes", "1", "-1"} or (
                        t.get("junction") in {"roundabout", "circular"} and t.get("oneway") != "no"), "length": round(part.length, 1),
                },
            })
    near = rcfg.get("tracks_near_route_m")
    route_file = OUTPUT / f"route_{rcfg.get('route', '')}.geojson"
    if near and route_file.exists():
        # far from the climb, tracks are not exported (they would only cost decals and load time)
        corridor = LineString(json.loads(route_file.read_text())["features"][0]["properties"]["local_xy"]).buffer(near)
        before = len(feats)
        feats = [f for f in feats if f["properties"]["group"] != "track"
                 or LineString(np.asarray(f["geometry"]["coordinates"])[:, :2]).intersects(corridor)]
        for k, f in enumerate(feats):
            f["properties"]["id"] = k
        print(f"  {before - len(feats)} tracks farther than {near:.0f} m from the route dropped")
    print(f"Roads: {len(ways)} OSM ways -> {len(chains)} chains -> {len(feats)} features, "
          f"{sum(f['properties']['length'] for f in feats) / 1000:.1f} km")
    return feats


# ---------------------------------------------------------------- polygons

def way_polygon(el, conv):
    xy = conv.coords(el["geometry"])
    if len(xy) < 4 or not np.allclose(xy[0], xy[-1]):
        return None
    poly = Polygon(xy)
    return poly if poly.is_valid else poly.buffer(0)


def relation_polygon(el, conv):
    outer, inner = [], []
    for m in el.get("members", []):
        if m["type"] != "way" or "geometry" not in m:
            continue
        line = LineString(conv.coords(m["geometry"]))
        (inner if m.get("role") == "inner" else outer).append(line)
    if not outer:
        return None
    shell = unary_union(list(polygonize(linemerge(outer))))
    if inner:
        shell = shell.difference(unary_union(list(polygonize(linemerge(inner)))))
    return None if shell.is_empty else shell


def polygons_of(el, conv):
    if el["type"] == "way" and "geometry" in el:
        return way_polygon(el, conv)
    if el["type"] == "relation":
        return relation_polygon(el, conv)
    return None


def building_height(tags, cfg):
    h = parse_number(tags.get("height"))
    if h:
        return h
    levels = parse_number(tags.get("building:levels"))
    if levels:
        return levels * cfg["buildings"]["level_height_m"] + 1.0
    return float(BUILDING_HEIGHTS.get(tags.get("building"), cfg["buildings"]["default_height_m"]))


def build_buildings(cfg, elements, conv, hf):
    area = box(-cfg.half, -cfg.half, cfg.half, cfg.half)
    feats = []
    for el in elements:
        t = el.get("tags", {})
        if "building" not in t or t["building"] in {"no", "construction"}:
            continue
        geom = polygons_of(el, conv)
        if geom is None or geom.is_empty or not area.contains(geom.centroid) or geom.area < 4:
            continue
        polys = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
        for poly in polys:
            ring = np.asarray(poly.exterior.coords)
            ground = hf.sample(ring[:, 0], ring[:, 1])
            height = building_height(t, cfg)
            z_base = float(ground.min()) - 0.5
            z_top = float(max(ground.min() + height, ground.max() + min(height, 2.5)))
            feats.append({
                "type": "Feature",
                "geometry": mapping(shapely.set_precision(poly, 0.01)),
                "properties": {"osm_id": el["id"], "kind": t["building"], "height": height,
                               "z_base": round(z_base, 2), "z_top": round(z_top, 2),
                               "name": t.get("name")},
            })
    print(f"Buildings: {len(feats)} footprints")
    return feats


def build_landcover(cfg, elements, conv):
    area = box(-cfg.half - 50, -cfg.half - 50, cfg.half + 50, cfg.half + 50)
    feats = []
    for el in elements:
        t = el.get("tags", {})
        cls = next((name for name, pred in reversed(LANDCOVER) if pred(t)), None)
        if cls is None or "building" in t:
            continue
        geom = polygons_of(el, conv)
        if geom is None or geom.is_empty:
            continue
        geom = geom.intersection(area)
        if geom.is_empty or geom.area < 10:
            continue
        feats.append({"type": "Feature", "geometry": mapping(shapely.set_precision(geom, 0.01)),
                      "properties": {"class": cls, "osm_id": el["id"],
                                     "priority": LANDCOVER_ORDER.index(cls)}})
    counts = defaultdict(int)
    for f in feats:
        counts[f["properties"]["class"]] += 1
    print(f"Land cover: {dict(counts)}")
    return feats


def build_water(cfg, elements, conv):
    clip = box(-cfg.half, -cfg.half, cfg.half, cfg.half)
    feats = []
    for el in elements:
        t = el.get("tags", {})
        if el["type"] != "way" or t.get("waterway") not in {"river", "stream", "canal", "ditch", "drain"}:
            continue
        geom = LineString(conv.coords(el["geometry"])).intersection(clip)
        if geom.is_empty:
            continue
        feats.append({"type": "Feature", "geometry": mapping(shapely.set_precision(geom, 0.01)),
                      "properties": {"waterway": t["waterway"], "name": t.get("name"),
                                     "tunnel": t.get("tunnel") == "culvert" or t.get("tunnel") == "yes"}})
    print(f"Waterways: {len(feats)}")
    return feats


def write(name, feats):
    path = OUTPUT / name
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    print(f"  -> {path} ({path.stat().st_size / 1e6:.1f} MB)")


def main():
    cfg = Config()
    raw = json.loads((DATA / "osm" / "raw.json").read_text())
    elements = raw["elements"]
    conv = Converter(cfg)
    hf = Heightfield(cfg, OUTPUT / "dem_natural.tif")
    write("roads_local.geojson", build_roads(cfg, elements, conv, hf))
    write("buildings_osm_local.geojson", build_buildings(cfg, elements, conv, hf))
    write("landcover_local.geojson", build_landcover(cfg, elements, conv))
    write("water_local.geojson", build_water(cfg, elements, conv))


if __name__ == "__main__":
    main()
