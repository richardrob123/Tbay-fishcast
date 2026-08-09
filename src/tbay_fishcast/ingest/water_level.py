"""CHS water-level gauges — the OBSERVED half of the upwelling story (ADR-043).

The upwelling phase banner (setup → peak → relaxation) is the tool's headline timing signal, and
until now it was inferred entirely from WIND: a Wedderburn-style threshold on CYQT observations
plus forecast wind. That is a prediction of a forcing, never a measurement of the response. Every
other layer in this system carries a measured error bar; the phase layer carried none.

Water level closes that gap, and the physics is direct. On this shore an offshore (west-quadrant)
blow drives surface water away from the coast; the shoreline water level DRAWS DOWN and the
thermocline tilts UP toward the surface — that rise is the upwelling. So a falling nearshore
water level is the physical fingerprint of upwelling setup, and a rebound marks relaxation. If the
wind model says "setup" while the gauge shows no drawdown, the phase call deserves less
confidence, and the tool should say so rather than assert.

Two CHS gauges sit inside our map: Thunder Bay harbour (10050) and Mission River at the Kam mouth
(10047), both reporting at 3-minute resolution on the free DFO IWLS API (no key).

CAVEAT carried in the output, not hidden: nearshore level also responds to barometric pressure
(inverse barometer), basin-scale seiche, and slow seasonal lake-level change. This module reports
the SHORT-TERM anomaly against a local baseline, which suppresses the seasonal component but does
not separate wind setup from seiche or pressure. It is corroboration, not attribution.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

API = "https://api-iwls.dfo-mpo.gc.ca/api/v1"

# station-id -> (code, label, lat, lon). Verified live 2026-08-09.
GAUGES = {
    "thunder_bay": ("5cebf1e23d0f4a073c4bc0fc", "10050", "Thunder Bay", 48.4094, -89.2173),
    "mission_river": ("69cbf99e9d2027ef2344b3da", "10047", "Mission River", 48.3674, -89.2361),
}

# A drawdown/rebound has to clear the gauge's own short-term noise to mean anything. Lake Superior
# wind setup at a shore station runs a few cm to ~20 cm; 3 cm is comfortably above instrument and
# wave noise while still catching a modest event. Bounded judgment, stated (PROVENANCE_LEDGER).
ANOMALY_M = 0.03


@dataclass(frozen=True)
class LevelState:
    station: str
    n: int
    latest_m: float | None
    baseline_m: float | None
    anomaly_m: float | None      # latest window mean minus the preceding baseline mean
    trend: str                   # "drawdown" | "rebound" | "steady" | "unknown"
    upwelling_favorable: bool    # a falling shore level is the upwelling fingerprint
    span_h: float
    note: str

    def as_dict(self) -> dict:
        d = {k: getattr(self, k) for k in
             ("station", "n", "latest_m", "baseline_m", "anomaly_m", "trend",
              "upwelling_favorable", "span_h", "note")}
        return d


def fetch_levels(station: str = "thunder_bay", hours: int = 72, *, now=None) -> list[tuple]:
    """[(utc_datetime, level_m)] of OBSERVED water level (time-series wlo), oldest first."""
    if station not in GAUGES:
        raise ValueError(f"unknown gauge {station!r}")
    sid = GAUGES[station][0]
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    url = (f"{API}/stations/{sid}/data?time-series-code=wlo"
           f"&from={start.strftime('%Y-%m-%dT%H:%M:%SZ')}"
           f"&to={end.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    req = urllib.request.Request(url, headers={"User-Agent": "tbay-fishcast/1.0"})
    with urllib.request.urlopen(req, timeout=45) as r:
        raw = json.loads(r.read())
    out = []
    for row in raw:
        v, t = row.get("value"), row.get("eventDate")
        if v is None or not t:
            continue
        try:
            out.append((datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc),
                        float(v)))
        except (ValueError, TypeError):
            continue
    out.sort()
    return out


def classify(series, station: str = "thunder_bay", *, recent_h: float = 6.0,
             baseline_h: float = 30.0) -> LevelState:
    """Short-term shore-level anomaly: mean of the last `recent_h` minus the mean of the
    `baseline_h` window preceding it. Pure — takes the series, does no I/O, so it is testable
    without the network (the golden-fixture discipline in CLAUDE rule 2)."""
    if not series:
        return LevelState(station, 0, None, None, None, "unknown", False, 0.0,
                          "no observations returned")
    series = sorted(series)
    t_end = series[-1][0]
    span_h = (t_end - series[0][0]).total_seconds() / 3600.0
    recent = [v for t, v in series if (t_end - t).total_seconds() <= recent_h * 3600]
    base = [v for t, v in series
            if recent_h * 3600 < (t_end - t).total_seconds() <= (recent_h + baseline_h) * 3600]
    latest = series[-1][1]
    if len(recent) < 3 or len(base) < 10:
        return LevelState(station, len(series), latest, None, None, "unknown", False, span_h,
                          f"insufficient span for an anomaly (recent n={len(recent)}, "
                          f"baseline n={len(base)})")
    r_mean = sum(recent) / len(recent)
    b_mean = sum(base) / len(base)
    anom = r_mean - b_mean
    if anom <= -ANOMALY_M:
        trend, fav = "drawdown", True
    elif anom >= ANOMALY_M:
        trend, fav = "rebound", False
    else:
        trend, fav = "steady", False
    return LevelState(
        station, len(series), round(latest, 3), round(b_mean, 3), round(anom, 3), trend, fav,
        round(span_h, 1),
        "shore-level anomaly vs the preceding window; responds to wind setup AND seiche AND "
        "barometric pressure — corroboration of the wind-derived phase, not attribution")


def observed_state(station: str = "thunder_bay", hours: int = 72) -> LevelState:
    """Convenience: fetch + classify. Any failure degrades to an 'unknown' state, never raises —
    the map must not depend on a gauge being up (rule 5: degrade loudly, don't break)."""
    try:
        return classify(fetch_levels(station, hours), station)
    except Exception as e:  # noqa: BLE001
        return LevelState(station, 0, None, None, None, "unknown", False, 0.0,
                          f"gauge unavailable: {str(e)[:60]}")
