"""ECCC hydrometric flows — GeoMet realtime discharge (river-plume strength for the run species).

STATUS (2026-08-07): host `api.weather.gc.ca` (GeoMet OGC-API) is REACHABLE from this environment
— verified live against the `hydrometric-realtime` collection (Kaministiquia 02AB006 ≈ 20 m³/s,
N. Current 02AB014 ≈ 0.08, Neebing 02AB008 ≈ 0.23). The earlier "403-blocked" note was stale; the
`hydrometric-daily-mean` archive lags ~7 months, so the LIVE plume signal uses `hydrometric-realtime`.

Why this matters: for the weak-temperature-cue species (salmon/steelhead) the river PLUME — flow,
and especially a RISING river after rain — draws staging fish to the mouth far more than the lake
thermal field does. This ingest supplies the current discharge + a short trailing window so the
feature layer can read a rising/steady/falling trend (the "first cool rain = starting gun" the
calendar already encodes). Thin I/O only; classification lives in `features/river_flow.py`.
"""
from __future__ import annotations

from datetime import datetime, timezone

import requests

from . import SourceUnavailable

GEOMET_STATIONS = "https://api.weather.gc.ca/collections/hydrometric-stations/items"
GEOMET_REALTIME = "https://api.weather.gc.ca/collections/hydrometric-realtime/items"
# Bounding box around Thunder Bay for station discovery.
TBAY_BBOX = "-89.7,48.2,-89.0,48.7"

# Verified gauge IDs for the three modelled river mouths (2026-08-07, live).
GAUGES = {
    "kam": "02AB006",       # Kaministiquia R. — the big plume
    "current": "02AB014",   # N. Current R. (below Boulevard Lake)
    "neebing": "02AB008",   # Neebing–McIntyre floodway
}


def list_stations(bbox: str = TBAY_BBOX, timeout: float = 60.0) -> list[dict]:
    """Enumerate hydrometric stations in a bbox."""
    try:
        r = requests.get(GEOMET_STATIONS,
                         params={"bbox": bbox, "limit": 100, "f": "json"},
                         timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as e:
        raise SourceUnavailable(f"GeoMet unreachable: {e}") from e
    return r.json().get("features", [])


def fetch_recent_discharge(station_number: str, *, limit: int = 900,
                           timeout: float = 45.0) -> list[tuple[datetime, float]]:
    """Recent realtime discharge samples (UTC datetime, m³/s), newest first.

    `limit` bounds how far back: realtime is sub-hourly, so ~900 samples ≈ several days — enough for
    a multi-day trend. Rows with a null DISCHARGE are dropped. Raises SourceUnavailable on transport
    error so the caller can degrade gracefully (a marker simply carries no flow, not a crash)."""
    try:
        r = requests.get(GEOMET_REALTIME,
                         params={"STATION_NUMBER": station_number, "limit": limit,
                                 "sortby": "-DATETIME", "f": "json"},
                         timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as e:
        raise SourceUnavailable(f"GeoMet realtime unreachable: {e}") from e
    out: list[tuple[datetime, float]] = []
    for f in r.json().get("features", []):
        p = f.get("properties", {})
        q, ts = p.get("DISCHARGE"), p.get("DATETIME")
        if q is None or ts is None:
            continue
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        out.append((t, float(q)))
    return out
