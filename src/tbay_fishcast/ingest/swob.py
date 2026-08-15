"""LIVE in-bay wind — Welcome Island via ECCC SWOB-realtime (ADR-056).

WHAT THIS REPLACES AND WHY. The upwelling phase classifier has always read CYQT, an airport
anemometer 16 km inland, because that was the only live wind observation anyone here had found.
ADR-056 measured what that costs, over 8,567 held-out hours against the in-bay station:

    airport under-reads the lake                     -2.95 kn all hours, -3.77 kn in the W quadrant
    the two disagree about SECTOR MEMBERSHIP          19.7% of hours
    phase classifier run on the airport               misses 71% of the hours the lake is blowing

and — the finding that decides the design — **no scalar correction fixes it**. Best-fit corrected
airport traded 101 missed blows for 50 missed plus 53 false, no net gain, because the discrepancy
is directional and regime-dependent rather than a constant offset. The information is absent, not
mis-scaled. So the station changes; the threshold does not.

`ingest/eccc_wind.py` reads the same instrument from the CLIMATE archive, which lags ~2 days and
is therefore useless to a heartbeat. This module reads the REAL-TIME SWOB feed of the same
station, hourly and current to the hour, and is the live path.

CYQT REMAINS THE FALLBACK, loudly (rule 5). A silent revert to a station that misses seven blows
in ten is worse than no upwelling layer at all, so the caller is handed enough to say so.
"""
from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .eccc_wind import KMH_TO_KN

API = "https://api.weather.gc.ca/collections/swob-realtime/items"

# Welcome Island (AUT). MSC/climate id as published by the SWOB station registry — note this is
# NOT the STN_ID 4061 the climate-hourly archive uses for the same station; the two ECCC services
# key the same instrument differently, which is exactly the sort of thing that silently returns
# an empty series if assumed.
WELCOME_ISLAND_CLIM_ID = "6049443"
WELCOME_ISLAND_LAT, WELCOME_ISLAND_LON = 48.369485, -89.119559

# SWOB carries several averaging windows for the same instrument. The hourly mean is the right
# one here: the phase classifier integrates a trailing wind RUN, so a 2-minute spot reading would
# inject gust structure into an integral that wants a sustained mean.
SPD = "avg_wnd_spd_10m_pst1hr"
DIR = "avg_wnd_dir_10m_pst1hr"
QA_GOOD = 100          # SWOB marks a value that passed its checks with 100


@dataclass(frozen=True)
class WindRecord:
    """Deliberately the same shape as `metar.WindRecord` so the two are interchangeable."""
    time: datetime
    dir_deg: float | None
    speed_kn: float | None


def _num(p, key):
    v = p.get(key)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def fetch_recent_wind(hours: int = 72, *, clim_id: str = WELCOME_ISLAND_CLIM_ID,
                      timeout: float = 60.0) -> list[WindRecord]:
    """Observed hourly wind at the in-bay station over the last `hours`, oldest first.

    Values SWOB has flagged are dropped rather than used — this feed decides whether the product
    tells someone the lake is turning over, and an unchecked reading is not worth that.
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    url = (f"{API}?clim_id-value={clim_id}"
           f"&datetime={start.strftime('%Y-%m-%dT%H:%M:%SZ')}/"
           f"{end.strftime('%Y-%m-%dT%H:%M:%SZ')}&limit=1000&f=json")
    raw = subprocess.run(["curl", "-sS", "-m", str(int(timeout)), url],
                         capture_output=True).stdout.decode("utf-8", "replace")
    try:
        feats = json.loads(raw).get("features") or []
    except ValueError as e:
        raise RuntimeError(f"SWOB unavailable: {raw[:160]!r}") from e

    out = [w for w in (parse_feature(f) for f in feats) if w is not None]
    out.sort(key=lambda w: w.time)
    return out


def parse_feature(f: dict) -> WindRecord | None:
    """One SWOB feature -> WindRecord, or None if it carries no usable, unflagged wind."""
    p = f.get("properties") or {}
    t = p.get("date_tm-value")
    spd, drc = _num(p, SPD), _num(p, DIR)
    if t is None or spd is None:
        return None
    for k in (f"{SPD}-qa", f"{DIR}-qa"):
        q = _num(p, k)
        if q is not None and q != QA_GOOD:
            return None
    when = datetime.fromisoformat(str(t).replace("Z", "+00:00")).astimezone(timezone.utc)
    # Calm: a zero mean speed carries no meaningful direction. Same convention as the climate
    # archive and the METAR reader, so the three are safe to substitute for one another.
    return WindRecord(when, None if spd == 0 else drc, spd * KMH_TO_KN)


def age_hours(obs: list[WindRecord], *, now: datetime | None = None) -> float | None:
    """How stale is the newest observation? Rule 5 — staleness is loud, so callers can say it."""
    if not obs:
        return None
    now = now or datetime.now(timezone.utc)
    return round((now - obs[-1].time).total_seconds() / 3600.0, 2)
