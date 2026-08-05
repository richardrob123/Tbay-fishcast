"""G2 event verification — pure functions (no I/O), unit-tested like verification/g1.py.

Implements the pre-registered primary endpoint (docs/G2_PREREGISTRATION.md):
  * persistence-guarded upwelling-onset detection on a temperature series
    (a >= drop_c cooling within window_h that is SUSTAINED >= persist_h — a single
    noisy sample cannot fire an event, fixing the detect_events one-sample flaw);
  * episode clustering (merge onsets within merge_gap_days into one event);
  * EVENT-based one-to-one matching with a temporal tolerance tau_days, producing
    hits / misses / false_alarms for verification.scorecard.contingency.

Used for BOTH sides: the LSOFS 6 m detector series and the GLSEA differential truth
series feed the same functions (different window_h / thresholds).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

import numpy as np


@dataclass(frozen=True)
class Episode:
    onset: date          # first day of the event
    start: date
    end: date
    n_days: int


@dataclass(frozen=True)
class MatchResult:
    hits: int
    misses: int          # truth episodes with no detected match
    false_alarms: int    # detected episodes with no truth match
    matched: list        # list[(truth_onset, detected_onset)]


def _as_naive_utc(x):
    if isinstance(x, datetime) and x.tzinfo is not None:
        return x.astimezone(timezone.utc).replace(tzinfo=None)
    return x


def detect_onsets(times, temp, *, drop_c: float, persist_h: float,
                  window_h: float = 24.0) -> list[datetime]:
    """Persistence-guarded cooling onsets.

    An hour is 'active' when temp is >= drop_c below the max of the trailing
    window_h. A qualifying event is a run of active hours lasting >= persist_h; its
    onset is the first hour of the run. Returns onset datetimes (UTC, tz-aware).
    """
    t = np.asarray([np.datetime64(_as_naive_utc(x), "s") for x in times])
    y = np.asarray(temp, dtype=float)
    if t.size != y.size:
        raise ValueError("times and temp length mismatch")
    if t.size == 0:
        return []
    order = np.argsort(t)
    t, y = t[order], y[order]
    hours = (t - t[0]) / np.timedelta64(1, "h")

    active = np.zeros(y.size, dtype=bool)
    for i in range(y.size):
        w = (hours >= hours[i] - window_h) & (hours <= hours[i])
        active[i] = (float(np.max(y[w])) - y[i]) >= drop_c

    onsets: list[datetime] = []
    i = 0
    n = y.size
    while i < n:
        if not active[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and active[j + 1]:
            j += 1
        run_h = float(hours[j] - hours[i])
        # a single-sample run has 0 duration; require >= persist_h (0 => no guard)
        if run_h >= persist_h - 1e-9:
            onsets.append(_np_to_dt(t[i]))
        i = j + 1
    return onsets


def cluster_episodes(onsets, merge_gap_days: int = 1) -> list[Episode]:
    """Merge onset datetimes into episodes; onsets within merge_gap_days join one
    event. Returns episodes sorted by onset day."""
    days = sorted({(_as_naive_utc(o).date() if isinstance(o, datetime) else o)
                   for o in onsets})
    if not days:
        return []
    episodes: list[Episode] = []
    start = prev = days[0]
    for d in days[1:]:
        if (d - prev).days <= merge_gap_days:
            prev = d
        else:
            episodes.append(Episode(start, start, prev, (prev - start).days + 1))
            start = prev = d
    episodes.append(Episode(start, start, prev, (prev - start).days + 1))
    return episodes


def match_episodes(truth: list[Episode], detected: list[Episode],
                   tau_days: int = 1) -> MatchResult:
    """One-to-one greedy match of detected to truth episodes within +-tau_days of
    onset. Each truth/detected episode matches at most once (no double counting a
    multi-day event). Hits/misses/false_alarms feed scorecard.contingency."""
    truth_sorted = sorted(truth, key=lambda e: e.onset)
    det_sorted = sorted(detected, key=lambda e: e.onset)
    used_det = set()
    matched = []
    hits = 0
    for te in truth_sorted:
        best = None
        best_gap = None
        for k, de in enumerate(det_sorted):
            if k in used_det:
                continue
            gap = abs((de.onset - te.onset).days)
            if gap <= tau_days and (best_gap is None or gap < best_gap):
                best, best_gap = k, gap
        if best is not None:
            used_det.add(best)
            matched.append((te.onset, det_sorted[best].onset))
            hits += 1
    misses = len(truth_sorted) - hits
    false_alarms = len(det_sorted) - len(used_det)
    return MatchResult(hits=hits, misses=misses, false_alarms=false_alarms, matched=matched)


def _np_to_dt(x) -> datetime:
    dt = np.datetime64(x, "s").astype("datetime64[s]").astype(datetime)
    return dt.replace(tzinfo=timezone.utc)
