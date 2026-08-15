"""Archived CYQT METAR wind — the same stream the product reads live (ADR-056).

WHY THIS ARCHIVE AND NOT ECCC. The obvious source for historical Thunder Bay airport wind is
ECCC's climate archive, and it is a dead end: station 4055 "THUNDER BAY A" stops reporting hourly
on **2012-04-12**, and no other ECCC station near the airport carries an hourly wind record. The
airport observation that exists today is the METAR, which is exactly what `ingest/metar.py`
consumes live from aviationweather.gov — but that endpoint serves only the last ~72 hours.

Iowa State's ASOS archive serves the SAME METAR stream back decades. That identity is the point:
the archive being scored is the observation the product actually reads, so a correction fitted
here applies directly to what the heartbeat consumes rather than to a cousin of it.

CONVENTIONS, verified against 3426 records over 2025-05..2025-09 rather than assumed:
  * `sknt` is already knots — the unit the Wedderburn bar is written in. No conversion.
  * `drct == 0` occurs ONLY when `sknt == 0` (189 of 189 cases): direction zero is CALM. North is
    reported as 360, never 0.
  * Rows carry SPECIs between the hourly METARs, so the series is irregular; callers asking for
    hourly data get the observation nearest each hour within a stated tolerance, never a
    resampled or interpolated value.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import subprocess
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import windowed

_ROOT = Path(__file__).resolve().parents[3]
_CACHE = _ROOT / "data" / "asos_cache"

API = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
CYQT = "CYQT"                 # Thunder Bay International — the product's land wind reference
CYQT_LAT, CYQT_LON = 48.372, -89.324
HOUR_TOL_MIN = 10             # an observation further than this from the hour is not that hour


@dataclass(frozen=True)
class WindObs:
    time: datetime
    speed_kn: float
    dir_deg: float | None     # None == calm

    @property
    def calm(self) -> bool:
        return self.dir_deg is None


def _cache_path(key: str) -> Path:
    return _CACHE / f"{hashlib.sha256(key.encode()).hexdigest()[:20]}.json"


def parse_row(r: dict) -> WindObs | None:
    """One archive row -> WindObs, or None if it carries no usable wind."""
    d, s = r.get("drct"), r.get("sknt")
    if not d or not s:
        return None
    try:
        dd, ss = float(d), float(s)
    except ValueError:
        return None
    if ss < 0 or not (0 <= dd <= 360):
        return None
    when = datetime.fromisoformat(r["valid"]).replace(tzinfo=timezone.utc)
    # Calm: METAR 00000KT arrives as direction 0 with speed 0. Reading it as a northerly would
    # inject a phantom wind into every hour the airport was still.
    return WindObs(when, ss, None if ss == 0 else dd)


def fetch(start: date, end: date, *, station: str = CYQT,
          timeout: float = 300.0) -> list[WindObs]:
    """All wind observations over [start, end), oldest first. Cached per (station, range)."""
    # CLAMP FIRST, THEN KEY (ADR-059). Keying on the REQUESTED range froze a truncated series:
    # data/asos_cache/9f9c9982467d16201c22.json was keyed 2026-04-01..2026-11-30 and held data
    # only to 2026-08-14, so every later run in 2026 would have replayed mid-August data while
    # wind_exposure.json asserted the holdout covered the whole season. Verified on disk, not
    # supposed. The clamp is the shared one so the rule cannot drift per module.
    _s, end, _clamped, _why = windowed.clamp_window(start, end, not_after_today=True)
    if end < start:
        return []
    key = json.dumps(["asos", station, start.isoformat(), end.isoformat()])
    cp = _cache_path(key)
    if cp.exists():
        try:
            rows = json.loads(cp.read_text())
        except ValueError:
            rows = None
        if rows is not None:
            return [WindObs(datetime.fromisoformat(t).replace(tzinfo=timezone.utc), s, d)
                    for t, s, d in rows]

    url = (f"{API}?station={station}&data=drct&data=sknt"
           f"&year1={start.year}&month1={start.month}&day1={start.day}"
           f"&year2={end.year}&month2={end.month}&day2={end.day}"
           f"&tz=UTC&format=onlycomma&missing=empty&trace=empty")
    # Iowa State rate-limits, and it does so by returning a PLAIN-TEXT NOTICE with a 200 status
    # rather than an error code. Without a retry a multi-year fetch quietly loses whole years —
    # seen live: a 2018-2024 training pull came back missing 2021 and 2024 entirely, which a
    # caller that only checks for exceptions would have fitted on without noticing.
    # A HEADER IS NOT A COMPLETE RESPONSE. Checking only the first 200 bytes accepted any body
    # that merely STARTED with the CSV header — including one cut short by the timeout or dropped
    # mid-stream, which then got cached as if whole. curl exits non-zero on an incomplete
    # transfer, so its RETURN CODE is the signal the content check cannot give us.
    raw = ""
    for attempt in range(5):
        proc = subprocess.run(["curl", "-sS", "-m", str(int(timeout)), url],
                              capture_output=True)
        raw = proc.stdout.decode("utf-8", "replace")
        if proc.returncode == 0 and "station,valid" in raw[:200]:
            break
        raw = ""
        time.sleep(2 ** attempt * 3)
    if not raw:
        raise RuntimeError(f"ASOS archive unavailable or truncated after 5 tries: {url[-60:]!r}")
    out = [w for w in (parse_row(r) for r in csv.DictReader(io.StringIO(raw))) if w is not None]
    out.sort(key=lambda w: w.time)
    _CACHE.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps([[w.time.replace(tzinfo=None).isoformat(), w.speed_kn, w.dir_deg]
                              for w in out]))
    return out


def hourly(obs: list[WindObs], *, tol_min: int = HOUR_TOL_MIN) -> dict[datetime, WindObs]:
    """-> {hour: nearest observation within tol_min}. Hours with no close observation are ABSENT.

    Nearest-within-tolerance rather than resampling: an interpolated wind is a number nobody
    measured, and this record exists to be compared against another instrument.
    """
    best: dict[datetime, tuple[float, WindObs]] = {}
    for w in obs:
        hr = w.time.replace(minute=0, second=0, microsecond=0)
        for cand in (hr, hr + timedelta(hours=1)):
            gap = abs((w.time - cand).total_seconds()) / 60.0
            if gap <= tol_min and (cand not in best or gap < best[cand][0]):
                best[cand] = (gap, w)
    return {k: v for k, (_g, v) in sorted(best.items())}
