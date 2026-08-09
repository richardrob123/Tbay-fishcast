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
# MAIN-STEM gauges, with drainage area (km2) and the discharge on the lidar acquisition date.
#
# THE FIX THIS RECORDS. The first mapping sent the Current River to 02AB014 — "NORTH CURRENT RIVER",
# a 105 km2 TRIBUTARY — while the main stem has its own active station 02AB021 at 406.8 km2, nearly
# four times the catchment. McVicar and McIntyre were likewise treated as ungauged when both have
# active stations. All five rivers are gauged on their main stems; we simply had the wrong list.
#
# q_ref_cms is the DAILY MEAN DISCHARGE ON 2024-05-06, the day the lidar was flown. It is what makes
# the measured channel widths usable: those widths are the WETTED width at that flow, and applying
# them to today's discharge without rescaling is what produced a 3.5 cm "depth" for a 28 m river.
# See features/hydraulics.width_at_flow.
#
# CROSS-CHECK, and the reason to trust the new list: specific discharge q = Q/A should be similar
# for neighbouring catchments sharing one climate. Measured 2026-08-09: Current 0.66, Neebing 0.80,
# McIntyre 0.75 L/s/km2 — a tight cluster. Kam (3.27) and McVicar (1.99) sit out, both explicably:
# the Kam is a 6,481 km2 regulated system with large lake storage, and McVicar is a small urban
# catchment whose impervious surfaces raise runoff. A gauge on the WRONG watercourse shows up here
# as an inexplicable outlier, which is exactly how the North Current error would have been caught.
GAUGES = {
    "kam": "02AB006",       # Kaministiquia at Kaministiquia — the big plume
    "current": "02AB021",   # Current R. AT STEPSTONE — main stem (was 02AB014, a tributary)
    "neebing": "02AB008",   # Neebing-McIntyre floodway
    "mcintyre": "02AB020",  # McIntyre above Thunder Bay
    "mcvicar": "02AB019",   # McVicar Creek at Thunder Bay
}

GAUGE_META = {
    "kam": {"station": "02AB006", "area_km2": 6481.0, "q_ref_cms": 42.00},
    "current": {"station": "02AB021", "area_km2": 406.8, "q_ref_cms": 11.80},
    "neebing": {"station": "02AB008", "area_km2": 208.1, "q_ref_cms": 3.62},
    "mcintyre": {"station": "02AB020", "area_km2": 81.41, "q_ref_cms": 1.74},
    "mcvicar": {"station": "02AB019", "area_km2": 44.77, "q_ref_cms": 0.89},
}
LIDAR_REF_DATE = "2024-05-06"


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
