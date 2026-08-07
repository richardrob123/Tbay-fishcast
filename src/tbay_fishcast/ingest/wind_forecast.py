"""Ensemble wind forecast via Open-Meteo Ensemble API (PLAN upwelling-probability task).

Point: LSOFS temperature-forecast skill decays with lead time, and upwelling is
wind-driven, so we want an honest PROBABILITY that upwelling-favorable wind occurs
each forecast day rather than trusting a single deterministic wind line. This module
fetches the ensemble; `features/upwelling.py` turns it into a probability, pure and
untested-by-network.

Endpoint (verified live 2026-08-05, see report): `ensemble-api.open-meteo.com`, NOT
`api.open-meteo.com` (the latter 404s for `/v1/ensemble` — the ensemble product lives
on its own subdomain, unlike the deterministic forecast/archive APIs used elsewhere
in this repo). Confirmed live against candidate `models=` values:
  - gfs025            -> 200, 30 perturbed members + 1 unsuffixed control (31 total)
  - gfs05              -> 200, 30 perturbed members + 1 unsuffixed control (31 total)
  - icon_seamless      -> 200, 39 perturbed members + 1 unsuffixed control (40 total)
  - gem_global         -> 200, 20 perturbed members + 1 unsuffixed control (21 total)
  - ecmwf_ifs025       -> request timed out (not verified)
Default is `gfs025`: NOAA GFS ensemble (GEFS), 0.25 deg, reliably available, good
member count (31) without ECMWF's timeout risk.

`gfs025` over `icon_seamless` is EVIDENCE-BACKED, not just availability: scripts/
validate_wind_model.py scored both against ERA5 over the over-lake point (35 d). ICON's finer
grid gave marginally better OVERALL MAE (2.01 vs 2.14 kn) BUT under-predicted the upwelling-
favorable WEST-quadrant wind by ~2.2 kn (favorable bias -2.21 vs GFS -0.50) and was slightly
worse on favorable-sector MAE — i.e. it would MISS west-blow upwelling events, the exact signal
this product exists for. "Finer = better" was refuted for the metric that counts; see
data/calib/wind_model_eval.json. Re-run the validator before revisiting this default.

Response `hourly` columns are flat: `wind_speed_10m` / `wind_direction_10m` (the
unsuffixed control member) plus `wind_speed_10m_memberNN` / `wind_direction_10m_memberNN`
for NN = 01..N. `fetch_ensemble_wind` groups these into one dict per member.
"""
from __future__ import annotations

import re

import requests

from . import SourceUnavailable

ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
TBAY_LAT, TBAY_LON = 48.4, -89.2
DEFAULT_MODEL = "gfs025"

_MEMBER_RE = re.compile(r"^wind_speed_10m(?:_member(\d+))?$")


def fetch_ensemble_wind(lat: float = TBAY_LAT, lon: float = TBAY_LON,
                         forecast_days: int = 5, models: str = DEFAULT_MODEL,
                         timeout: float = 60.0) -> dict:
    """Hourly ensemble wind (speed kn, direction deg) for the next `forecast_days`.

    Returns `{"time": [...], "members": [{"speed_kn": [...], "dir_deg": [...]}, ...]}`
    — one entry in `members` per ensemble member (control member first, then
    memberNN in ascending numeric order). Raises SourceUnavailable on any transport
    error (CLAUDE rule 5: staleness is loud, never silent).
    """
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": "wind_speed_10m,wind_direction_10m",
        "models": models,
        "forecast_days": forecast_days,
        "wind_speed_unit": "kn",
    }
    try:
        r = requests.get(ENSEMBLE_URL, params=params, timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as e:
        raise SourceUnavailable(f"Open-Meteo ensemble unreachable: {e}") from e
    hourly = r.json().get("hourly", {})
    return _parse_hourly(hourly)


def _parse_hourly(hourly: dict) -> dict:
    """Group flat `wind_speed_10m[_memberNN]` / `wind_direction_10m[_memberNN]`
    columns into a list of per-member {"speed_kn", "dir_deg"} series.

    The control member (unsuffixed) sorts first, then memberNN ascending.
    """
    time = hourly.get("time", [])

    # discover member suffixes from the speed columns; None = control (unsuffixed).
    suffixes: list[str | None] = []
    for key in hourly:
        m = _MEMBER_RE.match(key)
        if m:
            suffixes.append(m.group(1))

    def sort_key(s: str | None):
        return (-1, "") if s is None else (int(s), s)

    suffixes = sorted(set(suffixes), key=sort_key)

    members = []
    for suf in suffixes:
        speed_key = "wind_speed_10m" if suf is None else f"wind_speed_10m_member{suf}"
        dir_key = "wind_direction_10m" if suf is None else f"wind_direction_10m_member{suf}"
        if speed_key not in hourly or dir_key not in hourly:
            continue
        members.append({
            "speed_kn": list(hourly[speed_key]),
            "dir_deg": list(hourly[dir_key]),
        })

    return {"time": list(time), "members": members}
