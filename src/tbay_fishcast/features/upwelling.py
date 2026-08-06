"""Ensemble-wind upwelling-probability (PLAN task: honest probability, not a single line).

LSOFS temperature-forecast skill decays with lead time; upwelling is wind-driven, so
rather than trust one deterministic wind trace we run the same favorable-wind test
against every member of an ensemble forecast (ingest/wind_forecast.fetch_ensemble_wind)
and report the FRACTION of members agreeing, per forecast day. That fraction is the
honest probability — calibrated, not certain (CLAUDE domain quick-reference: "fish
ceiling ... the product is calibrated probabilities with honest intervals").

Reuses `in_sector` from features/wind.py (west-quadrant test, FAVORABLE_SECTOR =
225-315 deg). Threshold 13 kn is the mid of the Wedderburn upwelling range (~12-17 kt
sustained, mixed-layer-depth dependent) — kept as a parameter, not hardcoded lore.

PURE functions only: no network, no I/O. Operate on already-parsed time + per-member
speed/direction arrays (as returned, grouped, by ingest/wind_forecast.py) so they are
unit-testable without a live call (CLAUDE rule 2: TDD, deterministic core).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np

from .wind import FAVORABLE_SECTOR, in_sector


def member_favorable(times, speed_kn, dir_deg, *, threshold_kn: float = 13.0,
                      sector=FAVORABLE_SECTOR, min_run_h: float = 6.0) -> set[date]:
    """Local days (for ONE ensemble member) with a sustained upwelling-favorable blow.

    "Sustained" = a contiguous run of hours, each simultaneously in the west quadrant
    (`sector`) and at/above `threshold_kn`, totalling >= `min_run_h`. Every calendar
    day touched by such a run is included (a run crossing midnight marks both days).
    A run shorter than `min_run_h` — however strong — does not count, and wind outside
    `sector` never counts regardless of speed.

    Run duration is measured using the actual spacing between input samples (the
    median consecutive gap), so this works for hourly ensemble data as delivered by
    Open-Meteo without assuming a fixed step.
    """
    t = [_naive(x) for x in times]
    v = np.asarray(speed_kn, dtype=float)
    d = np.asarray(dir_deg, dtype=float)
    n = len(t)
    if not (n == v.size == d.size):
        raise ValueError("times/speed/dir length mismatch")
    if n == 0:
        return set()

    order = np.argsort(t)
    t = [t[i] for i in order]
    v = v[order]
    d = d[order]

    fav = in_sector(d, sector) & (v >= threshold_kn)

    if n >= 2:
        diffs_h = np.array(
            [(t[i + 1] - t[i]).total_seconds() / 3600.0 for i in range(n - 1)]
        )
        diffs_h = diffs_h[diffs_h > 0]
        spacing_h = float(np.median(diffs_h)) if diffs_h.size else 1.0
    else:
        spacing_h = 1.0

    days: set[date] = set()
    i = 0
    while i < n:
        if fav[i]:
            j = i
            # a data gap (> 2x the nominal spacing) breaks the run: favorable hours on
            # either side of missing data must not be counted as one sustained blow
            while (j + 1 < n and fav[j + 1]
                   and (t[j + 1] - t[j]).total_seconds() / 3600.0 <= 2.0 * spacing_h):
                j += 1
            run_len_h = (j - i + 1) * spacing_h
            if run_len_h >= min_run_h:
                for k in range(i, j + 1):
                    days.add(t[k].date())
            i = j + 1
        else:
            i += 1
    return days


def upwelling_probability(time, members, **kw) -> dict[date, float]:
    """Fraction of ensemble members with a sustained favorable-wind day, per day.

    `time` is the shared hourly time axis; `members` is a list of
    `{"speed_kn": [...], "dir_deg": [...]}` dicts (one per ensemble member), as
    produced by `ingest.wind_forecast.fetch_ensemble_wind`. Extra keyword args
    (`threshold_kn`, `sector`, `min_run_h`) pass through to `member_favorable`.

    Returns `{date: probability}` for every calendar day present in `time`,
    including days with probability 0.0 (no member favorable) — days aren't
    silently dropped just because nothing happened.
    """
    all_days: set[date] = {_naive(x).date() for x in time}
    if not members:
        return {d: 0.0 for d in sorted(all_days)}

    n_members = len(members)
    counts: dict[date, int] = {d: 0 for d in all_days}
    for m in members:
        fav_days = member_favorable(time, m["speed_kn"], m["dir_deg"], **kw)
        for d in fav_days:
            all_days.add(d)
            counts[d] = counts.get(d, 0) + 1

    return {d: counts.get(d, 0) / n_members for d in sorted(all_days)}


def _naive(x) -> datetime:
    """Normalize a time value (datetime or ISO string, tz-aware or naive) to a
    naive datetime for calendar-day bucketing and hour-gap arithmetic."""
    if isinstance(x, str):
        x = datetime.fromisoformat(x)
    if isinstance(x, datetime) and x.tzinfo is not None:
        return x.astimezone(timezone.utc).replace(tzinfo=None)
    return x
