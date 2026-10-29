#!/usr/bin/env python3
"""Download the Regione Veneto GeoDBT (topographic database, ~2009-2010) "aree" layers for the
lots covering the map, and extract the building, volume and bridge layers.

Lots are listed in config.json ("geodbt_lots"); find the lot of a municipality with
getCartaTecnicaByComune on the geoportal (see README). The server does not support resuming,
so a failed download is retried from scratch. Licence: IODL 2.0, "Regione del Veneto – L.R. n. 28/76".
"""

import time
import urllib.parse
import urllib.request
import zipfile

from common import DATA, Config

URL = ("https://idt2.regione.veneto.it/idt/download/layerDownload/downloadGeoDBT"
       "?lotto={lot}&rif=FUSO12&type=aree")
KEEP = ("UN_VOL.", "EDIFC.", "EDIFC_CR_EDF_ME.", "EDIFC_EDIFC_USO.", "PONTE.",
        "H_EDIFC_CATEGORIA_USO.", "H_EDIFC_TIPOLOGIA_EDILIZIA.")


def download(lot, dest, attempts=6):
    url = URL.format(lot=urllib.parse.quote(lot))
    for i in range(attempts):
        try:
            tmp = dest.with_suffix(".part")
            with urllib.request.urlopen(url, timeout=600) as r, tmp.open("wb") as fh:
                expected = int(r.headers.get("X-Actual-Content-Lenght") or 0)
                while chunk := r.read(1 << 20):
                    fh.write(chunk)
            if zipfile.is_zipfile(tmp) and (not expected or tmp.stat().st_size >= expected):
                tmp.rename(dest)
                return
            print(f"  incomplete download ({tmp.stat().st_size} bytes), retrying")
        except OSError as exc:
            print(f"  attempt {i + 1} failed: {exc}")
        time.sleep(10)
    raise SystemExit(f"Could not download GeoDBT lot {lot}")


def main():
    cfg = Config()
    root = DATA / "geodbt"
    root.mkdir(parents=True, exist_ok=True)
    for lot, folder in cfg["geodbt_lots"].items():
        target = root / folder
        if target.exists() and any(target.rglob("UN_VOL.shp")):
            print(f"{lot}: already extracted")
            continue
        archive = root / f"{folder}_aree.zip"
        if not archive.exists():
            print(f"Downloading GeoDBT {lot} (a few hundred MB)...")
            download(lot, archive)
        with zipfile.ZipFile(archive) as z:
            members = [n for n in z.namelist() if n.rsplit("/", 1)[-1].startswith(KEEP)]
            z.extractall(target, members)
        print(f"{lot}: extracted {len(members)} files to {target}")


if __name__ == "__main__":
    main()
