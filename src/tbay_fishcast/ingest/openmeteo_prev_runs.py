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
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_CACHE = _ROOT / "data" / "prev_runs_cache"

API = "https://previous-runs-api.open-meteo.com/v1/forecast"
MAX_PAST_DAYS = 120           # probed: 120 returns a complete series; the API accepts it plainly
LEAD_DAYS = (1, 2, 3, 4, 5, 6, 7)
MODEL = "gfs_seamless"        # the run the product itself uses (ADR-032) — score what we ship


def _cache_path(key: str) -> Path:
    return _CACHE / f"{hashlib.sha256(key.encode()).hexdigest()[:20]}.json"


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

    fields = ([f"wind_speed_10m_previous_day{n}" for n in leads_d]
              + [f"wind_direction_10m_previous_day{n}" for n in leads_d])
    url = (f"{API}?latitude={lat}&longitude={lon}&hourly={','.join(fields)}"
           f"&past_days={past_days}&forecast_days=1&wind_speed_unit=kn"
           f"&models={model}&timezone=UTC")
    out_raw = subprocess.run(["curl", "-sS", "-m", str(int(timeout)), url],
                             capture_output=True).stdout.decode("utf-8", "replace")
    try:
        h = json.loads(out_raw)["hourly"]
    except (ValueError, KeyError) as e:
        raise RuntimeError(f"Open-Meteo previous-runs unavailable: {out_raw[:160]!r}") from e

    times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in h["time"]]
    out: dict[int, list[tuple[datetime, float, float]]] = {}
    for n in leads_d:
        sp = h.get(f"wind_speed_10m_previous_day{n}") or []
        dr = h.get(f"wind_direction_10m_previous_day{n}") or []
        rows = [(t, float(s), float(d)) for t, s, d in zip(times, sp, dr)
                if s is not None and d is not None]
        if rows:
            out[n] = rows
    _CACHE.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps({str(n): [[t.replace(tzinfo=None).isoformat(), s, d]
                                       for t, s, d in rows] for n, rows in out.items()}))
    return out
