"""Observed wind INSIDE Thunder Bay — Welcome Island, ECCC station 4061 (ADR-055).

WHY THIS EXISTS. The product's headline mechanism is west-quadrant wind driving upwelling on
the city/north shore, and the wind gate that checks it (`accumulate_wind_gate.py`) scores the
forecast against NDBC buoys 45027 and 45023 — 200 km and 130 km away. That was the best
over-water anemometer anyone here had found. It was not the best one available.

**WELCOME ISLAND (AUT)**, ECCC STN_ID 4061 at 48.36917,-89.11944, is an automatic station on an
island IN Thunder Bay, ~4 km from the LSOFS node the product forecasts at and inside the water
body the map draws. It reports wind hourly and has done since 1994-02-01, continuously, to the
present. Measured over 2026-05-01..08-13: 2497 hourly records for a 2496-hour window, every one
of them unflagged. That is a 32-year, gap-free, in-domain wind record.

TWO ECCC CONVENTIONS, both of which silently corrupt the data if missed:
  * WIND_DIRECTION is in TENS OF DEGREES, 1..36 — so 36 is 360 degrees (north), not 36.
  * WIND_DIRECTION == 0 means CALM, not north. Verified rather than assumed: over 2497 records,
    direction 0 occurs 83 times and the wind speed is 0 on every single one of them.
  * WIND_SPEED is km/h. The product's thresholds are written in knots (the Wedderburn bar is
    12-17 kt), so the conversion happens here, once, at the boundary.

T1 observation (a real anemometer, not an analysis) — which is the entire point. ADR-053 was
caused by scoring against a temporally relaxed analysis; this module exists so the wind track
cannot repeat that mistake.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import windowed

_ROOT = Path(__file__).resolve().parents[3]
_CACHE = _ROOT / "data" / "eccc_wind_cache"

API = "https://api.weather.gc.ca/collections/climate-hourly/items"
PAGE = 10000                    # server honours this; larger windows paginate below
CHUNK_DAYS = 120                # ~2900 records / ~3 MB — well inside the measured wall
KMH_TO_KN = 1.0 / 1.852
SCHEMA = 2                      # bump whenever the cached row shape changes


class PartialFetch(RuntimeError):
    """Some window returned nothing after every retry. Carries the report."""

    def __init__(self, msg, report):
        super().__init__(msg)
        self.report = report


@dataclass(frozen=True)
class Station:
    stn_id: int
    lat: float
    lon: float
    name: str
    hourly_from: str


# The one that matters. Kept as a table so a second in-domain station (if one is ever found)
# is a data change rather than a code change.
STATIONS = {
    "welcome_island": Station(4061, 48.36917, -89.11944, "WELCOME ISLAND (AUT)", "1994-02-01"),
}


@dataclass(frozen=True)
class WindObs:
    time: datetime          # UTC
    speed_kn: float
    dir_deg: float | None   # None == calm (ECCC reports direction 0 with speed 0)
    # AIR temperature at the same in-bay mast. Carried because it is the cleanest available
    # control for the confound that ruins a wind-vs-cooling test: the west wind that drives
    # upwelling also brings cold air, which cools the surface on its own. Controlling for air
    # temperature separates the two at the SAME place, which no second satellite pixel can do.
    air_c: float | None = None

    @property
    def calm(self) -> bool:
        return self.dir_deg is None


def _cache_path(key: str) -> Path:
    return _CACHE / f"{hashlib.sha256(key.encode()).hexdigest()[:20]}.json"


def _get(url: str, timeout: float, tries: int = 4) -> dict:
    """One request, retried. An overloaded ECCC returns an EMPTY BODY rather than an error, which
    a single-shot caller cannot distinguish from a station with no data."""
    raw = ""
    for attempt in range(tries):
        raw = subprocess.run(["curl", "-sS", "-m", str(int(timeout)), url],
                             capture_output=True).stdout.decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except ValueError:
            time.sleep(2 ** attempt * 2)
    raise RuntimeError(f"ECCC climate-hourly unavailable after {tries} tries: {raw[:160]!r}")


def _parse(props: dict) -> WindObs | None:
    t, spd, drc = props.get("UTC_DATE"), props.get("WIND_SPEED"), props.get("WIND_DIRECTION")
    air = props.get("TEMP")
    if props.get("TEMP_FLAG"):
        air = None
    try:
        air = None if air is None else float(air)
    except (TypeError, ValueError):
        air = None
    if t is None or spd is None or drc is None:
        return None
    # Any flag at all means ECCC has qualified the value; a validation record should not quietly
    # absorb estimated or corrected observations as if they were clean measurements.
    if props.get("WIND_SPEED_FLAG") or props.get("WIND_DIRECTION_FLAG"):
        return None
    try:
        kn = float(spd) * KMH_TO_KN
        d = int(drc)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(kn) or kn < 0 or not (0 <= d <= 36):
        return None
    when = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return WindObs(when.astimezone(timezone.utc), kn,
                   None if d == 0 else float(d) * 10.0, air)


def fetch_hourly_report(start: date, end: date, *, station: str = "welcome_island",
                        timeout: float = 240.0):
    """-> (records, FetchReport). The evidence-carrying form.

    Use this when an analysis can legitimately proceed on a partial record but must SAY that it
    did — a calibration over many seasons, say, where one gappy season is worth keeping as long
    as the output records which. `fetch_hourly` is the strict form and raises instead.
    """
    return _fetch(start, end, station=station, timeout=timeout, allow_partial=True)


def fetch_hourly(start: date, end: date, *, station: str = "welcome_island",
                 timeout: float = 240.0, allow_partial: bool = False) -> list[WindObs]:
    """Observed hourly wind over [start, end], oldest first. Cached; chunked; retried.

    Records ECCC has flagged, or that carry no direction, are DROPPED rather than guessed at —
    a wind gate that silently substitutes for missing observations is measuring itself.

    Raises `PartialFetch` if any window came back empty after every retry, because a silently
    short record is how a rate-limited year once vanished out of a "2018-2024" training set while
    the label kept saying 2018-2024. Pass ``allow_partial=True`` to accept a hole knowingly; use
    `fetch_hourly_report` if you also want the evidence of what is missing.
    """
    return _fetch(start, end, station=station, timeout=timeout,
                  allow_partial=allow_partial)[0]


def _fetch(start: date, end: date, *, station: str, timeout: float, allow_partial: bool):
    st = STATIONS[station]
    # ECCC returns HTTP 200 with features:[] for a window in the future, so chunks past today
    # come back "empty" and would trip PartialFetch — reporting a request that merely outran its
    # source as a failed fetch, and dropping a whole season. Clamp before keying, so the cache
    # cannot freeze a truncated series under a key that claims the full range.
    _s, end, _c, _w = windowed.clamp_window(start, end, not_after_today=True)
    if end < start:
        rep = windowed.FetchReport(requested_start=start, requested_end=end,
                                   effective_start=start, effective_end=end,
                                   chunk_days=CHUNK_DAYS)
        return [], rep
    # SCHEMA VERSION IN THE KEY. Adding air temperature did not invalidate the cache, so
    # an analysis ran with air_c=None on 95% of its rows and reported the resulting
    # n=104 partial correlation as if it were the n=2179 one. A cached row must never
    # outlive the shape it was written for.
    key = json.dumps(["eccc-hourly", SCHEMA, st.stn_id, start.isoformat(),
                      end.isoformat()])
    cp = _cache_path(key)
    if cp.exists():
        try:
            rows = json.loads(cp.read_text())
        except ValueError:
            rows = None
        if rows is not None:
            cached = [WindObs(datetime.fromisoformat(r[0]).replace(tzinfo=timezone.utc), r[1],
                              r[2], r[3] if len(r) > 3 else None) for r in rows]
            rep = windowed.FetchReport(requested_start=start, requested_end=end,
                                       effective_start=start, effective_end=end,
                                       chunk_days=CHUNK_DAYS, records=len(cached))
            return cached, rep

    # CHUNK/RETRY/REPORT is delegated to ingest/windowed (ADR-059) rather than reimplemented.
    # Measured here and encoded there: 2024-04-01..11-30 returns 6.0 MB in 1.5 s, while the same
    # request one month longer is killed with "connection reset by peer" — no HTTP error, no
    # partial page, an empty body indistinguishable from "this station has no wind". `limit` and
    # `offset` do not help; the server dies composing the response before paging applies.
    def _chunk(cs, ce):
        rows: list[WindObs] = []
        offset = 0
        while True:
            url = (f"{API}?STN_ID={st.stn_id}"
                   f"&datetime={cs.isoformat()}T00:00:00Z/{ce.isoformat()}T23:59:59Z"
                   f"&limit={PAGE}&offset={offset}&f=json")
            d = _get(url, timeout, tries=1)      # windowed owns the retry policy
            feats = d.get("features") or []
            for f in feats:
                w = _parse(f.get("properties") or {})
                if w is not None:
                    rows.append(w)
            if len(feats) < PAGE:
                break
            offset += PAGE
        return rows

    out, report = windowed.fetch_windowed(start, end, fetch=_chunk, chunk_days=CHUNK_DAYS)
    # SILENCE IS NOT AN OPTION BY DEFAULT. A window that never returned is a hole in a validation
    # record; a caller that genuinely wants partial data has to ask, and is handed the evidence.
    if report.partial and not allow_partial:
        raise PartialFetch(f"{st.name} {start}..{end}: {report.describe()}", report)
    out.sort(key=lambda w: w.time)
    _CACHE.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps([[w.time.replace(tzinfo=None).isoformat(), w.speed_kn, w.dir_deg,
                               w.air_c] for w in out]))
    return out, report
