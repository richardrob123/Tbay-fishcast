"""CHS NONNA-10 bathymetry — programmatic depth-vs-distance-from-shore.

This is the map half of the product. cross_shore.py turns a temperature PROFILE
(temp vs depth) into "where does the target band sit"; it needs a BATHYMETRY
profile (depth vs distance from shore) for the same spot. That profile used to be
hand-typed COARSE guesses. This module builds it from real hydrographic soundings.

Source: Canadian Hydrographic Service NONNA-10 (Non-Navigational, 10 m grid),
served as OGC WCS coverage from the CHS GeoServer. Free, open-licensed for
non-navigational use (fishing forecast qualifies), whole-of-Canada coverage,
~10 m native / ~12.7 m ground at this latitude. This is the same survey data the
proprietary chart apps repackage — but machine-readable and free, so we never
scrape or buy a proprietary product (ADR-016).

  Licence: "This product ... contains intellectual property of the Canadian
  Hydrographic Service (CHS)"; not for navigation. Attribution goes in every
  derivative (see knowledge/bathymetry/*.json provenance and the map footer).

TWO LAYERS:
  * PURE geometry (`nearest_water_px`, `offshore_bearing`, `walk_profile`) — no
    network, unit-tested against synthetic depth grids.
  * NETWORK fetch (`fetch_patch`) — pulls a GeoTIFF subset via rasterio. Used only
    by the OFFLINE builder (scripts/build_bathymetry.py); the heartbeat reads the
    cached JSON profiles it writes, so no geo dependency sits in the 4x/day loop
    (ADR-001) and CHS is hit once per spot, not every cycle.

Depth convention in NONNA GeoTIFFs: value is height relative to chart datum,
negative = below datum (underwater). We flip to positive-down `depth_m` (> 0 is
water); land / drying height / nodata become NaN.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

WCS_URL = "https://nonna-geoserver.data.chs-shc.ca/geoserver/wcs"
COVERAGE_ID = "nonna__NONNA 10 Coverage"
GEOTIFF_NODATA = 3.4028234663852886e38  # float32 max — GeoServer's land/nodata fill
_R = 6378137.0  # WGS84 / Web-Mercator sphere radius (EPSG:3857)


# ---------------------------------------------------------------------------
# projection helpers (EPSG:4326 <-> EPSG:3857). NONNA WCS serves 3857.
# ---------------------------------------------------------------------------
def to_mercator(lat: float, lon: float) -> tuple[float, float]:
    x = math.radians(lon) * _R
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * _R
    return x, y


def ground_res_m(res_mercator_m: float, lat: float) -> float:
    """Mercator metres are inflated by 1/cos(lat); convert a pixel size to ground m."""
    return res_mercator_m * math.cos(math.radians(lat))


# ---------------------------------------------------------------------------
# PURE grid geometry — testable without network
# ---------------------------------------------------------------------------
def depths_from_raw(raw: np.ndarray) -> np.ndarray:
    """NONNA raw values -> positive-down depth_m; land/dry/nodata -> NaN.

    Underwater samples are negative (below chart datum). depth_m = -value where
    value < 0; everything else (drying height >= 0, or nodata fill) is not water.
    """
    a = np.asarray(raw, dtype=float)
    a = np.where(np.isfinite(a) & (np.abs(a) < 1e5), a, np.nan)
    depth = -a
    depth = np.where(depth > 0, depth, np.nan)
    return depth


def nearest_water_px(depth: np.ndarray, r: int, c: int, max_radius: int = 60):
    """Nearest pixel (row, col) with a finite water depth, spiralling out from (r,c)."""
    r = int(np.clip(r, 0, depth.shape[0] - 1))
    c = int(np.clip(c, 0, depth.shape[1] - 1))
    if np.isfinite(depth[r, c]):
        return r, c
    for rad in range(1, max_radius + 1):
        best = None
        best_d = None
        r0, r1 = max(0, r - rad), min(depth.shape[0] - 1, r + rad)
        c0, c1 = max(0, c - rad), min(depth.shape[1] - 1, c + rad)
        for rr in range(r0, r1 + 1):
            for cc in range(c0, c1 + 1):
                if max(abs(rr - r), abs(cc - c)) != rad:
                    continue  # ring only
                if np.isfinite(depth[rr, cc]):
                    d2 = (rr - r) ** 2 + (cc - c) ** 2
                    if best_d is None or d2 < best_d:
                        best_d, best = d2, (rr, cc)
        if best is not None:
            return best
    return None


def _bearing_step(bearing_deg: float) -> tuple[float, float]:
    """Geographic bearing (0=N, 90=E, clockwise) -> unit (drow, dcol) in pixel space.
    Row increases southward, so North is -row."""
    th = math.radians(bearing_deg)
    return (-math.cos(th), math.sin(th))


def sample_ray(depth: np.ndarray, r0: float, c0: float, drow: float, dcol: float,
               n: int, step_px: float) -> list[float]:
    """Depth values along a ray from (r0,c0); NaN where off-grid or not water."""
    out = []
    for k in range(n + 1):
        r = int(round(r0 + drow * step_px * k))
        c = int(round(c0 + dcol * step_px * k))
        if 0 <= r < depth.shape[0] and 0 <= c < depth.shape[1]:
            out.append(float(depth[r, c]))
        else:
            out.append(float("nan"))
    return out


def offshore_bearing(depth: np.ndarray, r: int, c: int, *, window: int = 8,
                     hint_deg: float | None = None,
                     hint_tol_deg: float = 60.0) -> float | None:
    """Bearing (deg) of the deepening direction — the local depth gradient.

    Offshore is, by definition, the direction the bottom drops away: the gradient
    of the depth field. Average the finite depth differences to neighbours over a
    `window`-pixel box around (r,c) to get an east/north vector, then convert to a
    geographic bearing. This is robust to grid-edge truncation (unlike ray-scans).

    If `hint_deg` (a station's exposure bearing) is given and the gradient bearing
    falls outside `hint_tol_deg` of it — e.g. the pixel sits in a channel whose
    gradient points up-channel, not out to the lake — the hint is used instead, so
    a noisy near-shore cell can't misdirect the transect. Returns None if the
    neighbourhood has no usable gradient (and no hint).
    """
    r0, r1 = max(0, r - window), min(depth.shape[0], r + window + 1)
    c0, c1 = max(0, c - window), min(depth.shape[1], c + window + 1)
    sub = depth[r0:r1, c0:c1]
    # east component = d(depth)/d(col); north component = -d(depth)/d(row)
    east_sum = north_sum = 0.0
    count = 0
    for i in range(sub.shape[0]):
        for j in range(sub.shape[1]):
            d = sub[i, j]
            if not math.isfinite(d):
                continue
            if j + 1 < sub.shape[1] and math.isfinite(sub[i, j + 1]):
                east_sum += sub[i, j + 1] - d
                count += 1
            if i + 1 < sub.shape[0] and math.isfinite(sub[i + 1, j]):
                north_sum += -(sub[i + 1, j] - d)  # row+ is south, flip sign
                count += 1
    grad_bearing = None
    if count and (abs(east_sum) > 1e-9 or abs(north_sum) > 1e-9):
        grad_bearing = math.degrees(math.atan2(east_sum, north_sum)) % 360.0
    if hint_deg is None:
        return grad_bearing
    if grad_bearing is None:
        return float(hint_deg)
    diff = abs((grad_bearing - hint_deg + 180) % 360 - 180)
    return grad_bearing if diff <= hint_tol_deg else float(hint_deg)


def walk_profile(depth: np.ndarray, r: int, c: int, bearing_deg: float, *,
                 res_ground_m: float, step_px: float = 1.0, max_n: int = 60):
    """Cross-shore (distance_from_shore_m, depth_m) profile along `bearing_deg`.

    Walks LANDWARD first to place the shoreline (depth -> ~0) at distance 0, then
    OFFSHORE sampling depth every `step_px`. Distances are real ground metres.
    Stops offshore at the first land/nodata gap (end of the mapped near-shore slope).
    """
    drow, dcol = _bearing_step(bearing_deg)
    # landward: opposite direction until we run out of water -> shore origin
    sr, sc = float(r), float(c)
    for _ in range(max_n):
        nr, nc = sr - drow * step_px, sc - dcol * step_px
        ri, ci = int(round(nr)), int(round(nc))
        if not (0 <= ri < depth.shape[0] and 0 <= ci < depth.shape[1]) or not math.isfinite(depth[ri, ci]):
            break
        sr, sc = nr, nc
    # offshore sampling from shore origin
    dists = [0.0]
    depths = [0.0]
    step_m = step_px * res_ground_m
    for k in range(1, max_n + 1):
        r_k = int(round(sr + drow * step_px * k))
        c_k = int(round(sc + dcol * step_px * k))
        if not (0 <= r_k < depth.shape[0] and 0 <= c_k < depth.shape[1]):
            break
        d = depth[r_k, c_k]
        if not math.isfinite(d):
            break
        dists.append(k * step_m)
        depths.append(float(d))
    return dists, depths


# ---------------------------------------------------------------------------
# NETWORK fetch — offline builder only (guarded rasterio import)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Patch:
    depth: np.ndarray          # positive-down depth_m, NaN=land/nodata
    r0: int                    # row of the requested centre lat/lon
    c0: int                    # col of the requested centre lat/lon
    res_mercator_m: float      # pixel size in EPSG:3857 metres
    lat: float
    lon: float
    coverage_frac: float       # fraction of the patch that is water
    # actual EPSG:3857 bounds of the returned grid (left, bottom, right, top) —
    # the ground truth for aligning any co-registered basemap (e.g. satellite).
    bounds_3857: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


def _wcs_geotiff_url(lat: float, lon: float, half_m: float, scale_px: int | None) -> str:
    from urllib.parse import quote
    x, y = to_mercator(lat, lon)
    cid = quote(COVERAGE_ID)
    url = (f"{WCS_URL}?service=WCS&version=2.0.1&request=GetCoverage"
           f"&coverageId={cid}&format=image/geotiff"
           f"&subset=x({x - half_m:.1f},{x + half_m:.1f})"
           f"&subset=y({y - half_m:.1f},{y + half_m:.1f})")
    if scale_px:
        url += (f"&scalesize=http://www.opengis.net/def/axis/OGC/1/i({scale_px}),"
                f"http://www.opengis.net/def/axis/OGC/1/j({scale_px})")
    return url


def fetch_patch(lat: float, lon: float, *, half_m: float = 1500.0,
                scale_px: int | None = None, timeout: float = 60.0) -> Patch:
    """Pull a NONNA-10 GeoTIFF subset centred on (lat,lon) and return a Patch.

    Requires rasterio (import guarded so the pure geometry above stays dependency-free).
    Network — offline use only.
    """
    import io
    import subprocess

    import rasterio

    url = _wcs_geotiff_url(lat, lon, half_m, scale_px)
    raw = subprocess.run(["curl", "-s", "-m", str(int(timeout)), url],
                         capture_output=True).stdout
    if not raw or raw[:2] not in (b"II", b"MM"):
        head = raw[:200].decode("latin-1", "replace")
        raise RuntimeError(f"NONNA WCS did not return a GeoTIFF for {lat},{lon}: {head!r}")
    with rasterio.open(io.BytesIO(raw)) as ds:
        band = ds.read(1)
        res_merc = abs(ds.transform.a)
        x, y = to_mercator(lat, lon)
        c0, r0 = (~ds.transform) * (x, y)
        b = ds.bounds
        bounds = (float(b.left), float(b.bottom), float(b.right), float(b.top))
    depth = depths_from_raw(band)
    cov = float(np.isfinite(depth).mean())
    return Patch(depth=depth, r0=int(round(r0)), c0=int(round(c0)),
                 res_mercator_m=res_merc, lat=lat, lon=lon, coverage_frac=cov,
                 bounds_3857=bounds)


def build_profile(lat: float, lon: float, *, bearing_deg: float | None = None,
                  half_m: float = 1500.0, scale_px: int | None = None,
                  max_n: int = 60) -> dict:
    """Fetch + reduce to a cross-shore profile dict (the cross_shore.py bathy input).

    Returns {lat, lon, bearing_deg, dist_m[], depth_m[], resolution_ground_m,
    coverage_frac, snapped, source, note}. Network — offline builder only.
    """
    patch = fetch_patch(lat, lon, half_m=half_m, scale_px=scale_px)
    res_g = ground_res_m(patch.res_mercator_m, lat)
    snap = nearest_water_px(patch.depth, patch.r0, patch.c0)
    if snap is None:
        return {"lat": lat, "lon": lon, "bearing_deg": bearing_deg,
                "dist_m": [], "depth_m": [], "resolution_ground_m": round(res_g, 1),
                "coverage_frac": round(patch.coverage_frac, 3), "snapped": False,
                "source": "CHS NONNA-10 (WCS)",
                "note": "no NONNA water pixel within snap radius — unsurveyed here"}
    sr, sc = snap
    brg = bearing_deg
    if brg is None:
        brg = offshore_bearing(patch.depth, sr, sc, hint_deg=None)
    else:
        # keep the given exposure bearing but let the grid nudge it toward deep water
        auto = offshore_bearing(patch.depth, sr, sc, hint_deg=brg, hint_tol_deg=60.0)
        brg = auto if auto is not None else brg
    if brg is None:
        brg = bearing_deg or 90.0
    dists, depths = walk_profile(patch.depth, sr, sc, brg, res_ground_m=res_g,
                                 max_n=max_n)
    return {"lat": lat, "lon": lon, "bearing_deg": round(float(brg), 1),
            "dist_m": [round(d, 1) for d in dists],
            "depth_m": [round(h, 2) for h in depths],
            "resolution_ground_m": round(res_g, 1),
            "coverage_frac": round(patch.coverage_frac, 3), "snapped": True,
            "source": "CHS NONNA-10 (WCS GetCoverage, EPSG:3857)",
            "note": f"cross-shore transect on bearing {brg:.0f}deg, "
                    f"{len(dists)} samples to {dists[-1]:.0f} m offshore"}
