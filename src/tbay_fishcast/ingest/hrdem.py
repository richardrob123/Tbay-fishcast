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


# --- CHANNEL WIDTH ---------------------------------------------------------------------------
# Width is measured, not assumed, because it turned out to drive two things at once: the slope
# window and the reach-length floor are both expressed in channel widths, so a guessed width
# corrupts both. Checking the guesses against lidar found three of five materially wrong (Kam
# 80 -> 116 m, Neebing 20 -> 12 m, McIntyre 15 -> 54 m), while the Current matched (25 -> 28 m),
# which is what makes the method credible rather than merely convenient.
#
# WHAT THIS MEASURES, precisely: the WETTED width on the acquisition date (2024-05-06), not
# bankfull width. May is post-freshet here so the two are probably close, but they are not the
# same thing and the output says so rather than quietly borrowing bankfull's authority.
WIDTH_SEARCH_HALF_M = 150.0
WIDTH_STEP_M = 2.0
WIDTH_TOL_M = 0.60          # water is flat: within this of the channel's own surface level

# A CHANNEL HAS BANKS, and that is what these two constants encode (ADR-047).
#
# The flat-tolerance test alone finds "everything within 0.60 m of the water surface", which is
# the channel wherever the stream is incised — and the whole FIELD wherever it is not. On the
# Neebing at 48.4506,-89.3603 the ground west of the stream is a plain at 280.0-281.0 m for 150 m,
# so a 12 m stream measured 152 m wide. Those over-measurements then drove the bend-seam bank
# offset, and the seams drew in the woods on the live map.
#
# So the run must be bounded by ground that is UNAMBIGUOUSLY above the water — above it by twice
# the tolerance that defined "wet" in the first place, rather than marginally — reached within a
# short distance of the waterline. 1.2 m of rise within 16 m is a bank slope of ~7.5%, at or below
# the gentlest natural bank and far above any floodplain gradient.
#
# MEASURED SENSITIVITY (all five rivers, stride 5, look distance varied 6/10/16/24 m): the reach
# MEDIAN width is unchanged on every river at every setting, while the maximum collapses to a
# plausible channel — Neebing 252 -> 92-106 m, McVicar 196 -> 74-82 m, Current 220 -> 134 m — and
# the genuinely wide Kam barely moves (258 -> 254 m). The choice inside 10-24 m is therefore not
# load-bearing; what matters is that the rise is 2x the tolerance rather than 1x, which does
# nothing at all (the first cell outside the run already clears 1x by construction).
BANK_RISE_TOL_MULT = 2.0
BANK_LOOK_CELLS = 8         # x WIDTH_STEP_M = 16 m outside each edge of the wet run


def _bank_confined(prof, lo: int, hi: int, base: float) -> bool:
    """Is the wet run bounded by real banks on BOTH sides? (see BANK_RISE_TOL_MULT)

    Both sides, not either: a stream running along the toe of a valley wall has one perfectly
    good bank and one floodplain, and it is the floodplain side that runs away with the width.
    """
    need = base + BANK_RISE_TOL_MULT * WIDTH_TOL_M
    left = [v for v in prof[max(0, lo - BANK_LOOK_CELLS):lo] if v is not None]
    right = [v for v in prof[hi + 1:hi + 1 + BANK_LOOK_CELLS] if v is not None]
    return bool(left) and bool(right) and max(left) >= need and max(right) >= need


def measure_widths(points, *, url: str = DTM_URL, stride: int = 1):
    """Wetted width (m) at each station, or None where the transect saturates.

    A saturated transect — wet all the way to the search limit — does NOT mean a very wide river.
    It means the station is not in a channel at all: an impoundment, a wetland, or the river mouth
    opening into the lake. Averaging those in would inflate width exactly where the channel
    concept stops applying, so they are returned as None for the caller to interpolate across.
    Usefully, saturation is itself evidence of standing water.
    """
    import rasterio
    from rasterio.warp import transform as warp_transform

    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    pts = list(points)
    if len(pts) < 2:
        return [None] * len(pts)
    offs = [(-WIDTH_SEARCH_HALF_M + i * WIDTH_STEP_M)
            for i in range(int(2 * WIDTH_SEARCH_HALF_M / WIDTH_STEP_M) + 1)]
    idx = list(range(0, len(pts), max(1, stride)))
    query, groups = [], []
    for i in idx:
        a, b = pts[max(0, i - 1)], pts[min(len(pts) - 1, i + 1)]
        dlat, dlon = _transect_offsets(a, b)
        g = []
        for d in offs:
            g.append(len(query))
            query.append((pts[i][0] + dlat * d, pts[i][1] + dlon * d))
        groups.append(g)

    with rasterio.open(url) as ds:
        xs, ys = warp_transform("EPSG:4326", ds.crs,
                                [q[1] for q in query], [q[0] for q in query])
        raw = [v[0] for v in ds.sample(list(zip(xs, ys)))]

    out_sparse = []
    n = len(offs)
    for g in groups:
        prof = [None if (raw[k] is None or float(raw[k]) == NODATA
                         or not math.isfinite(float(raw[k]))) else float(raw[k]) for k in g]
        c = n // 2
        near = [v for v in prof[max(0, c - 4):c + 5] if v is not None]
        if not near:
            out_sparse.append(None)
            continue
        base = min(near)
        wet = [v is not None and v <= base + WIDTH_TOL_M for v in prof]
        if not wet[c]:
            cand = [k for k, w in enumerate(wet) if w]
            if not cand:
                out_sparse.append(None)
                continue
            c = min(cand, key=lambda k: abs(k - n // 2))
        lo = c
        while lo - 1 >= 0 and wet[lo - 1]:
            lo -= 1
        hi = c
        while hi + 1 < n and wet[hi + 1]:
            hi += 1
        if lo == 0 or hi == n - 1:
            out_sparse.append(None)          # saturated: not a channel here
        elif not _bank_confined(prof, lo, hi, base):
            out_sparse.append(None)          # no bank on one side: floodplain, not a channel
        else:
            out_sparse.append((hi - lo) * WIDTH_STEP_M)

    if stride == 1:
        return out_sparse
    full = [None] * len(pts)
    for k, i in enumerate(idx):
        full[i] = out_sparse[k]
    return full


def local_width(widths, dist_m, max_gap_m: float | None = None):
    """Per-station wetted width, filling ONLY the sampling stride — never saturation.

    :func:`measure_widths` runs on a stride, so most stations carry no measurement at all. Those
    holes are an artefact of how often we sampled and are filled by linear interpolation between
    their measured neighbours. A run of ``None`` LONGER than the stride is a different thing
    entirely: the transect never found a bank, which means there is no channel at that station —
    an impoundment, a wetland, a drowned mouth. Those stay ``None``, and callers must decline to
    draw rather than invent a width.

    THIS IS THE COUNTERPART TO :func:`reach_width`, AND THE DISTINCTION IS WHERE A REAL BUG LIVED.
    ``reach_width``'s 1 km running median is the right scale for the SLOPE WINDOW (see its
    docstring) and the wrong scale for anything positional. Offsetting a bend seam to the outer
    bank by half of it put segments 60-110 m off a 10 m stream on the Neebing, because the same
    1 km window contained a wide confluence — and on the Current, because it contained Boulevard
    Lake. The measured local width there was 10 m; the reach median was 148 m. The seams drew in
    the woods, which is exactly what the operator saw on the live map.

    ``max_gap_m`` should be passed by the caller, which knows the stride it actually used
    (``2 * stride * step_m``). The fallback — twice the SMALLEST observed gap, the smallest gap
    being the stride whenever any adjacent pair was measured — is there so the function is usable
    standalone, and it degenerates honestly: with only two measurements there is no evidence about
    the sampling interval at all, and it will fill.
    """
    n = len(widths)
    out = [None] * n
    idx = [i for i in range(n) if widths[i] is not None]
    if len(idx) < 2:
        for i in idx:
            out[i] = widths[i]
        return out
    if max_gap_m is None:
        max_gap_m = 2.0 * min(dist_m[b] - dist_m[a] for a, b in zip(idx, idx[1:]))
    for i in idx:
        out[i] = widths[i]
    for a, b in zip(idx, idx[1:]):
        span = dist_m[b] - dist_m[a]
        if span <= 0 or span > max_gap_m:
            continue
        for k in range(a + 1, b):
            t = (dist_m[k] - dist_m[a]) / span
            out[k] = widths[a] + t * (widths[b] - widths[a])
    return out


def reach_width(widths, dist_m, smooth_m: float = 1000.0):
    """Reach-scale width: a running median over `smooth_m`, with saturated gaps filled.

    Use this ONLY where a reach-scale number is what the physics wants — the slope window. For
    anything POSITIONAL (offsetting a seam to a bank, a bend's R/w, Manning's channel width) use
    :func:`local_width`, whose docstring records what happens when they are confused.

    WHY SMOOTHED, and this is the subtle part. If the slope window scaled with the LOCAL width,
    then a wide slow pool would be smoothed harder than a narrow riffle — the classifier's own
    resolution tracking the very property it is trying to classify, a feedback that makes pools
    look flatter and reinforces its own answer. Smoothing width over a scale much longer than a
    pool-riffle unit (~5-7 widths) breaks that loop while keeping the genuine downstream
    widening, which is the part that actually varies with position on the river.
    """
    n = len(widths)
    out = [None] * n
    for i in range(n):
        lo, hi = dist_m[i] - smooth_m / 2, dist_m[i] + smooth_m / 2
        vals = [widths[j] for j in range(n)
                if widths[j] is not None and lo <= dist_m[j] <= hi]
        if vals:
            vals.sort()
            out[i] = vals[len(vals) // 2]
    known = [i for i in range(n) if out[i] is not None]
    if not known:
        return out
    for i in range(n):                       # carry the nearest measured value into the gaps
        if out[i] is None:
            j = min(known, key=lambda k: abs(k - i))
            out[i] = out[j]
    return out
