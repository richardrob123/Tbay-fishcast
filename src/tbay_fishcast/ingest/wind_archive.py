"""Historical hourly wind for hindcast stratification (ADR-051).

`ingest/metar.py` reads CYQT's LAST 72 HOURS and `ingest/wind_forecast.py` reads GEFS forward.
Neither reaches back a season, so no hindcast could ask the question that matters most here:
does the model's error depend on the WIND REGIME — specifically, on whether the coast is
upwelling? Open-Meteo's archive endpoint serves hourly 10 m wind back decades, free and keyless,
which is what makes that question answerable at all.

Reanalysis, not observation: this is ERA5-family gridded wind, T2. It is used only to CLASSIFY a
day into an upwelling phase for stratification, never to force anything or to produce a number the
product reports. For that job a reanalysis is appropriate — it has no gaps, and gaps in a
stratifying variable silently drop exactly the stormy days you most want to keep.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_CACHE = _ROOT / "data" / "wind_archive_cache"

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
TBAY_LAT, TBAY_LON = 48.43, -89.21


def _cache_path(key: str) -> Path:
    return _CACHE / f"{hashlib.sha256(key.encode()).hexdigest()[:20]}.json"


def fetch_hourly_wind(start: date, end: date, *, lat: float = TBAY_LAT, lon: float = TBAY_LON,
                      timeout: float = 180.0):
    """-> (times, dir_deg, speed_kn), hourly UTC over [start, end].

    Knots are requested from the API rather than converted here, so the unit the product's
    thresholds are written in (the Wedderburn bar is 12-17 kt) is the unit that arrives.
    """
    # CLAMP TO THE ARCHIVE'S RANGE. Open-Meteo answers an end_date past its coverage with a hard
    # error for the WHOLE request, not a short series. Callers legitimately ask past today —
    # a hindcast needs wind out to its longest lead's valid time — so an unclamped request fails
    # every single run rather than returning what exists. Clamping here, at the one place that
    # knows the API's contract, keeps every caller from having to know it. The cache key uses the
    # CLAMPED dates so tomorrow's wider window is a fresh fetch rather than a truncated hit.
    end = min(end, datetime.now(timezone.utc).date())
    if end < start:
        return [], [], []
    key = json.dumps(["om-archive", lat, lon, start.isoformat(), end.isoformat()])
    cp = _cache_path(key)
    if cp.exists():
        try:
            d = json.loads(cp.read_text())
        except ValueError:
            d = None
        if d:
            return ([datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in d["time"]],
                    d["dir"], d["kn"])
    url = (f"{ARCHIVE_URL}?latitude={lat}&longitude={lon}"
           f"&start_date={start.isoformat()}&end_date={end.isoformat()}"
           f"&hourly=wind_speed_10m,wind_direction_10m&wind_speed_unit=kn&timezone=UTC")
    raw = subprocess.run(["curl", "-sS", "-m", str(int(timeout)), url],
                         capture_output=True).stdout.decode("utf-8", "replace")
    try:
        d = json.loads(raw)["hourly"]
    except (ValueError, KeyError) as e:
        raise RuntimeError(f"Open-Meteo archive unavailable: {raw[:120]!r}") from e
    times, dirs, kn = [], [], []
    for t, s, dd in zip(d["time"], d["wind_speed_10m"], d["wind_direction_10m"]):
        if s is None or dd is None:
            continue
        times.append(datetime.fromisoformat(t).replace(tzinfo=timezone.utc))
        kn.append(float(s))
        dirs.append(float(dd))
    _CACHE.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps({"time": [t.replace(tzinfo=None).isoformat() for t in times],
                              "dir": dirs, "kn": kn}))
    return times, dirs, kn
