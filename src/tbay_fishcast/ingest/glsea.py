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
