"""GLSEA daily Great Lakes SST — surface truth proxy for G1/G2 (PLAN task 3/5/8).

Verified live 2026-08-05 via the GLERL ERDDAP (`apps.glerl.noaa.gov/erddap`):

  * GLSEA_ACSPO_GCS — daily SST, 2006-12-11 → present (yesterday). **Truth source**
    for the 2024 tune / 2025-26 validation windows.
  * GLSEA_GCS       — daily SST, 1995-01-01 → 2023-12-31. **Climatology** source
    (PLAN task 8: seasonal norms / anomalies).
  * Grid ~0.014° (~1.1 km). Coastal/near-shore pixels are LAND-MASKED (null), so a
    station's exact coordinate often has no SST — we snap to the nearest valid water
    pixel (same offshore-sampling reality as the LSOFS nodes). That pixel is a
    SURFACE reference vs the gates' 6 m target: carries the depth caveat downstream.

Pure HTTP + numpy. No LLM.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import requests

from . import SourceUnavailable

ERDDAP = "https://apps.glerl.noaa.gov/erddap/griddap"
DATASET_TRUTH = "GLSEA_ACSPO_GCS"   # 2006 -> present
DATASET_CLIMO = "GLSEA_GCS"          # 1995 -> 2023
_HALF_DEG = 0.05  # ~3-4 pixel neighborhood to search for the nearest valid pixel


@dataclass(frozen=True)
class SstPixel:
    sst_c: float
    pixel_lat: float
    pixel_lon: float
    dist_km: float
    day: str
    dataset: str


def _erddap_box(dataset: str, day: str, lat: float, lon: float, half: float) -> list:
    """Fetch an sst neighborhood as rows [time, lat, lon, sst] from ERDDAP griddap."""
    q = (f"{ERDDAP}/{dataset}.json?sst"
         f"%5B({day}T12:00:00Z)%5D"
         f"%5B({lat - half}):({lat + half})%5D"
         f"%5B({lon - half}):({lon + half})%5D")
    try:
        r = requests.get(q, timeout=60)
        r.raise_for_status()
    except requests.RequestException as e:
        raise SourceUnavailable(f"GLSEA ERDDAP unreachable: {e}") from e
    return r.json()["table"]["rows"]


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = np.radians(lat2 - lat1), np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return float(2 * r * np.arcsin(np.sqrt(a)))


def fetch_sst(lat: float, lon: float, day: str, *, dataset: str = DATASET_TRUTH,
              half_deg: float = _HALF_DEG) -> SstPixel | None:
    """Daily SST (°C) at the nearest VALID water pixel to (lat, lon) for ISO `day`.

    Returns None if every pixel in the neighborhood is land-masked (widen half_deg
    to search further). `day` is 'YYYY-MM-DD' (GLSEA is a daily 12:00Z composite).
    """
    rows = _erddap_box(dataset, day, lat, lon, half_deg)
    best: SstPixel | None = None
    for _t, plat, plon, sst in rows:
        if sst is None:
            continue
        d = _haversine_km(lat, lon, plat, plon)
        if best is None or d < best.dist_km:
            best = SstPixel(float(sst), float(plat), float(plon), d, day, dataset)
    return best


def fetch_recent_sst(lat: float, lon: float, day, *, dataset: str = DATASET_TRUTH,
                     half_deg: float = _HALF_DEG, max_back: int = 4) -> SstPixel | None:
    """Most-recent available daily SST at/near (lat, lon), walking back from `day`.

    GLSEA lags ~1 day (today's composite posts tomorrow) and gaps on cloud, so the
    requested day is frequently a 404/empty. SST changes slowly day to day, so a
    recent prior day is a sound SURFACE ANCHOR — far better than dropping it, which
    collapses the corrected isotherm to the surface. Walks back up to `max_back` days;
    the returned SstPixel's `.day` is the day actually used. None only if nothing in
    the window resolves. `day` may be a date or an ISO 'YYYY-MM-DD' string.
    """
    if isinstance(day, str):
        day = date.fromisoformat(day)
    for k in range(max_back + 1):
        d = day - timedelta(days=k)
        try:
            px = fetch_sst(lat, lon, d.isoformat(), dataset=dataset, half_deg=half_deg)
        except SourceUnavailable:
            px = None
        if px is not None:
            return px
    return None


def coverage_end(dataset: str = DATASET_TRUTH, timeout: float = 60.0) -> str:
    """Dataset's last available day (ISO 'YYYY-MM-DD') from ERDDAP metadata, so callers
    can clamp a requested window and avoid ERDDAP's 404 on an out-of-range end."""
    try:
        r = requests.get(f"https://apps.glerl.noaa.gov/erddap/info/{dataset}/index.json",
                         timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as e:
        raise SourceUnavailable(f"GLSEA info unreachable: {e}") from e
    for row in r.json()["table"]["rows"]:
        if len(row) >= 5 and row[2] == "time_coverage_end":
            return str(row[4])[:10]
    raise SourceUnavailable("GLSEA time_coverage_end not found")


def fetch_series(pixel_lat: float, pixel_lon: float, start: str, end: str,
                 *, dataset: str = DATASET_TRUTH, timeout: float = 120.0) -> dict:
    """Daily SST time series at ONE fixed pixel over [start, end] (ISO days).

    Pass a pre-resolved VALID water pixel (e.g. fetch_sst(...).pixel_lat/lon) so the
    series is at a consistent location; cloud-gapped days come back absent (null).
    Returns {ISO-day: sst_c}. One ERDDAP griddap request over the time range.
    """
    q = (f"{ERDDAP}/{dataset}.json?sst"
         f"%5B({start}T12:00:00Z):({end}T12:00:00Z)%5D"
         f"%5B({pixel_lat})%5D%5B({pixel_lon})%5D")
    try:
        r = requests.get(q, timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as e:
        raise SourceUnavailable(f"GLSEA series unreachable: {e}") from e
    out: dict[str, float] = {}
    for t, _plat, _plon, sst in r.json()["table"]["rows"]:
        if sst is not None:
            out[str(t)[:10]] = float(sst)
    return out
