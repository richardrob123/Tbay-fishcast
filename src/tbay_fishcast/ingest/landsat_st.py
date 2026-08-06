"""Landsat 8/9 surface temperature — the NEAR-SHORE skin-temp anchor (~100 m).

The gap every other source left open: GLSEA (~1 km) and MUR/ERA5 are LAND-MASKED or too
coarse at the immediate shore, so the surface anchor near a station gets pulled from a
pixel 1-4 km offshore — the prime suspect for the shallow-isotherm low-confidence cases.
Landsat Collection-2 Level-2 **Surface Temperature** (ST_B10, ~100 m native thermal on a
30 m grid) retains water pixels right up to the Thunder Bay shore (confirmed: Marina 50 m
offshore 16.1 C, 500 m 15.5 C, mid-harbour 13.0 C — the correct nearshore-warm gradient).

Served analysis-ready and NO-AUTH via the Microsoft Planetary Computer STAC + anonymous
SAS signing. Thunder Bay sits in a WRS-2 path overlap, so L8+L9 give a clear-sky attempt
every ~3-8 days. Latency ~10 days (C2 L2 processing) and cloud-limited, so this is a
periodic clear-sky ANCHOR, not a daily feed; it in-fills between passes via LSOFS/GLSEA.

Honest caveat: the ST product uses land emissivity + land-tuned atmospheric correction,
so over water it is a reasonable SKIN temperature, not a water-calibrated bulk temp —
validate against the Bare Point intake once available. Offline/validation use (rasterio;
network); never in the heartbeat (ADR-001). No LLM.

  Data: USGS Landsat C2 L2 (courtesy USGS/NASA), via Microsoft Planetary Computer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import requests

STAC_SEARCH = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
PC_SIGN = "https://planetarycomputer.microsoft.com/api/sas/v1/sign"
COLLECTION = "landsat-c2-l2"
_ST_SCALE, _ST_OFFSET = 0.00341802, 149.0        # DN -> Kelvin (C2 L2 ST)
_QA_WATER_BIT = 1 << 7                             # QA_PIXEL bit 7 = water


def st_dn_to_celsius(dn):
    """Landsat C2 L2 ST digital number -> degrees Celsius (pure, unit-testable)."""
    return dn * _ST_SCALE + _ST_OFFSET - 273.15


def is_water(qa_pixel_value: int) -> bool:
    """True if the Landsat C2 QA_PIXEL value has the water bit (7) set."""
    return bool(int(qa_pixel_value) & _QA_WATER_BIT)


@dataclass
class StPixel:
    sst_c: float
    scene: str
    day: str
    cloud_pct: float
    dist_m: float          # distance from the request point to the water pixel used


def search_scenes(bbox, start: date, end: date, *, max_cloud: float = 30.0,
                  limit: int = 12, timeout: float = 60.0) -> list[dict]:
    """Clear-ish Landsat L2 items over `bbox` [W,S,E,N], newest first."""
    r = requests.post(STAC_SEARCH, timeout=timeout, json={
        "collections": [COLLECTION], "bbox": list(bbox),
        "datetime": f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z",
        "query": {"eo:cloud_cover": {"lt": max_cloud}},
        "sortby": [{"field": "properties.datetime", "direction": "desc"}],
        "limit": limit})
    r.raise_for_status()
    return r.json().get("features", [])


def _sign(href: str, timeout: float = 60.0) -> str:
    r = requests.get(PC_SIGN, params={"href": href}, timeout=timeout)
    r.raise_for_status()
    return r.json()["href"]


def sample_scene(item: dict, lat: float, lon: float, *, win_px: int = 25,
                 max_dist_m: float = 600.0) -> StPixel | None:
    """Nearest WATER-pixel skin temperature (C) to (lat, lon) in one Landsat scene.

    Reads a small window of the ST band + QA band, keeps only pixels flagged water and
    with a plausible lake temperature, and returns the closest one within `max_dist_m`.
    None if the point is clouded/off-scene or no water pixel is near (pick another scene).
    """
    import numpy as np
    import rasterio
    from rasterio.warp import transform as _transform
    from rasterio.windows import Window

    assets = item["assets"]
    st_href = _sign(assets["lwir11"]["href"])
    qa_href = _sign(assets["qa_pixel"]["href"])
    with rasterio.open(st_href) as st, rasterio.open(qa_href) as qa:
        xs, ys = _transform("EPSG:4326", st.crs, [lon], [lat])
        row, col = st.index(xs[0], ys[0])
        r0, c0 = row - win_px, col - win_px
        w = Window(c0, r0, 2 * win_px + 1, 2 * win_px + 1)
        if c0 < 0 or r0 < 0 or c0 + w.width > st.width or r0 + w.height > st.height:
            return None
        dn = st.read(1, window=w).astype("float64")
        qav = qa.read(1, window=w)
    temp_c = st_dn_to_celsius(dn)
    water = (qav & _QA_WATER_BIT).astype(bool) & (dn > 0) & (temp_c > 0.0) & (temp_c < 28.0)
    if not water.any():
        return None
    res = abs(st.transform.a)                         # ~30 m grid
    rr, cc = np.where(water)
    d_m = np.hypot(rr - win_px, cc - win_px) * res
    k = int(np.argmin(d_m))
    if d_m[k] > max_dist_m:
        return None
    p = item["properties"]
    return StPixel(round(float(temp_c[rr[k], cc[k]]), 2), item["id"],
                   p["datetime"][:10], float(p.get("eo:cloud_cover", -1)), round(float(d_m[k]), 0))


def fetch_recent_st(lat: float, lon: float, bbox, start: date, end: date, *,
                    max_cloud: float = 30.0) -> StPixel | None:
    """Most recent clear Landsat water-pixel skin temp at (lat, lon). Walks newest->older
    scenes until one has an unclouded water pixel near the point."""
    for item in search_scenes(bbox, start, end, max_cloud=max_cloud):
        px = sample_scene(item, lat, lon)
        if px is not None:
            return px
    return None
