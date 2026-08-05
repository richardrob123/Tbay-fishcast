"""ERA5 hourly wind via Open-Meteo historical archive (PLAN task 3).

STATUS: `archive-api.open-meteo.com` is reachable in this environment (verified
2026-08-05: HTTP 200, real hourly records returned). An earlier note claimed the
host was egress-blocked (403 on 2026-08-04); that no longer holds. The client still
raises SourceUnavailable on any transport error, so the backfill degrades loudly
(CLAUDE rule 5), never silently — it just isn't expected to be unavailable now.

Wind is the driver of the Wedderburn/upwelling cross-table (PLAN task 4). Thunder
Bay point: ~48.4 N, -89.2 W.
"""
from __future__ import annotations

import requests

from . import SourceUnavailable

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
TBAY_LAT, TBAY_LON = 48.4, -89.2


def fetch_wind(start_date: str, end_date: str, lat: float = TBAY_LAT,
               lon: float = TBAY_LON, timeout: float = 60.0) -> dict:
    """Hourly 10 m wind (speed kn, direction, gusts) for [start_date, end_date].

    Returns the parsed `hourly` block. Raises SourceUnavailable on any transport
    error (the expected state in this environment).
    """
    params = {
        "latitude": lat, "longitude": lon,
        "start_date": start_date, "end_date": end_date,
        "hourly": "wind_speed_10m,wind_direction_10m,wind_gusts_10m",
        "wind_speed_unit": "kn", "timezone": "UTC",
    }
    try:
        r = requests.get(ARCHIVE_URL, params=params, timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as e:
        raise SourceUnavailable(f"Open-Meteo ERA5 unreachable: {e}") from e
    return r.json().get("hourly", {})
