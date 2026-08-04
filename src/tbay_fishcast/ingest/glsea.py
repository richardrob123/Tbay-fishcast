"""GLSEA daily Great Lakes SST — surface truth proxy for G1/G2 (PLAN task 3/5).

STATUS: GLERL hosts (coastwatch.glerl.noaa.gov / apps.glerl.noaa.gov) are blocked
by this environment's egress policy (verified 2026-08-04: unreachable). Client
written to the real access shape; raises SourceUnavailable until allowlisted.

GLSEA is SURFACE skin SST, archive from 1995. It is the least-bad reachable truth
for the temperature gates, but it is NOT a 6 m measurement — every value carries a
surface-depth caveat downstream (see verification/scorecard.py).
"""
from __future__ import annotations

import requests

from . import SourceUnavailable

# GLSEA daily NetCDF (GLERL THREDDS/HTTPS). Pattern kept explicit for allowlisting.
GLSEA_BASE = "https://coastwatch.glerl.noaa.gov/data/glsea/glsea"


def fetch_sst_pixel(lat: float, lon: float, day: str, timeout: float = 60.0) -> float:
    """Daily SST (°C) at the pixel nearest (lat, lon) for ISO `day`.

    Raises SourceUnavailable in this environment (host blocked).
    """
    try:
        r = requests.get(f"{GLSEA_BASE}/{day}.nc", timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as e:
        raise SourceUnavailable(f"GLSEA unreachable: {e}") from e
    raise NotImplementedError(
        "GLSEA pixel decode lands once the host is reachable and the archive "
        "layout is confirmed (verification #4 could not run: host blocked)."
    )
