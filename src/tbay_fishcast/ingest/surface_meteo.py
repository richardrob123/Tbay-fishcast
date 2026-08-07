"""Surface scalar meteorology (mean-sea-level pressure, cloud cover) from Open-Meteo forecast.

Separate from `wind_forecast` (which hits the ENSEMBLE endpoint for per-member wind): pressure and
cloud are deterministic single series best taken from the standard forecast API. Thin I/O only —
returns a tidy dict of UTC-stamped series; all interpretation lives in `features/barometric.py`
(ingest/feature separation). Reachability confirmed through the agent proxy (HTTPS 200).
"""
from __future__ import annotations

import requests

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TBAY_LAT, TBAY_LON = 48.43, -89.15


def fetch_pressure_cloud(lat: float = TBAY_LAT, lon: float = TBAY_LON,
                         *, past_days: int = 1, forecast_days: int = 1,
                         timeout: float = 30.0) -> dict:
    """Hourly MSL pressure (hPa) + total cloud cover (%) around the issue day.

    Returns {"time": [ISO...], "pressure_hpa": [...], "cloud_pct": [...]}. `past_days` gives the
    trailing hours needed to compute a pressure TREND at the issue time. Raises on transport/HTTP
    error so the caller can degrade gracefully (the build treats a failure as 'no barometric prior',
    not a crash — staleness is loud, rule 5)."""
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": "pressure_msl,cloud_cover",
        "past_days": past_days, "forecast_days": forecast_days,
        "timezone": "UTC",
    }
    r = requests.get(FORECAST_URL, params=params, timeout=timeout)
    r.raise_for_status()
    h = r.json().get("hourly", {})
    return {
        "time": list(h.get("time", [])),
        "pressure_hpa": list(h.get("pressure_msl", [])),
        "cloud_pct": list(h.get("cloud_cover", [])),
    }
