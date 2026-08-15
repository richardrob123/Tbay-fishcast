"""Lead-stratified archived wind FORECASTS — Open-Meteo previous-runs API (ADR-055).

THE QUESTION THIS MAKES ANSWERABLE. The map publishes upwelling out to +120 h, and every bit of
that rests on a wind forecast crossing the Wedderburn bar. `data/wind_gate_log.csv` scores the
wind forecast — but its columns are `issue,buoy,n_hours,mae_kn,bias_kn,...`, with no lead at
all. Aggregated over every lead at once, it cannot say whether the +120 h wind forecast can call
a sustained west-quadrant blow, which is precisely the claim the long-lead map makes. So the
product's forecast horizon has been assumed, never measured.

`wind_speed_10m_previous_dayN` gives, for each valid hour, the value from the model run
initialized N days earlier. Probed rather than assumed: at Thunder Bay, `past_days=120` returns
2904 hourly rows spanning 121 days with all seven leads (previous_day1..7) COMPLETE — no nulls.
Free, keyless, and re-fetchable, so a full season of lead-stratified forecasts exists today
rather than after months of forward accumulation.

ONE HONEST IMPRECISION, stated because it bounds what the lead axis means. `previous_dayN` is
indexed by DAY, not by hour: for a valid time T the initializing run is N days earlier, so the
true lead lies in [N*24, N*24+23] h depending on the hour of day. The nominal lead N*24 h is
therefore the FLOOR of the true lead, and each lead bin carries a uniform ~12 h of spread. That
is immaterial to a lead-decay curve — the bins stay ordered and non-overlapping — and it would
matter if anyone tried to read an exact hour off it. Nobody should.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_CACHE = _ROOT / "data" / "prev_runs_cache"

API = "https://previous-runs-api.open-meteo.com/v1/forecast"
# The SAME archive, addressed by explicit date range instead of a rolling window. Verified rather
# than assumed before pooling the two: over 240 overlapping hours at leads 1/3/5 the two endpoints
# agree to **0.0000 kn, bit for bit**. Without that check, pooling them would have been a guess
# about provenance dressed up as a bigger sample.
ARCHIVE_API = "https://historical-forecast-api.open-meteo.com/v1/forecast"
# Probed: 2023-07 returns the FIELDS but zero non-null values; 2024-07 is complete. The true
# boundary lies between, and requesting from here simply yields whatever exists — which is why
# callers must count what came back rather than trust the range they asked for.
ARCHIVE_FIRST = date(2024, 1, 1)
# Measured, like ECCC's: an 8-month window across 14 hourly fields is killed in flight
# and arrives as an empty body. 90 days x 14 fields is comfortably inside the wall.
ARCHIVE_CHUNK_DAYS = 90
MAX_PAST_DAYS = 120           # probed: 120 returns a complete series; the API accepts it plainly
LEAD_DAYS = (1, 2, 3, 4, 5, 6, 7)
MODEL = "gfs_seamless"        # the run the product itself uses (ADR-032) — score what we ship


def _cache_path(key: str) -> Path:
    return _CACHE / f"{hashlib.sha256(key.encode()).hexdigest()[:20]}.json"


def _parse(h, leads_d):
    times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in h["time"]]
    out = {}
    for n in leads_d:
        sp = h.get(f"wind_speed_10m_previous_day{n}") or []
        dr = h.get(f"wind_direction_10m_previous_day{n}") or []
        rows = [(t, float(s), float(d)) for t, s, d in zip(times, sp, dr)
                if s is not None and d is not None]
        if rows:
            out[n] = rows
    return out


def _hourly(url: str, timeout: float, tries: int = 4) -> dict:
    """One request, retried. Open-Meteo kills an oversized response mid-flight and the client
    sees an EMPTY BODY, not an error — the same shape of failure ECCC has, and indistinguishable
    from "no data" unless the caller retries and then counts what arrived."""
    raw = ""
    for attempt in range(tries):
        raw = subprocess.run(["curl", "-sS", "-m", str(int(timeout)), url],
                             capture_output=True).stdout.decode("utf-8", "replace")
        try:
            return json.loads(raw)["hourly"]
        except (ValueError, KeyError):
            time.sleep(2 ** attempt * 2)
    raise RuntimeError(f"Open-Meteo unavailable after {tries} tries: {raw[:160]!r}")


def _fields(leads_d):
    return ([f"wind_speed_10m_previous_day{n}" for n in leads_d]
            + [f"wind_direction_10m_previous_day{n}" for n in leads_d])


def fetch_lead_wind_range(lat: float, lon: float, start: date, end: date, *,
                          leads_d=LEAD_DAYS, model: str = MODEL,
                          timeout: float = 300.0):
    """Lead-stratified archived forecasts over an explicit [start, end] — back to ARCHIVE_FIRST.

    This is what makes the EVENT question answerable at all. The rolling 120-day window held
    three storms in 121 days, far too few to judge an event forecast on; whole prior seasons turn
    that from an anecdote into a sample.
    """
    start = max(start, ARCHIVE_FIRST)
    if end < start:
        return {}
    leads_d = tuple(sorted(set(int(n) for n in leads_d)))
    key = json.dumps(["om-archive-runs", lat, lon, start.isoformat(), end.isoformat(),
                      leads_d, model])
    cp = _cache_path(key)
    if cp.exists():
        try:
            raw = json.loads(cp.read_text())
        except ValueError:
            raw = None
        if raw:
            return {int(n): [(datetime.fromisoformat(t).replace(tzinfo=timezone.utc), s, d)
                             for t, s, d in rows] for n, rows in raw.items()}
    out: dict = {}
    cs = start
    while cs <= end:
        ce = min(cs + timedelta(days=ARCHIVE_CHUNK_DAYS - 1), end)
        url = (f"{ARCHIVE_API}?latitude={lat}&longitude={lon}"
               f"&hourly={','.join(_fields(leads_d))}"
               f"&start_date={cs.isoformat()}&end_date={ce.isoformat()}"
               f"&wind_speed_unit=kn&models={model}&timezone=UTC")
        for n, rows in _parse(_hourly(url, timeout), leads_d).items():
            out.setdefault(n, []).extend(rows)
        cs = ce + timedelta(days=1)
    for n in out:
        out[n] = sorted({t: (t, sp, dr) for t, sp, dr in out[n]}.values())
    _CACHE.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps({str(n): [[t.replace(tzinfo=None).isoformat(), s, d]
                                       for t, s, d in rows] for n, rows in out.items()}))
    return out


def fetch_lead_wind(lat: float, lon: float, *, past_days: int = MAX_PAST_DAYS,
                    leads_d=LEAD_DAYS, model: str = MODEL,
                    timeout: float = 300.0) -> dict[int, list[tuple[datetime, float, float]]]:
    """-> {lead_days: [(valid_utc, speed_kn, dir_deg), ...]} , each list oldest-first.

    Knots are requested from the API rather than converted here, matching `wind_archive`, so the
    unit the Wedderburn bar is written in is the unit that arrives.
    """
    past_days = min(int(past_days), MAX_PAST_DAYS)
    leads_d = tuple(sorted(set(int(n) for n in leads_d)))
    key = json.dumps(["om-prev-runs", lat, lon, past_days, leads_d, model,
                      datetime.now(timezone.utc).date().isoformat()])
    cp = _cache_path(key)
    if cp.exists():
        try:
            raw = json.loads(cp.read_text())
        except ValueError:
            raw = None
        if raw:
            return {int(n): [(datetime.fromisoformat(t).replace(tzinfo=timezone.utc), s, d)
                             for t, s, d in rows] for n, rows in raw.items()}

    url = (f"{API}?latitude={lat}&longitude={lon}&hourly={','.join(_fields(leads_d))}"
           f"&past_days={past_days}&forecast_days=1&wind_speed_unit=kn"
           f"&models={model}&timezone=UTC")
    out = _parse(_hourly(url, timeout), leads_d)
    _CACHE.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps({str(n): [[t.replace(tzinfo=None).isoformat(), s, d]
                                       for t, s, d in rows] for n, rows in out.items()}))
    return out
