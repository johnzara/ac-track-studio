#!/usr/bin/env python3
"""Download Google Street View panoramas along a track's route into the build's private cache.

    python3 tools/streetview_fetch.py SLUG [options]             -> builds/SLUG/streetview/
    python3 tools/streetview_fetch.py --route FILE --out DIR     any route, any folder

    --spacing M      about one panorama every M metres of route (default 50; 0 = every panorama)
    --max-offset M   ignore panoramas further than this from the route line (default 12)
    --plan           only find the panoramas and print the count and size: download nothing
    --cache DIR      where the images are kept (default: the studio's cache/streetview/panos, one
                     copy of each panorama for every track; the build's panos/ only links to it)
    --reuse DIR      take panoramas already downloaded into DIR (…stm-<pano>.jpg) instead of
                     downloading them again; they are linked, not copied
    --offline        download nothing: index only the panoramas already here or in --reuse
    --keep-large     also keep the full-size image (16000 x 8000, ~80 MB each)
    --jobs N         downloads in parallel (default 3)
    --no-dates       skip the capture-date lookup (one extra request per panorama)

The route is builds/SLUG/output/route_climb.geojson (the scaffolded build) or, before scaffolding,
tracks/SLUG/route.geojson. Result, in the output folder:

  index.json        one entry per panorama: at_m (metres along the route, the survey's from_m /
                    to_m scale), offset from the route line (lateral_m: + right of it in the
                    driving direction), position, panorama id, capture date,
                    the car's heading / pitch / roll, the route's bearing there, the image files;
                    plus the stretches of route with no Street View coverage at all
  panos/<id>-stm.jpg  8192 x 4096 equirectangular (-sts 1300 x 650, -stl full size): relative
                    links into the shared cache, which keeps every panorama once
  coverage/         cached lookups

tools/streetview_views.py then cuts survey views and top-down road patches out of them.

This is the user's decision (docs/DATA_SOURCES.md 5.5): Google's terms do not allow bulk downloads
of Street View images, and these endpoints are unofficial and may change. The cache is a private
reference and texture source for the user's own tracks: never publish it or anything made from it.

The download itself is tools/streetview-dl/streeview-dl.php (public domain, from
github.com/fdd4s/streetview-dl, patched for 32 x 16 tile panoramas and failed downloads); it needs
php, aria2c, ImageMagick and exiftool on the PATH. This file is standard-library Python.
"""

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

STUDIO = Path(__file__).resolve().parent.parent
PHP_SCRIPT = Path(__file__).resolve().parent / "streetview-dl" / "streeview-dl.php"
SV_CACHE = STUDIO / "cache" / "streetview" / "panos"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/129.0 Safari/537.36")
COVERAGE_URL = "https://www.google.com/maps/photometa/ac/v1?pb=!1m1!1smaps_sv.tactile!6m3!1i{x}!2i{y}!3i17!8b1"
META_URL = ("https://www.google.com/maps/photometa/v1?authuser=0&hl=en&gl=us&pb=!1m4!1smaps_sv.tactile!11m2!2m1!1b1"
            "!2m2!1sen!2sus!3m3!1m2!1e2!2s{pano}!4m57!1e1!1e2!1e3!1e4!1e5!1e6!1e8!1e12!2m1!1e1!4m1!1i48!5m1!1e1"
            "!5m1!1e2!6m1!1e1!6m1!1e2!9m36!1m3!1e2!2b1!3e2!1m3!1e2!2b0!3e3!1m3!1e3!2b1!3e2!1m3!1e3!2b0!3e3"
            "!1m3!1e8!2b0!3e3!1m3!1e1!2b0!3e3!1m3!1e4!2b0!3e3!1m3!1e10!2b1!3e2!1m3!1e10!2b0!3e3")
MB_PER_PANO = {"stm": 24, "sts": 1, "stl": 80}
GAP_M = 75  # a stretch this long without any panorama counts as "no coverage"


def http_json(url, tries=4):
    """GET a Google maps endpoint whose JSON starts with the )]}' guard line."""
    for attempt in range(tries):
        try:
            with urlopen(Request(url, headers={"User-Agent": UA}), timeout=30) as r:
                text = r.read().decode("utf-8", "replace")
            return json.loads(text[4:] if text.startswith(")]}'") else text)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as e:
            if attempt == tries - 1:
                raise RuntimeError(f"{url[:70]}…: {e}") from e
            time.sleep(2 * (attempt + 1))


# ----------------------------------------------------------------------------------------- route
def load_route(path):
    """[(lon, lat), …] and the route's own length if it states one. Reads the pipeline's
    route_climb.geojson, the planner's route.geojson and an OSRM / openstreetmap.org route."""
    data = json.loads(Path(path).read_text())
    if "routes" in data:
        return [tuple(c[:2]) for c in data["routes"][0]["geometry"]["coordinates"]], data["routes"][0].get("distance")
    feats = data["features"] if data.get("type") == "FeatureCollection" else [data]
    for f in feats:
        if f.get("geometry", {}).get("type") == "LineString":
            return [tuple(c[:2]) for c in f["geometry"]["coordinates"]], (f.get("properties") or {}).get("length_m")
    sys.exit(f"{path}: no LineString found")


class Route:
    """The route in local metres, to measure how far along and how far off a point is."""

    def __init__(self, lonlat, stated_length=None):
        self.lat0 = math.radians(lonlat[0][1])
        self.pts = [self.xy(lon, lat) for lon, lat in lonlat]
        self.chain = [0.0]
        for a, b in zip(self.pts, self.pts[1:]):
            self.chain.append(self.chain[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
        # the pipeline measures in UTM: scale to its length so at_m matches the survey's metres
        self.scale = stated_length / self.chain[-1] if stated_length and self.chain[-1] > 0 else 1.0
        self.length = self.chain[-1] * self.scale
        self.lonlat = lonlat

    def xy(self, lon, lat):
        return lon * 111320.0 * math.cos(self.lat0), lat * 110574.0

    def locate(self, lon, lat):
        """(metres along the route, distance from the line, bearing of the route there, side:
        +1 right of the line in the driving direction, -1 left)."""
        px, py = self.xy(lon, lat)
        best = (math.inf, 0.0, 0.0, 1)
        for i in range(1, len(self.pts)):
            ax, ay = self.pts[i - 1]
            dx, dy = self.pts[i][0] - ax, self.pts[i][1] - ay
            len2 = dx * dx + dy * dy
            if len2 == 0:
                continue
            u = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / len2))
            d = math.hypot(px - (ax + u * dx), py - (ay + u * dy))
            if d < best[0]:
                best = (d, self.chain[i - 1] + u * math.sqrt(len2), math.degrees(math.atan2(dx, dy)) % 360,
                        -1 if dx * (py - ay) - dy * (px - ax) > 0 else 1)
        return best[1] * self.scale, best[0], best[2], best[3]

    def tiles(self, pad_m=40):
        """Zoom-17 map tiles the route touches, padded so panoramas just across an edge are found."""
        out = set()
        for i in range(1, len(self.lonlat)):
            steps = max(1, math.ceil((self.chain[i] - self.chain[i - 1]) / 15))
            for s in range(steps + 1):
                lon = self.lonlat[i - 1][0] + (self.lonlat[i][0] - self.lonlat[i - 1][0]) * s / steps
                lat = self.lonlat[i - 1][1] + (self.lonlat[i][1] - self.lonlat[i - 1][1]) * s / steps
                for ox, oy in ((0, 0), (pad_m, 0), (-pad_m, 0), (0, pad_m), (0, -pad_m)):
                    out.add(tile_of(lon + ox / (111320.0 * math.cos(self.lat0)), lat + oy / 110574.0))
        return sorted(out)


def tile_of(lon, lat):
    n = 1 << 17
    return (int((lon + 180) / 360 * n), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n))


# ------------------------------------------------------------------------------------- panoramas
def find_panos(route, cache, max_offset):
    """Every official Google panorama within max_offset of the route, in route order."""
    cache.mkdir(parents=True, exist_ok=True)
    tiles = route.tiles()
    found = {}
    for n, (x, y) in enumerate(tiles, 1):
        f = cache / f"tile_{x}_{y}.json"
        if not f.exists():
            f.write_text(json.dumps(http_json(COVERAGE_URL.format(x=x, y=y))))
            time.sleep(0.15)
            print(f"\r  coverage tiles {n}/{len(tiles)}", end="", file=sys.stderr, flush=True)
        data = json.loads(f.read_text())
        try:
            entries = data[1][1] or []
        except (IndexError, TypeError):
            entries = []
        for e in entries:
            try:
                kind, pano = e[0][0]
                lat, lon = e[0][2][0][2], e[0][2][0][3]
                hpr = (e[0][2][2] + [0, 0, 0])[:3] if len(e[0][2]) > 2 and e[0][2][2] else [None, 0, 0]
            except (IndexError, TypeError, ValueError):
                continue
            if kind != 2:  # 2 = Google's own imagery; user photo spheres have other sizes and ids
                continue
            found[pano] = (lon, lat, hpr)
    print(file=sys.stderr)
    panos = []
    for pano, (lon, lat, hpr) in found.items():
        at, off, bearing, side = route.locate(lon, lat)
        if off <= max_offset:
            panos.append({"at_m": round(at, 1), "offset_m": round(off, 1), "lateral_m": round(side * off, 1),
                          "lat": lat, "lon": lon, "pano": pano,
                          "heading_deg": None if hpr[0] is None else round(hpr[0], 2),
                          "pitch_deg": round(hpr[1] or 0, 2), "roll_deg": round(hpr[2] or 0, 2),
                          "route_bearing_deg": round(bearing, 1)})
    panos.sort(key=lambda p: p["at_m"])
    return panos, len(tiles), len(found)


def coverage_gaps(panos, length):
    gaps, prev = [], 0.0
    for at in [p["at_m"] for p in panos] + [length]:
        if at - prev > GAP_M:
            gaps.append({"from_m": round(prev), "to_m": round(at)})
        prev = at
    return gaps


def thin(panos, spacing, length, have):
    """About one panorama per `spacing` metres: for each target distance one already downloaded
    if there is one, else the one closest to it, preferring those nearer the route line (a side
    road's panorama can sit within max_offset)."""
    if spacing <= 0:
        return list(panos)
    chosen, used = [], set()
    for k in range(int(length // spacing) + 1):
        target = k * spacing
        near = [p for p in panos if abs(p["at_m"] - target) <= spacing / 2 and p["pano"] not in used]
        near = [p for p in near if p["pano"] in have] or near
        if near:
            best = min(near, key=lambda p: abs(p["at_m"] - target) + 2 * p["offset_m"])
            used.add(best["pano"])
            chosen.append(best)
    return sorted(chosen, key=lambda p: p["at_m"])


def add_dates(panos, cache):
    """Capture month and native size, from the panorama's own metadata (cached)."""
    f = cache / "meta.json"
    known = json.loads(f.read_text()) if f.exists() else {}
    todo = [p for p in panos if p["pano"] not in known]
    for n, p in enumerate(todo, 1):
        try:
            d = http_json(META_URL.format(pano=p["pano"]))[1][0]
            date = d[6][7]
            known[p["pano"]] = {"date": f"{date[0]}-{date[1]:02d}", "size": [d[2][2][1], d[2][2][0]]}
        except (RuntimeError, IndexError, TypeError):
            known[p["pano"]] = {"date": None, "size": None}
        print(f"\r  capture dates {n}/{len(todo)}", end="", file=sys.stderr)
        if n % 20 == 0:
            f.write_text(json.dumps(known))
        time.sleep(0.1)
    if todo:
        print(file=sys.stderr)
        f.write_text(json.dumps(known))
    for p in panos:
        p.update(known.get(p["pano"], {}))


# -------------------------------------------------------------------------------------- download
def image_size(path):
    r = subprocess.run(["magick", "identify", "-ping", "-format", "%w %h", str(path)], capture_output=True, text=True)
    try:
        w, h = r.stdout.split()
        return int(w), int(h)
    except ValueError:
        return None


def link_into(dst, target):
    """dst becomes a relative link to target (so the studio folder can move as a whole)."""
    if dst.is_symlink() or dst.exists():
        dst.unlink()
    dst.symlink_to(os.path.relpath(target, dst.parent))


def fetch_one(p, panos_dir, sizes, reuse, cache):
    """Make panos/<id>-<size>.jpg for one panorama, as links into the shared cache (downloading it
    there first if needed). Returns 'have', 'cached', 'reused', 'downloaded' or an error."""
    pano = p["pano"]
    want = {s: panos_dir / f"{pano}-{s}.jpg" for s in sizes}
    if all(f.exists() for f in want.values()):
        return "have"
    cached = {s: cache / f"{pano}-{s}.jpg" for s in sizes}
    if all(f.exists() for f in cached.values()):
        for s in sizes:
            link_into(want[s], cached[s])
        return "cached"
    if reuse:
        src = {s: next(iter(sorted(reuse.glob(f"*{s}-{pano}.jpg"))), None) for s in sizes}
        if all(src.values()):
            for s, f in want.items():
                if not (f.exists() or f.is_symlink()):
                    f.symlink_to(src[s].resolve())
            return "reused"
    made = {s: cache / f"{s}-{pano}.jpg" for s in ("stl", "stm", "sts")}
    err = "failed"
    for attempt in range(3):
        r = subprocess.run(["php", str(PHP_SCRIPT), pano], cwd=cache, capture_output=True, text=True)
        if r.returncode == 0 and image_size(made["stm"]) == (8192, 4096) and made["sts"].exists():
            subprocess.run(["exiftool", "-q", "-overwrite_original", f"-GPSLatitude={abs(p['lat'])}",
                            f"-GPSLatitudeRef={'N' if p['lat'] >= 0 else 'S'}", f"-GPSLongitude={abs(p['lon'])}",
                            f"-GPSLongitudeRef={'E' if p['lon'] >= 0 else 'W'}"] + [str(f) for f in made.values()],
                           capture_output=True)
            for s, f in made.items():
                if s in want:
                    f.replace(cached[s])
                    link_into(want[s], cached[s])
                else:
                    f.unlink(missing_ok=True)
            return "downloaded"
        err = (r.stdout.strip().splitlines() or ["failed"])[-1]
        for f in made.values():
            f.unlink(missing_ok=True)
        if "not found" in err:
            break
        time.sleep(20 * (attempt + 1))
    return f"ERROR {err}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug", nargs="?")
    ap.add_argument("--route", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--spacing", type=float, default=50)
    ap.add_argument("--max-offset", type=float, default=12)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--cache", type=Path, default=SV_CACHE)
    ap.add_argument("--reuse", type=Path)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--keep-large", action="store_true")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--no-dates", action="store_true")
    a = ap.parse_args()

    if a.slug:
        build = STUDIO / "builds" / a.slug
        candidates = [build / "output" / "route_climb.geojson", STUDIO / "tracks" / a.slug / "route.geojson"]
        route_path = a.route or next((c for c in candidates if c.exists()), None)
        if route_path is None:
            sys.exit(f"no route for {a.slug}: expected one of {', '.join(str(c) for c in candidates)}")
        out = a.out or build / "streetview"
    elif a.route and a.out:
        route_path, out = a.route, a.out
    else:
        ap.error("give a SLUG, or --route and --out")
    if a.reuse and not a.reuse.is_dir():
        sys.exit(f"--reuse {a.reuse}: not a folder")

    route = Route(*load_route(route_path))
    print(f"route {route_path}: {route.length / 1000:.2f} km")
    panos, n_tiles, n_seen = find_panos(route, out / "coverage", a.max_offset)
    gaps, n_near = coverage_gaps(panos, route.length), len(panos)
    panos_dir = out / "panos"
    have = {f.name[:-len("-stm.jpg")] for f in panos_dir.glob("*-stm.jpg")}
    have |= {f.name[:-len("-stm.jpg")] for f in a.cache.glob("*-stm.jpg")}
    if a.reuse:
        have |= {f.stem.split("stm-", 1)[1] for f in a.reuse.glob("*stm-*.jpg")}
    if a.offline:
        panos = [p for p in panos if p["pano"] in have]
    chosen = thin(panos, a.spacing, route.length, have)
    for n, p in enumerate(chosen, 1):
        p["seq"] = n
    sizes = ["stm", "sts"] + (["stl"] if a.keep_large else [])
    new = sum(1 for p in chosen if p["pano"] not in have)
    print(f"{len(panos)} panoramas {'already downloaded ' if a.offline else ''}within {a.max_offset:g} m of the route "
          f"({n_seen} in {n_tiles} map tiles); {len(chosen)} selected at {a.spacing:g} m spacing, {new} to download")
    print("no Street View coverage: " + (", ".join(f"{g['from_m']}–{g['to_m']} m" for g in gaps) if gaps else "none"))
    print(f"download: about {new * sum(MB_PER_PANO[s] for s in sizes) / 1000:.1f} GB kept, "
          f"{new * (512 + 1)} requests to Google")
    if a.plan:
        return

    missing = [t for t in ("php", "aria2c", "magick", "montage", "exiftool") if not shutil.which(t)]
    if missing and new:
        sys.exit(f"missing on the PATH: {', '.join(missing)} (brew install php aria2 imagemagick exiftool)")
    if not a.no_dates:
        add_dates(chosen, out / "coverage")
    panos_dir.mkdir(parents=True, exist_ok=True)
    a.cache.mkdir(parents=True, exist_ok=True)

    counts, done = {}, 0

    def work(p):
        return p, fetch_one(p, panos_dir, sizes, a.reuse, a.cache)

    with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as pool:
        for p, result in pool.map(work, chosen):
            done += 1
            key = result.split()[0]
            counts[key] = counts.get(key, 0) + 1
            p["status"] = "ok" if key != "ERROR" else result
            p["files"] = {s: f"panos/{p['pano']}-{s}.jpg" for s in sizes} if key != "ERROR" else {}
            if key == "ERROR":
                print(f"\n  {p['pano']} at {p['at_m']:.0f} m: {result}", file=sys.stderr)
            print(f"\r  panoramas {done}/{len(chosen)}", end="", file=sys.stderr)
    print(file=sys.stderr)

    index = {
        "schema": "ac-track-studio/streetview@1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "notice": "Google Street View imagery, downloaded by the user's decision outside Google's terms: "
                  "private reference and texture source only, never publish it or anything made from it.",
        "route": {"source": str(route_path), "length_m": round(route.length, 1)},
        "spacing_m": a.spacing, "max_offset_m": a.max_offset,
        "panoramas_near_route": n_near, "no_coverage": gaps,
        "panos": chosen,
    }
    (out / "index.json").write_text(json.dumps(index, indent=1) + "\n")
    print(f"{out / 'index.json'}: " + ", ".join(f"{v} {k.lower()}" for k, v in sorted(counts.items())))
    if counts.get("ERROR"):
        sys.exit(1)


if __name__ == "__main__":
    main()
