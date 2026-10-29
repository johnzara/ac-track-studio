#!/usr/bin/env python3
"""Assemble the building set: real volumes and heights from the Veneto GeoDBT, plus OSM for
anything newer, classified into local building styles and merged into continuous blocks.

Sources:
  data/geodbt/<lot>/**/UN_VOL.shp   building volumes with surveyed eave height (UN_VOL_AV)
  data/geodbt/<lot>/**/EDIFC*.shp   building outlines with use / typology / name
  output/buildings_osm_local.geojson (from build_osm.py)
Output:
  output/buildings_local.geojson    one feature per block: outline, eave/base z, roof type,
                                    and member volumes with their wall material
"""

import glob
import hashlib
import json
from collections import defaultdict

import numpy as np
import shapely
from pyogrio.raw import read
from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import unary_union

import sites
from common import DATA, OUTPUT, VENETO_FUSO12, Config, Heightfield, basalt_west_x
from textures import PLASTER

COLOUR_WEIGHTS = {"cream": 25, "white": 18, "ochre": 15, "yellow": 12, "salmon": 12, "pink": 10,
                  "terracotta": 8}
SHUTTER_WEIGHTS = {"brown": 45, "green": 40, "grey": 15}
MERGE_DH = 0.7          # max eave-height difference (m) for merging touching volumes
MERGE_DGROUND = 1.0     # max ground-level difference (m) for merging
MAX_PITCHED_WIDTH = 22  # wider blocks get flat roofs
ROAD_CLEAR_M = 0.4

WEST_X = basalt_west_x()   # local x of config.json "basalt_west_utm_e"
MILITARY = sites.military_area()   # buildings here get the "military" style


def pick(weights, key):
    h = int(hashlib.md5(str(key).encode()).hexdigest(), 16)
    total = sum(weights.values())
    r = h % total
    for name, w in weights.items():
        r -= w
        if r < 0:
            return name
    return next(iter(weights))


def load_lot(lot_dir, bbox, to_local):
    """Volumes [(polygon, height, attrs)] from one GeoDBT lot, clipped to bbox (Fuso 12)."""
    unvol = glob.glob(str(lot_dir / "**" / "UN_VOL.shp"), recursive=True)
    if not unvol:
        return edifc_with_heights(lot_dir, bbox, to_local)
    meta, _, geom, fields = read(unvol[0], bbox=bbox)
    names = list(meta["fields"])
    heights = np.asarray(fields[names.index("UN_VOL_AV")], float)
    vols = [to_local(shapely.force_2d(g)) for g in shapely.from_wkb(geom)]

    # building outlines + attributes (two schema variants exist)
    attrs_by_outline, outlines = [], []
    edifc = glob.glob(str(lot_dir / "**" / "EDIFC.shp"), recursive=True)
    if edifc:
        m, _, g, f = read(edifc[0], bbox=bbox)
        n = list(m["fields"])
        for i, gg in enumerate(shapely.from_wkb(g)):
            outlines.append(to_local(shapely.force_2d(gg)))
            attrs_by_outline.append({"use": f[n.index("EDIFC_USO")][i], "ty": f[n.index("EDIFC_TY")][i],
                                     "name": f[n.index("EDIFC_NOM")][i], "bid": f[n.index("ID")][i]})
    else:
        cr = glob.glob(str(lot_dir / "**" / "EDIFC_CR_EDF_ME.shp"), recursive=True)
        tab = glob.glob(str(lot_dir / "**" / "EDIFC.dbf"), recursive=True)
        uso = glob.glob(str(lot_dir / "**" / "EDIFC_EDIFC_USO.dbf"), recursive=True)
        if cr and tab:
            mt, _, _, ft = read(tab[0])
            nt = list(mt["fields"])
            ty = dict(zip(ft[nt.index("CLASSID")], ft[nt.index("EDIFC_TY")]))
            use = {}
            if uso:
                mu, _, _, fu = read(uso[0])
                nu = list(mu["fields"])
                use = dict(zip(fu[nu.index("CLASSREF")], fu[nu.index("EDIFC_USO")]))
            m, _, g, f = read(cr[0], bbox=bbox)
            refs = f[list(m["fields"]).index("CLASSREF")]
            for gg, ref in zip(shapely.from_wkb(g), refs):
                outlines.append(to_local(shapely.force_2d(gg)))
                attrs_by_outline.append({"use": use.get(ref), "ty": ty.get(ref), "name": None, "bid": ref})
    tree = STRtree(outlines) if outlines else None
    out = []
    for i, (poly, h) in enumerate(zip(vols, heights)):
        if poly.is_empty or h <= 0.3 or poly.area < 2:
            continue
        attrs = {"use": None, "ty": None, "name": None, "bid": f"{lot_dir.name}_v{i}"}
        if tree is not None:
            p = poly.representative_point()
            hits = [j for j in tree.query(p) if outlines[j].contains(p)]
            if hits:
                attrs = dict(attrs_by_outline[hits[0]])
        out.append((poly, float(h), attrs))
    return out


def edifc_with_heights(lot_dir, bbox, to_local):
    """Older lots (e.g. from 2005) have no UN_VOL volumes; their building outlines
    carry the surveyed eave altitude EDIFC_QGRO and base altitude EDIFC_QBAS instead."""
    edifc = glob.glob(str(lot_dir / "**" / "EDIFC.shp"), recursive=True)
    if not edifc:
        return []
    m, _, g, f = read(edifc[0], bbox=bbox)
    n = list(m["fields"])
    if "EDIFC_QGRO" not in n:
        return []
    q = np.asarray(f[n.index("EDIFC_QGRO")], float)
    b = np.asarray(f[n.index("EDIFC_QBAS")], float)
    out = []
    for i, gg in enumerate(shapely.from_wkb(g)):
        h = q[i] - b[i]
        if not (1.5 <= h <= 40) or f[n.index("EDIFC_STAT")][i] == "01":     # 01: under construction
            continue
        poly = to_local(shapely.force_2d(gg))
        if poly.is_empty or poly.area < 2:
            continue
        out.append((poly, float(h), {"use": f[n.index("EDIFC_USO")][i], "ty": f[n.index("EDIFC_TY")][i],
                                     "name": f[n.index("EDIFC_NOM")][i], "bid": f"{lot_dir.name}_{f[n.index('ID')][i]}"}))
    return out


def osm_volumes(existing_union_tree, existing):
    """OSM buildings that the (older) GeoDBT does not already contain."""
    feats = json.loads((OUTPUT / "buildings_osm_local.geojson").read_text())["features"]
    use_of = {"industrial": "08", "warehouse": "08", "factory": "08", "manufacture": "08",
              "commercial": "07", "retail": "07", "church": "05", "chapel": "05",
              "farm_auxiliary": "09", "barn": "09", "school": "0303", "hospital": "0301"}
    out = []
    for f in feats:
        p = f["properties"]
        if p["kind"] in {"roof", "collapsed", "ruins", "construction"}:
            continue
        poly = shape(f["geometry"])
        covered = sum(poly.intersection(existing[j]).area for j in existing_union_tree.query(poly))
        if covered / poly.area > 0.3:
            continue
        ty = "07" if p["kind"] in {"bell_tower"} else "11" if p["kind"] == "church" else None
        out.append((poly, float(p["height"]),
                    {"use": use_of.get(p["kind"], "01"), "ty": ty, "name": p.get("name"),
                     "bid": f"osm{p['osm_id']}"}))
    return out


def style_of(poly, h, attrs, x):
    use, ty = attrs.get("use") or "01", attrs.get("ty") or "01"
    bid = attrs.get("bid")
    if MILITARY is not None and MILITARY.contains(poly.centroid):
        return "military", "bld_military"           # config.json sites.military_base
    colour = pick(COLOUR_WEIGHTS, bid)
    shutter = pick(SHUTTER_WEIGHTS, f"{bid}s")
    if ty == "07":
        return "tower", "bld_campanile" if h >= 8 else "bld_brick"
    if ty in {"11", "22"} or use == "05":
        return "church", "bld_stone_ashlar" if pick({"a": 1, "b": 1}, bid) == "a" else f"bld_plain_{colour}"
    if ty == "08" or use.startswith("08") or use.startswith("06"):
        return "industrial", "bld_ind_" + pick({"grey": 2, "beige": 1, "white": 1}, bid)
    if use.startswith("09"):
        if h < 3.2:
            return "annex", "bld_stone_rubble" if x < WEST_X else f"bld_plain_{colour}"
        stone = "bld_stone_basalt" if x < WEST_X and pick({"a": 1, "b": 2}, bid) == "a" else "bld_stone_rubble"
        return "rural", pick({stone: 2, f"bld_plain_{colour}": 1, "bld_plain_terracotta": 1}, bid)
    if use.startswith("07") or use.startswith("12"):
        return "commercial", f"bld_shop_{colour}" if h >= 5.5 else f"bld_plain_{colour}"
    if h < 2.8:
        return "annex", f"bld_plain_{colour}"
    return "residential", f"bld_res_{colour}_{shutter}"


def main():
    cfg = Config()
    hf = Heightfield(cfg, OUTPUT / "dem_final.tif")
    to_fuso = Transformer.from_crs(cfg.crs, VENETO_FUSO12, always_xy=True)
    from_fuso = Transformer.from_crs(VENETO_FUSO12, cfg.crs, always_xy=True)
    w, s, e, n = cfg.utm_bounds(20)
    fx, fy = to_fuso.transform([w, e, w, e], [s, s, n, n])
    bbox = (min(fx), min(fy), max(fx), max(fy))

    def to_local(g):
        return shapely.transform(g, lambda c: np.column_stack(
            from_fuso.transform(c[:, 0], c[:, 1])) - [cfg.center_e, cfg.center_n])

    vols = []
    for lot in sorted((DATA / "geodbt").iterdir()):
        if lot.is_dir():
            got = load_lot(lot, bbox, to_local)
            print(f"GeoDBT {lot.name}: {len(got)} volumes")
            vols += got
    area = shapely.box(-cfg.half + 1, -cfg.half + 1, cfg.half - 1, cfg.half - 1)
    vols = [v for v in vols if area.contains(v[0].representative_point())]
    polys = [v[0] for v in vols]
    extra = osm_volumes(STRtree(polys), polys) if polys else []
    print(f"OSM buildings not in GeoDBT: {len(extra)}")
    vols += [v for v in extra if area.contains(v[0].representative_point())]

    # nothing stands on a carriageway: footprints are trimmed back to ROAD_CLEAR_M from the
    # asphalt of paved roads (2005-2010 outlines, traced roads and terrace houses on narrow
    # village streets disagree by up to ~1 m)
    roads = json.loads((OUTPUT / "roads_local.geojson").read_text())["features"]
    from common import OFFROAD, road_polygon
    paved = unary_union([road_polygon(f, ROAD_CLEAR_M) for f in roads
                         if f["properties"]["group"] not in OFFROAD and not f["properties"]["tunnel"]
                         and not f["properties"]["bridge"]])
    ptree = STRtree(list(getattr(paved, "geoms", [paved])))
    trimmed, dropped = 0, 0
    kept = []
    for poly, h, attrs in vols:
        if len(ptree.query(poly, predicate="intersects")):
            cut = poly.difference(paved)
            if cut.is_empty or cut.area < 0.5 * poly.area or cut.area < 4:
                dropped += 1
                continue
            if cut.geom_type == "MultiPolygon":
                cut = max(cut.geoms, key=lambda g: g.area)
            poly = cut
            trimmed += 1
        kept.append((poly, h, attrs))
    vols = kept
    print(f"Buildings on carriageways: {trimmed} trimmed, {dropped} dropped")

    # per-volume ground, eave, style
    recs = []
    for poly, h, attrs in vols:
        poly = poly.buffer(0) if not poly.is_valid else poly
        if poly.is_empty:
            continue
        ring = np.vstack([np.asarray(g.exterior.coords) for g in getattr(poly, "geoms", [poly])])
        g = hf.sample(ring[:, 0], ring[:, 1])
        style, wall = style_of(poly, h, attrs, float(poly.centroid.x))
        g0, g1 = float(g.min()), float(g.max())
        eave = max(g0 + h, g1 + min(h, 1.8))
        recs.append({"poly": poly, "h": h, "attrs": attrs, "style": style, "wall": wall,
                     "g0": g0, "eave": eave})

    # merge touching volumes of the same style, similar eave and ground level
    polys = [r["poly"] for r in recs]
    tree = STRtree(polys)
    parent = list(range(len(recs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, r in enumerate(recs):
        if r["style"] in {"tower"}:
            continue
        for j in tree.query(r["poly"].buffer(0.05)):
            if j <= i:
                continue
            o = recs[j]
            if (o["style"] == r["style"] and abs(o["eave"] - r["eave"]) <= MERGE_DH
                    and abs(o["g0"] - r["g0"]) <= MERGE_DGROUND
                    and r["poly"].distance(o["poly"]) < 0.05):
                parent[find(i)] = find(j)
    groups = defaultdict(list)
    for i in range(len(recs)):
        groups[find(i)].append(recs[i])

    feats = []
    for members in groups.values():
        outline = unary_union([m["poly"].buffer(0.03, join_style="mitre") for m in members]).buffer(
            -0.03, join_style="mitre")
        outline = shapely.set_precision(outline, 0.01)
        if outline.is_empty:
            continue
        areas = np.array([m["poly"].area for m in members])
        eave = float(np.average([m["eave"] for m in members], weights=areas))
        g0 = min(m["g0"] for m in members)
        style = members[0]["style"]
        rect = outline.minimum_rotated_rectangle if outline.area > 0 else outline
        coords = np.asarray(rect.exterior.coords) if isinstance(rect, Polygon) else np.zeros((3, 2))
        width = float(min(np.hypot(*np.diff(coords[:3], axis=0).T))) if len(coords) >= 3 else 0
        if style == "tower":
            roof = "pyramid"
        elif style in {"industrial", "military"} or width > MAX_PITCHED_WIDTH:
            roof = "flat"
        else:
            roof = "hip"
        roof_mat = {"industrial": "roof_metal"}.get(style, "roof_coppi" if roof != "flat" else "roof_flat")
        if style == "rural" and roof != "flat":
            roof_mat = "roof_coppi_dark"
        name = next((m["attrs"].get("name") for m in members if m["attrs"].get("name")), None)
        feats.append({
            "type": "Feature", "geometry": mapping(outline),
            "properties": {
                "style": style, "roof": roof, "roof_mat": roof_mat, "eave_z": round(eave, 2),
                "ground_z": round(g0, 2), "base_z": round(g0 - 0.5, 2), "name": name,
                "members": [{"wall": m["wall"], "geometry": mapping(shapely.set_precision(m["poly"], 0.01))}
                            for m in members],
            },
        })
    counts = defaultdict(int)
    for f in feats:
        counts[f["properties"]["style"]] += 1
    out = OUTPUT / "buildings_local.geojson"
    out.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    print(f"Buildings: {len(recs)} volumes -> {len(feats)} blocks {dict(counts)} -> {out}")


if __name__ == "__main__":
    main()
