"""Observed surface wind from METAR — the *actual* recent wind at Thunder Bay.

The upwelling-phase indicator (features/upwelling_phase.py) must answer "where in the
upwelling cycle are we RIGHT NOW", and the honest way to do that for day 0 is with
OBSERVED wind, not a model. Thunder Bay International (CYQT) reports an hourly METAR from
a real anemometer, right on the bay's doorstep — so the "past couple days" that condition
today are actual observations, never our own forecast fed back in (the operator's explicit
requirement). Forecast DAYS still use the ensemble (ingest/wind_forecast.py); this module
is only the observed tail.

Source: aviationweather.gov METAR JSON API (NOAA/NWS, public, no key). `wdir` is degrees
true FROM which the wind blows, or the string "VRB" when variable/light — reported as
`None` (no usable direction, so it can't count toward a west-quadrant run). `wspd` is in
KNOTS already (the units the Wedderburn threshold is quoted in), so no conversion.

CAVEAT (documented, not hidden): CYQT is an inland airport (elev ~197 m, ~10 km from the
shore). Over-lake wind runs stronger and less deflected, so airport SPEED reads low versus
the true over-water blow — the phase classifier uses a slightly lower knots threshold to
compensate (see upwelling_phase.py). DIRECTION and TIMING of a synoptic west-quadrant
event — which is what actually drives upwelling — transfer well from the airport.

Pure HTTP + parse. No LLM (ADR-001).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from . import SourceUnavailable

METAR_URL = "https://aviationweather.gov/api/data/metar"
CYQT = "CYQT"  # Thunder Bay International — the bay's own anemometer
CYQT_LAT, CYQT_LON = 48.374, -89.330


@dataclass(frozen=True)
class WindObs:
    time: datetime          # UTC
    dir_deg: float | None   # degrees FROM; None when variable/calm (no usable direction)
    speed_kn: float


def parse_metar_json(records: list[dict]) -> list[WindObs]:
    """Turn the aviationweather METAR JSON array into sorted WindObs (oldest first).

    Skips records with no wind speed. `wdir == 'VRB'` (or any non-numeric) becomes
    `dir_deg=None`: a variable/light wind has no direction to test against the sector.
    """
    out: list[WindObs] = []
    for r in records:
        ws = r.get("wspd")
        rt = r.get("reportTime") or r.get("obsTime")
        if ws is None or rt is None:
            continue
        try:
            speed = float(ws)
        except (TypeError, ValueError):
            continue
        if speed < 0 or speed > 200:  # physical sanity (kn)
            continue
        wd = r.get("wdir")
        try:
            dir_deg: float | None = float(wd)
            if dir_deg < 0 or dir_deg > 360:
                dir_deg = None
        except (TypeError, ValueError):
            dir_deg = None  # 'VRB', None, etc.
        t = _parse_time(rt)
        if t is None:
            continue
        out.append(WindObs(time=t, dir_deg=dir_deg, speed_kn=speed))
    out.sort(key=lambda w: w.time)
    return out


def _parse_time(rt) -> datetime | None:
    if isinstance(rt, (int, float)):  # obsTime epoch seconds
        return datetime.fromtimestamp(int(rt), tz=timezone.utc)
    try:
        s = str(rt).replace("Z", "+00:00")
        t = datetime.fromisoformat(s)
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def fetch_recent_wind(station: str = CYQT, hours: int = 72,
                      timeout: float = 45.0) -> list[WindObs]:
    """Observed hourly wind for the last `hours` at `station` (default CYQT), oldest first.

    Raises SourceUnavailable on any transport error (CLAUDE rule 5: staleness is loud —
    the phase must degrade to 'unknown', never silently invent a calm history).
    """
    params = {"ids": station, "format": "json", "hours": hours}
    try:
        r = requests.get(METAR_URL, params=params, timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as e:
        raise SourceUnavailable(f"METAR {station} unreachable: {e}") from e
    try:
        data = r.json()
    except ValueError as e:
        raise SourceUnavailable(f"METAR {station} bad JSON: {e}") from e
    if not isinstance(data, list):
        raise SourceUnavailable(f"METAR {station} unexpected payload: {type(data).__name__}")
    return parse_metar_json(data)
