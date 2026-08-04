"""NDBC Slate Island buoy 45136 — secondary water-temp truth proxy (PLAN task 3).

STATUS: NDBC hosts (www.ndbc.noaa.gov / dods.ndbc.noaa.gov) are blocked by this
environment's egress policy (verified 2026-08-04: unreachable). Client written to
the real realtime/stdmet shape; raises SourceUnavailable until allowlisted.

Buoy 45136 is ~165 km E of Thunder Bay and reads near-surface (1 m) water temp —
a coarse, distant proxy (seed corpus: 7.9 °C @ 1 m, 08:00 Aug 4 2026). Secondary
to GLSEA for the gates.
"""
from __future__ import annotations

import requests

from . import SourceUnavailable

STATION = "45136"
REALTIME_URL = f"https://www.ndbc.noaa.gov/data/realtime2/{STATION}.txt"


def fetch_water_temp(timeout: float = 60.0) -> list[dict]:
    """Recent standard-met records (incl. WTMP water temp) for buoy 45136.

    Raises SourceUnavailable in this environment (host blocked).
    """
    try:
        r = requests.get(REALTIME_URL, timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as e:
        raise SourceUnavailable(f"NDBC 45136 unreachable: {e}") from e
    raise NotImplementedError(
        "NDBC stdmet parse lands once the host is reachable (verification blocked)."
    )
