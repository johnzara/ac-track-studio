#!/usr/bin/env python3
"""Download elevation data covering the map area. Cached; safe to re-run.

Sources, best first (build_dem.py stacks them in this order):
1. Regione Veneto "DTM LiDAR 5M": 2 x 2 km ASCII grid tiles in "Fuso 12". Has holes.
   -> data/dem/veneto_lidar5/<tile>.asc
2. Regione Veneto "DTM5": 5 m grid per CTR 1:10000 sheet, derived from the regional
   technical map (contours/spot heights), Gauss-Boaga Fuso Ovest (EPSG:3003). Full coverage,
   bare ground, less detailed than LiDAR.
   -> data/dem/veneto_dtm5/<sheet>.tif
3. Copernicus GLO-30: 30 m surface model (includes trees/buildings), last-resort filler.
   -> data/dem/copernicus_glo30.tif

Both Veneto products come from the regional geoportal (idt2.regione.veneto.it) under IODL 2.0;
credit them as "Regione del Veneto – L.R. n. 28/76".
"""

import io
import json
import time
import urllib.request
import zipfile

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.windows import from_bounds

from common import DATA, Config, ensure_dirs

WFS = ("https://idt2-geoserver.regione.veneto.it/geoserver/ows?service=WFS&version=1.1.0"
       "&request=GetFeature&typeName={layer}&outputFormat=application/json"
       "&srsName=EPSG:4326&bbox={w},{s},{e},{n},EPSG:4326")
IDT = "https://idt2.regione.veneto.it/idt/download/layerDownload/"
LIDAR_URL = IDT + "downloadDtmLidar5?dataDtmLidarId={id}"
DTM5_LIST_URL = IDT + "getDtm5ByProvincia?idProvincia={prov}&formatoFile=ZIP"
DTM5_URL = IDT + "downloadDtm5?dataCtrDtmId={id}"
COPERNICUS = ("https://copernicus-dem-30m.s3.amazonaws.com/"
              "Copernicus_DSM_COG_10_N{lat:02d}_00_E{lon:03d}_00_DEM/"
              "Copernicus_DSM_COG_10_N{lat:02d}_00_E{lon:03d}_00_DEM.tif")

DEM_DIR = DATA / "dem"
LIDAR_DIR = DEM_DIR / "veneto_lidar5"
DTM5_DIR = DEM_DIR / "veneto_dtm5"
DTM5_CRS = CRS.from_epsg(3003)  # Monte Mario / Italy zone 1 (Gauss-Boaga Fuso Ovest)


def http_get(url, retries=4, timeout=180):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ac-track-studio/1.0 (private track builder)"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as exc:  # noqa: BLE001 - network errors of all kinds get retried
            if attempt == retries - 1:
                raise
            print(f"  retry {attempt + 1} after error: {exc}")
            time.sleep(2 ** attempt)


def wfs_features(cfg, layer, cache_name):
    cache = DEM_DIR / cache_name
    if not cache.exists():
        w, s, e, n = cfg.lonlat_bounds(cfg["dem"]["source_margin_m"])
        print(f"Querying WFS layer {layer}")
        cache.write_bytes(http_get(WFS.format(layer=layer, w=w, s=s, e=e, n=n)))
    return json.loads(cache.read_text())["features"]


def first_member(zip_bytes, suffixes):
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    name = next(n for n in z.namelist() if n.lower().endswith(suffixes))
    return z.read(name)


def fetch_lidar(cfg):
    LIDAR_DIR.mkdir(parents=True, exist_ok=True)
    feats = wfs_features(cfg, "rv:c0101071_lidar5m", "veneto_lidar5_index.json")
    tiles = {f["properties"]["nome"]: f["properties"]["id_pol"] for f in feats}
    for name, tile_id in sorted(tiles.items()):
        out = LIDAR_DIR / f"{name}.asc"
        if not out.exists():
            print(f"Downloading LiDAR tile {name}")
            out.write_bytes(first_member(http_get(LIDAR_URL.format(id=tile_id)), (".asc",)))
            time.sleep(0.3)
    print(f"Veneto LiDAR 5 m: {len(tiles)} tiles")


def fetch_dtm5(cfg):
    DTM5_DIR.mkdir(parents=True, exist_ok=True)
    feats = wfs_features(cfg, "rv:c0101021_ctr10000", "veneto_ctr10000_index.json")
    sheets = sorted(f["properties"]["a_codice"] for f in feats)
    ids = {}
    for prov in cfg["dem"]["veneto_provinces"]:
        cache = DEM_DIR / f"veneto_dtm5_list_{prov}.json"
        if not cache.exists():
            cache.write_bytes(http_get(DTM5_LIST_URL.format(prov=prov)))
        for row in json.loads(cache.read_text())["result"]["data"]:
            ids[row["a_codice"]] = row["dataCtrDtmId"]
    for sheet in sheets:
        out = DTM5_DIR / f"{sheet}.tif"
        if out.exists():
            continue
        if sheet not in ids:
            print(f"  DTM5 sheet {sheet} not offered for the configured provinces, skipping")
            continue
        print(f"Downloading DTM5 sheet {sheet}")
        raw = first_member(http_get(DTM5_URL.format(id=ids[sheet])), (".txt", ".asc"))
        with rasterio.MemoryFile(raw, ext=".asc") as mem, mem.open() as src:
            arr = src.read(1)
            profile = dict(driver="GTiff", width=src.width, height=src.height, count=1,
                           dtype=arr.dtype, crs=DTM5_CRS, transform=src.transform,
                           nodata=src.nodata, compress="deflate", predictor=3)
        with rasterio.open(out, "w", **profile) as dst:
            dst.write(arr, 1)
        time.sleep(0.3)
    print(f"Veneto DTM5: {len(sheets)} sheets")


def fetch_copernicus(cfg):
    out = DEM_DIR / "copernicus_glo30.tif"
    if out.exists():
        return
    lon0, lat0, lon1, lat1 = cfg.lonlat_bounds(cfg["dem"]["source_margin_m"])
    cells = {(int(np.floor(la)), int(np.floor(lo)))
             for la in (lat0, lat1) for lo in (lon0, lon1)}
    if len(cells) != 1:
        raise SystemExit("Map spans several 1-degree Copernicus tiles; mosaicking is not implemented")
    lat, lon = cells.pop()
    url = "/vsicurl/" + COPERNICUS.format(lat=lat, lon=lon)
    print("Reading Copernicus GLO-30 window")
    with rasterio.open(url) as src:
        win = from_bounds(lon0, lat0, lon1, lat1, src.transform).round_offsets().round_lengths()
        arr = src.read(1, window=win)
        profile = dict(driver="GTiff", width=arr.shape[1], height=arr.shape[0], count=1,
                       dtype=arr.dtype, crs=src.crs, transform=src.window_transform(win),
                       nodata=src.nodata, compress="deflate")
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(arr, 1)
    print(f"Copernicus GLO-30: {arr.shape[1]}x{arr.shape[0]} px")


def main():
    ensure_dirs()
    DEM_DIR.mkdir(exist_ok=True)
    cfg = Config()
    fetch_lidar(cfg)
    fetch_dtm5(cfg)
    fetch_copernicus(cfg)


if __name__ == "__main__":
    main()
