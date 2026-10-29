"""Shared configuration and coordinate helpers for the track pipeline.

Coordinate frames
-----------------
* UTM: config.json "crs" (e.g. EPSG:32632, WGS 84 / UTM 32N), metres.
* Local frame: metres, origin at the map centre, +X east, +Y north, +Z up.
  local_x = utm_e - center_e, local_y = utm_n - center_n, z = altitude above sea level.
  Assetto Corsa: (x, y, z) -> (x, z, -y).
* Terrain grid (dem_*.tif, terrain layers): N x N samples. Sample (i, j) sits at
  local (-half + i * s, -half + j * s) with i west->east, j south->north.
  Rasters are written north-up, so row r holds grid row j = N - 1 - r.
"""

import json
from functools import cached_property
from pathlib import Path

import numpy as np
from pyproj import Transformer

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUTPUT = ROOT / "output"

# Regione Veneto "Fuso 12": transverse Mercator, central meridian 12 E, scale 1,
# false easting 3,000,000 (verified against Copernicus GLO-30, median diff ~2.4 m).
VENETO_FUSO12 = ("+proj=tmerc +lat_0=0 +lon_0=12 +k=1 +x_0=3000000 +y_0=0 "
                 "+ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs")


class Config:
    def __init__(self, path=ROOT / "config.json"):
        self.raw = json.loads(Path(path).read_text())
        self.crs = self.raw["crs"]
        self.center_e, self.center_n = map(float, self.raw["center_utm"])
        self.size_px = int(self.raw["terrain_size_px"])
        self.mpp = float(self.raw["meters_per_px"])
        if self.size_px & (self.size_px - 1):
            raise ValueError("terrain_size_px must be a power of two")

    def __getitem__(self, key):
        return self.raw[key]

    @property
    def half(self):
        """Half the terrain extent in metres (the terrain covers [-half, half))."""
        return self.size_px * self.mpp / 2

    def utm_bounds(self, margin=0.0):
        """(west, south, east, north) of the terrain in UTM, plus an optional margin."""
        h = self.half + margin
        return (self.center_e - h, self.center_n - h, self.center_e + h, self.center_n + h)

    def grid_axes(self):
        """Local x (west->east) and y (south->north) coordinates of the heightmap samples."""
        idx = np.arange(self.size_px) * self.mpp - self.half
        return idx, idx.copy()

    @cached_property
    def to_wgs84(self):
        return Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True)

    @cached_property
    def from_wgs84(self):
        return Transformer.from_crs("EPSG:4326", self.crs, always_xy=True)

    def lonlat_bounds(self, margin=0.0):
        """Lon/lat box that fully contains the (margin-expanded) terrain square."""
        w, s, e, n = self.utm_bounds(margin)
        xs = np.array([w, e, w, e, (w + e) / 2, (w + e) / 2])
        ys = np.array([s, s, n, n, s, n])
        lon, lat = self.to_wgs84.transform(xs, ys)
        return float(lon.min()), float(lat.min()), float(lon.max()), float(lat.max())

    def lonlat_to_local(self, lon, lat):
        e, n = self.from_wgs84.transform(np.asarray(lon), np.asarray(lat))
        return e - self.center_e, n - self.center_n


class Heightfield:
    """A heightmap-grid raster (north-up GeoTIFF from build_dem) with bilinear sampling in
    local coordinates."""

    def __init__(self, cfg, path):
        import rasterio
        with rasterio.open(path) as src:
            self.z = src.read(1).astype(np.float32)
        self.cfg = cfg
        self.n = self.z.shape[0]

    def to_grid(self, x, y):
        """Local metres -> fractional (row, col) in the north-up array."""
        col = (np.asarray(x) + self.cfg.half) / self.cfg.mpp
        row = (self.n - 1) - (np.asarray(y) + self.cfg.half) / self.cfg.mpp
        return row, col

    def sample(self, x, y):
        row, col = self.to_grid(x, y)
        row = np.clip(row, 0, self.n - 1.001)
        col = np.clip(col, 0, self.n - 1.001)
        r0, c0 = np.floor(row).astype(int), np.floor(col).astype(int)
        fr, fc = row - r0, col - c0
        z = self.z
        return ((z[r0, c0] * (1 - fc) + z[r0, c0 + 1] * fc) * (1 - fr)
                + (z[r0 + 1, c0] * (1 - fc) + z[r0 + 1, c0 + 1] * fc) * fr)


def ensure_dirs():
    DATA.mkdir(exist_ok=True)
    OUTPUT.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------------------------
# roads with per-node widths (traced roads carry a "widths" list, others a single "width")

OFFROAD = {"track", "trail"}     # road groups that are unpaved / off-road


def road_widths(feat):
    """Width at every node of a road feature (m)."""
    import numpy as np
    p = feat["properties"]
    n = len(feat["geometry"]["coordinates"])
    w = p.get("widths")
    return np.asarray(w, float) if w and len(w) == n else np.full(n, float(p["width"]))


def road_normals(xy):
    """Unit left-hand normals of a polyline (smoothed at the vertices)."""
    import numpy as np
    xy = np.asarray(xy, float)[:, :2]
    if len(xy) < 2:
        return np.tile([0.0, 1.0], (len(xy), 1))
    d = np.gradient(xy, axis=0)
    d /= np.linalg.norm(d, axis=1, keepdims=True) + 1e-9
    return np.column_stack([-d[:, 1], d[:, 0]])


def road_polygon(feat, extra=0.0):
    """Road surface polygon from per-node widths (+ extra metres each side)."""
    import numpy as np
    from shapely.geometry import LineString, Polygon
    pts = np.asarray(feat["geometry"]["coordinates"], float)[:, :2]
    w = road_widths(feat)
    if "widths" not in feat["properties"]:
        return LineString(pts).buffer(w[0] / 2 + extra, cap_style="flat")
    n = road_normals(pts)
    half = (w / 2 + extra)[:, None]
    ring = np.vstack([pts + n * half, (pts - n * half)[::-1]])
    poly = Polygon(ring)
    return poly if poly.is_valid else poly.buffer(0)


def landmark_registry():
    """landmarks/landmarks.json without its comment keys."""
    path = ROOT / "landmarks" / "landmarks.json"
    if not path.exists():
        return {}
    return {k: v for k, v in json.loads(path.read_text()).items() if not k.startswith("_")}


def basalt_west_x():
    """config.json "basalt_west_utm_e" in local x: west of that UTM easting walls and rural
    buildings are dark basalt and rock, east of it grey limestone (-inf when not set)."""
    cfg = Config()
    e = cfg.raw.get("basalt_west_utm_e")
    return float("-inf") if e is None else float(e) - cfg.center_e


def landmark_footprint_blocks(feats):
    """Indices of the generated blocks standing on a hand-built landmark's footprint."""
    from shapely.geometry import Polygon
    path = OUTPUT / "landmarks" / "placements.json"
    if not path.exists():
        return set()
    out = set()
    for lm in json.loads(path.read_text()):
        foot = Polygon(lm["footprint"]).buffer(1.0)
        for i, f in enumerate(feats):
            from shapely.geometry import shape
            g = shape(f["geometry"])
            if g.intersects(foot) and g.intersection(foot).area > 0.3 * g.area:
                out.add(i)
    return out
