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
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_CACHE = _ROOT / "data" / "eccc_wind_cache"

API = "https://api.weather.gc.ca/collections/climate-hourly/items"
PAGE = 10000                    # server honours this; larger windows paginate below
KMH_TO_KN = 1.0 / 1.852


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

    @property
    def calm(self) -> bool:
        return self.dir_deg is None


def _cache_path(key: str) -> Path:
    return _CACHE / f"{hashlib.sha256(key.encode()).hexdigest()[:20]}.json"


def _get(url: str, timeout: float) -> dict:
    raw = subprocess.run(["curl", "-sS", "-m", str(int(timeout)), url],
                         capture_output=True).stdout.decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except ValueError as e:
        raise RuntimeError(f"ECCC climate-hourly unavailable: {raw[:160]!r}") from e


def _parse(props: dict) -> WindObs | None:
    t, spd, drc = props.get("UTC_DATE"), props.get("WIND_SPEED"), props.get("WIND_DIRECTION")
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
    return WindObs(when.astimezone(timezone.utc), kn, None if d == 0 else float(d) * 10.0)


def fetch_hourly(start: date, end: date, *, station: str = "welcome_island",
                 timeout: float = 240.0) -> list[WindObs]:
    """Observed hourly wind over [start, end], oldest first. Cached; paginated.

    Records ECCC has flagged, or that carry no direction, are DROPPED rather than guessed at —
    a wind gate that silently substitutes for missing observations is measuring itself.
    """
    st = STATIONS[station]
    key = json.dumps(["eccc-hourly", st.stn_id, start.isoformat(), end.isoformat()])
    cp = _cache_path(key)
    if cp.exists():
        try:
            rows = json.loads(cp.read_text())
        except ValueError:
            rows = None
        if rows is not None:
            return [WindObs(datetime.fromisoformat(t).replace(tzinfo=timezone.utc), s, d)
                    for t, s, d in rows]

    out: list[WindObs] = []
    offset = 0
    while True:
        url = (f"{API}?STN_ID={st.stn_id}"
               f"&datetime={start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z"
               f"&limit={PAGE}&offset={offset}&f=json")
        d = _get(url, timeout)
        feats = d.get("features") or []
        for f in feats:
            w = _parse(f.get("properties") or {})
            if w is not None:
                out.append(w)
        if len(feats) < PAGE:
            break
        offset += PAGE
    out.sort(key=lambda w: w.time)
    _CACHE.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps([[w.time.replace(tzinfo=None).isoformat(), w.speed_kn, w.dir_deg]
                              for w in out]))
    return out
