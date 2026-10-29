"""Regione Veneto GeoDBT area layers beyond buildings, in local coordinates, for the areas
built with extra detail (config.json "detail_areas"):

  CL_AGR  crops: 01 vineyards, 02 orchards (here mostly cherries), 04 olive groves,
          05 meadows, 07 arable land, 08 vegetable gardens
  AC_VEI  vehicle traffic areas by zone (AC_VEI_ZON): 01xx carriageway (the asphalt between
          the edge lines; 0105 alleys), 0204 squares, 0205 junctions, 0206 roundabouts (the
          whole roundabout, islands as holes), 0301 car parks, 0307 yards and private roads
          inside a property, 05 lay-bys (Verona also 04 parking aisles, 07 roadside strips)

The lots use two schemas: Vicenza Sud keeps geometry and attributes apart
(<L>_<L>_SUP.shp CLASSREF -> <L>.dbf CLASSID; AC_VEI's zone lives on its sub-regions,
<L>_<L>_SUP_SR.shp), Bim Adige (Verona) has flat shapefiles.
Files come from fetch_geodbt.py (data/geodbt/<lot>/...); coordinates are Veneto Fuso 12.
"""

import shapely
from shapely.geometry import box

from common import DATA, VENETO_FUSO12, Config

TYPE_FIELD = {"CL_AGR": "CL_AGR_TY", "AC_VEI": "AC_VEI_ZON"}


def areas(cfg=None, flag=None):
    """config "detail_areas" entries (optionally only those with `flag` set)."""
    cfg = cfg or Config()
    out = cfg.raw.get("detail_areas", {}).get("areas", [])
    return [a for a in out if flag is None or a.get(flag)]


def area_union(cfg=None, flag=None):
    """Union of the detail-area boxes (local metres), or None."""
    boxes = [box(*a["bounds"]) for a in areas(cfg, flag)]
    return shapely.union_all(boxes) if boxes else None


def _files(layer):
    """[(geometry shapefile, attribute table or None)] for every lot that has the layer."""
    out = []
    for lot in sorted((DATA / "geodbt").iterdir()):
        if not lot.is_dir():
            continue
        split = sorted(lot.rglob(f"{layer}_{layer}_SUP.shp")) or sorted(lot.rglob(f"{layer}_{layer}_SUP_SR.shp"))
        if split:
            dbf = split[0].with_name(f"{layer}.dbf")
            out.append((split[0], dbf if dbf.exists() else None))
            continue
        flat = sorted(p for p in lot.rglob(f"{layer}.shp"))
        if flat:
            out.append((flat[0], None))
    return out


def read(layer, region, cfg=None):
    """[(local geometry, type code or None)] of a GeoDBT area layer within `region` (local
    shapely geometry)."""
    from pyogrio.raw import read as ogr_read
    from pyproj import Transformer
    import numpy as np
    import warnings
    cfg = cfg or Config()
    to_f = Transformer.from_crs(cfg.crs, VENETO_FUSO12, always_xy=True)
    from_f = Transformer.from_crs(VENETO_FUSO12, cfg.crs, always_xy=True)
    x0, y0, x1, y1 = region.bounds
    X, Y = to_f.transform(np.array([x0, x1, x0, x1]) + cfg.center_e, np.array([y0, y0, y1, y1]) + cfg.center_n)
    bbox = (float(X.min()), float(Y.min()), float(X.max()), float(Y.max()))

    def local(g):
        g = shapely.force_2d(g)
        return shapely.transform(g, lambda P: np.column_stack(from_f.transform(P[:, 0], P[:, 1]))
                                 - [cfg.center_e, cfg.center_n])

    field = TYPE_FIELD.get(layer)
    out = []
    for shp, dbf in _files(layer):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")          # "Measured 3D Polygon" read as Polygon Z
            meta, _, geom, f = ogr_read(shp, bbox=bbox)
            fields = list(meta["fields"])
            if field and field in fields:            # on the geometry itself (Verona, sub-regions)
                codes = list(f[fields.index(field)])
            elif dbf is not None and "CLASSREF" in fields:   # Vicenza: CLASSREF -> CLASSID in <L>.dbf
                types = {}
                if field:
                    _, _, _, t = ogr_read(dbf, columns=["CLASSID", field], read_geometry=False)
                    types = dict(zip(t[0], t[1]))
                codes = [types.get(r) for r in f[fields.index("CLASSREF")]]
            else:
                codes = [None] * len(geom)
        for g, code in zip(shapely.from_wkb(geom), codes):
            if g is None:
                continue
            g = local(g)
            if not g.is_valid:
                g = g.buffer(0)
            g = g.intersection(region)
            if not g.is_empty:
                out.append((g, code))
    return out
