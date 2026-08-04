"""Upwelling-event detection over an LSOFS temperature series (PLAN task 4).

Definition (from PLAN + events_calendar.yaml):
  an upwelling event = ΔT@6m drops by > 4 °C within 24 h at a Superior-shore
  station, OR T@6m falls to <= 12 °C (the laker-band trigger).

Pure, deterministic, no wind input required (wind cross-tabulation is a separate
step once ERA5 is reachable). Operates on a tidy, time-sorted temp series for one
station/depth. This is a real detector, not a stub — but its thresholds are
PROVISIONAL Phase-0 numbers, tuned on 2022-2024 (here: 2024+) and validated on
held-out years before any gate is claimed (ADR-004).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

DROP_C_24H = 4.0   # ΔT@6m over 24 h that flags an event
COLD_BAND_C = 12.0  # absolute T@6m at/below which the laker band is at shore


@dataclass(frozen=True)
class UpwellingEvent:
    onset: datetime
    min_temp_c: float
    drop_c_24h: float
    trigger: str  # 'drop' | 'cold_band' | 'both'


def detect_events(
    times, temp_c,
    *,
    drop_threshold_c: float = DROP_C_24H,
    cold_band_c: float = COLD_BAND_C,
) -> list[UpwellingEvent]:
    """Detect upwelling onsets in a station's 6 m temperature series.

    times: sequence of UTC datetimes (ascending). temp_c: matching temperatures.
    Returns one event per onset (a sample that first satisfies a trigger while the
    previous sample did not — de-duplicates a multi-hour cold spell into one event).
    """
    t = np.asarray([_as_utc_naive(x) for x in times], dtype="datetime64[s]")
    y = np.asarray(temp_c, dtype=float)
    if t.size != y.size:
        raise ValueError("times and temp_c length mismatch")
    order = np.argsort(t)
    t, y = t[order], y[order]

    hours = (t - t[0]) / np.timedelta64(1, "h")
    events: list[UpwellingEvent] = []
    active = False
    for i in range(y.size):
        # drop over the trailing 24 h window
        window = (hours >= hours[i] - 24.0) & (hours <= hours[i])
        drop = float(np.max(y[window]) - y[i]) if window.any() else 0.0
        cold = y[i] <= cold_band_c
        big_drop = drop >= drop_threshold_c
        triggered = cold or big_drop
        if triggered and not active:
            trig = "both" if (cold and big_drop) else ("cold_band" if cold else "drop")
            events.append(
                UpwellingEvent(
                    onset=_to_dt(t[i]),
                    min_temp_c=float(y[i]),
                    drop_c_24h=round(drop, 3),
                    trigger=trig,
                )
            )
        active = triggered
    return events


def _as_utc_naive(x):
    """np.datetime64 has no tz; normalize tz-aware datetimes to naive UTC first."""
    if isinstance(x, datetime) and x.tzinfo is not None:
        return x.astimezone(timezone.utc).replace(tzinfo=None)
    return x


def _to_dt(x) -> datetime:
    """np.datetime64[s] -> tz-aware UTC datetime (times enter/leave as UTC)."""
    dt = np.datetime64(x, "s").astype("datetime64[s]").astype(datetime)
    return dt.replace(tzinfo=timezone.utc)
