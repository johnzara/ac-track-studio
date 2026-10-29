#!/usr/bin/env python3
"""Add the Regione Veneto GeoDBT crop map to the land cover (ported from an earlier project's
build_osm.py): OSM maps few of the hill vineyards and orchards (along one reference ridge it calls
the vineyards beside the road "farmland"). Inside the config.json "detail_areas" with geodbt_crops,
GeoDBT vineyards (CL_AGR 01), orchards (02, 04) take the place of OSM fields and meadows; GeoDBT
meadows (05) and fields (07) only fill ground OSM leaves bare. Everything else OSM maps stays.

    geodbt_crops.py     updates output/landcover_local.geojson in place (re-runnable: earlier
                        GeoDBT features are replaced). Run after build_osm.py.
"""

import json
from collections import defaultdict

import shapely
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

import geodbt
from build_osm import LANDCOVER_ORDER
from common import OUTPUT, Config

GEODBT_CROPS = {"01": "vineyard", "02": "orchard", "04": "orchard", "05": "meadow", "07": "farmland"}
CROP_KIND = {"02": "fruit", "04": "olive"}


def main():
    cfg = Config()
    path = OUTPUT / "landcover_local.geojson"
    gj = json.loads(path.read_text())
    osm_feats = [f for f in gj["features"] if "geodbt" not in f["properties"]]
    region = geodbt.area_union(cfg, "geodbt_crops")
    if region is None:
        print("geodbt_crops: no detail area with geodbt_crops")
        return
    osm = [(shape(f["geometry"]), f["properties"]["class"]) for f in osm_feats]
    osm = [(g, c) for g, c in osm if g.intersects(region)]
    specific = unary_union([g for g, c in osm if c not in {"farmland", "meadow"}])
    anything = unary_union([g for g, _ in osm])
    feats = []
    for g, code in geodbt.read("CL_AGR", region, cfg):
        cls = GEODBT_CROPS.get(code)
        if cls is None:
            continue
        g = g.buffer(0).difference(specific if cls in {"vineyard", "orchard"} else anything)
        if g.is_empty or g.area < 50:
            continue
        props = {"class": cls, "geodbt": code, "priority": LANDCOVER_ORDER.index(cls)}
        if code in CROP_KIND:
            props["crop"] = CROP_KIND[code]
        feats.append({"type": "Feature", "geometry": mapping(shapely.set_precision(g, 0.01)), "properties": props})
    area = defaultdict(float)
    for f in feats:
        area[f["properties"].get("crop", f["properties"]["class"])] += shape(f["geometry"]).area
    gj["features"] = osm_feats + feats
    path.write_text(json.dumps(gj))
    print("GeoDBT crops added to the land cover: " + ", ".join(f"{k} {v / 1e4:.1f} ha" for k, v in sorted(area.items())))


if __name__ == "__main__":
    main()
