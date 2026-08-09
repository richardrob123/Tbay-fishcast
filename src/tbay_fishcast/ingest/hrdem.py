"""NRCan HRDEM 1 m lidar — water-surface elevation along a river (ADR-045).

SOURCE, verified 2026-08-09 against the CanElevation STAC:
    collection  hrdem-lidar
    item        ON-ThunderBay_UTM16_2024-1m   (acquired 2024-05-06, EPSG:3979)
    footprint   lon -90.152..-88.485, lat 47.995..48.867  -> covers the whole forecast domain
    assets      -dtm.tif / -dsm.tif, cloud-optimized GeoTIFF on public S3, HTTP range reads
Four other acquisitions overlap (2018, 2021 Thunder Bay, 2022 Nipigon, 2020 Black Sturgeon); the
2024 tile is preferred simply because it is newest and covers everything, and the acquisition date
is carried into the output so a reader knows what year's water surface they are looking at.

USE THE DTM, NOT THE DSM. The DSM is first-return, so over a river it samples bridges and
overhanging canopy rather than water — profiling the Current River with it produced vertical
spikes and 17.1% of samples running UPHILL. The bare-earth DTM gave the same length, the same
total drop and the same features with 1.8% uphill. That was the single largest accuracy fix in
this module and it is not optional.

TRANSECT MINIMUM, NOT CENTRELINE POINT. An OSM centreline is a cartographic line, not a surveyed
thalweg; it wanders toward one bank and clips vegetated edges. Sampling the MINIMUM elevation
across a short perpendicular transect finds the water surface even when the line is off-centre,
because within a channel cross-section the water is the lowest thing there. This is what makes the
profile robust to centreline error rather than merely tolerant of it.

No LLM (ADR-001). Network reads are cached under data/hrdem_cache/.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_CACHE = _ROOT / "data" / "hrdem_cache"

STAC_ITEM = "ON-ThunderBay_UTM16_2024-1m"
ACQUIRED = "2024-05-06"
_BASE = "https://canelevation-dem.s3.ca-central-1.amazonaws.com/hrdem-lidar"
DTM_URL = f"/vsicurl/{_BASE}/{STAC_ITEM}-dtm.tif"
DSM_URL = f"/vsicurl/{_BASE}/{STAC_ITEM}-dsm.tif"
FOOTPRINT = (-90.152, 47.995, -88.485, 48.867)     # lon0, lat0, lon1, lat1
NODATA = -32767.0

# Lake Superior's low-water datum (IGLD85). The downstream end of any profile that reaches the
# lake must land on this within a metre or so — a free, independent check that the vertical datum,
# the tile and the sampling all line up. Measured 182.82 m on the Current River.
LAKE_SUPERIOR_DATUM_M = 183.2

TRANSECT_HALF_M = 12.0     # half-width of the perpendicular search for the water surface
TRANSECT_STEP_M = 2.0


def in_footprint(lat: float, lon: float) -> bool:
    x0, y0, x1, y1 = FOOTPRINT
    return x0 <= lon <= x1 and y0 <= lat <= y1


def _cache_path(key: str) -> Path:
    return _CACHE / f"{hashlib.sha256(key.encode()).hexdigest()[:20]}.json"


def _transect_offsets(a, b):
    """Unit normal to the local downstream direction, in degrees lat/lon."""
    mlat = 111320.0
    mlon = 111320.0 * math.cos(math.radians(a[0]))
    dx = (b[1] - a[1]) * mlon
    dy = (b[0] - a[0]) * mlat
    n = math.hypot(dx, dy)
    if n < 1e-9:
        return 0.0, 0.0
    # normal is (-dy, dx) in metres -> back to degrees
    return (dx / n) / mlat, (-dy / n) / mlon


def sample_water_surface(points, *, url: str = DTM_URL, transect: bool = True):
    """Water-surface elevation (m) at each (lat, lon) along a downstream-ordered polyline.

    Returns (elevations, stats). Elevations are None where the raster has no data. `stats` carries
    what a later audit needs: nodata count, how often the transect minimum beat the centreline
    point, and the provenance of the tile.
    """
    import rasterio                                   # geo extra; imported lazily

    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    pts = list(points)
    if not pts:
        return [], {"n": 0}
    key = json.dumps([url, transect, [(round(a, 6), round(b, 6)) for a, b in pts[:4]],
                      len(pts), round(pts[-1][0], 6), round(pts[-1][1], 6)])
    cp = _cache_path(key)
    if cp.exists():
        try:
            d = json.loads(cp.read_text())
            return d["z"], d["stats"]
        except (ValueError, KeyError):
            pass

    from rasterio.warp import transform as warp_transform

    # build the full query list: centreline point plus, optionally, its perpendicular transect
    query, groups = [], []
    for i, p in enumerate(pts):
        a = pts[max(0, i - 1)]
        b = pts[min(len(pts) - 1, i + 1)]
        offs = [(0.0, 0.0)]
        if transect:
            dlat, dlon = _transect_offsets(a, b)
            k = 1
            while k * TRANSECT_STEP_M <= TRANSECT_HALF_M:
                d = k * TRANSECT_STEP_M
                offs += [(dlat * d, dlon * d), (-dlat * d, -dlon * d)]
                k += 1
        g = []
        for dla, dlo in offs:
            g.append(len(query))
            query.append((p[0] + dla, p[1] + dlo))
        groups.append(g)

    with rasterio.open(url) as ds:
        xs, ys = warp_transform("EPSG:4326", ds.crs,
                                [q[1] for q in query], [q[0] for q in query])
        raw = [v[0] for v in ds.sample(list(zip(xs, ys)))]

    z, nodata, improved = [], 0, 0
    for g in groups:
        vals = [float(raw[i]) for i in g
                if raw[i] is not None and float(raw[i]) != NODATA and math.isfinite(float(raw[i]))]
        if not vals:
            z.append(None)
            nodata += 1
            continue
        centre = float(raw[g[0]]) if float(raw[g[0]]) != NODATA else None
        m = min(vals)
        if centre is not None and m < centre - 0.05:
            improved += 1
        z.append(m)

    stats = {"n": len(pts), "nodata": nodata, "transect_lowered": improved,
             "transect": transect, "source": STAC_ITEM, "acquired": ACQUIRED,
             "product": "dtm" if url == DTM_URL else "dsm"}
    _CACHE.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps({"z": z, "stats": stats}))
    return z, stats


def datum_check(z_downstream_end: float | None) -> dict:
    """Compare a profile's lake end against Lake Superior's datum — an independent sanity check."""
    if z_downstream_end is None:
        return {"ok": False, "reason": "no elevation at the downstream end"}
    err = abs(z_downstream_end - LAKE_SUPERIOR_DATUM_M)
    return {"ok": err <= 1.5, "measured_m": round(z_downstream_end, 2),
            "datum_m": LAKE_SUPERIOR_DATUM_M, "error_m": round(err, 2)}
