"""Upwelling-PHASE indicator — *when* in the cycle are we, not just how cold.

The fish-behavior review (docs/FISH_BEHAVIOR_REVIEW.md) established the single most
important correction to the model: the coldest reachable water is NOT the best fishing.
A fresh, sharp upwelling SUPPRESSES the shore bite (cold shock → sluggish fish); the
productive window is the RELAXATION phase — water restratifying, the thermal front
sharpening and sweeping, baitfish stacking — typically ~0.5–3 days after a sustained
west-quadrant blow relaxes. This module reads a wind time series and says which phase a
given moment sits in, so the map can reward relaxation instead of the trough.

Phases (by the physics in CLAUDE.md: setup ≈10 h, seiche/restratification ≈40 h,
event lifetime ≈120 h):
  * SETUP       — a favorable blow is active but young (< setup_h): upwelling building.
  * PEAK        — favorable blow sustained/just ended: coldest water at shore, cold shock,
                  bite typically OFF short-term.
  * RELAXATION  — blow ended peak_h..relax_end_h ago: restratifying, front works — PRIME.
  * NEUTRAL     — no recent sustained favorable blow (or it has fully decayed).

A "sustained favorable blow" is a run of samples in the west quadrant (FAVORABLE_SECTOR)
at/above `threshold_kn` lasting >= `min_run_h`, tolerating brief sub-threshold lulls
(<= `grace_h`) — the same favorable test the ensemble upwelling-probability uses, made
robust to hour-to-hour anemometer flicker. Pure functions over an already-parsed wind
series (observed METAR for day 0, ensemble forecast for the outlook). No network, no LLM
(ADR-001, CLAUDE rule 2).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

from .wind import FAVORABLE_SECTOR, in_sector

SETUP = "setup"
PEAK = "peak"
RELAXATION = "relaxation"
NEUTRAL = "neutral"
UNKNOWN = "unknown"

# Upwelling-favorable wind threshold — the LOW end of the documented Wedderburn range (~12-17 kt
# sustained, mixed-layer-depth dependent; CLAUDE.md domain physics). Used for BOTH the observed
# (airport) and the forecast (over-lake ensemble) segments.
#
# Previously the observed segment used a lower 10 kn bar on the rationale that "CYQT reads ~3 kn low
# vs the over-lake wind." Measured against the NDBC over-lake buoys (45027/45023) that rationale is
# REFUTED: the airport-vs-buoy offset is ~±1 kn, and in the upwelling-favorable west quadrant CYQT
# reads ~1 kn HIGHER, not lower (data/wind_gate_log.csv accumulates this each build). With no data
# basis for a separate, sub-Wedderburn airport bar, both segments use the physics value. If the
# accumulating gate later shows a stable, significant airport offset, split it THEN with data behind
# it — not on a guess.
OBSERVED_THRESHOLD_KN = 13.0
# A genuine sustained blow dips below threshold for the odd hour (airport gust structure);
# a lull no longer than GRACE_H does not end it. Longer than that = two separate blows.
GRACE_H = 2.0


@dataclass(frozen=True)
class Run:
    start: datetime
    end: datetime
    duration_h: float
    peak_kn: float
    mean_dir_deg: float
    n: int


@dataclass(frozen=True)
class Phase:
    phase: str                       # setup | peak | relaxation | neutral | unknown
    prime: bool                      # relaxation = the prime bite window
    suppressed: bool                 # setup/peak = bite likely off (cold shock)
    since_end_h: float | None        # hours from the driving run's end to `now` (None if active/none)
    active: bool                     # a favorable blow is blowing at `now`
    run: Run | None                  # the driving run (most recent sustained blow), if any
    source: str = "observed"         # observed:<station> | forecast:<model>
    confidence: str = "low"          # high | med | low (data coverage / forecast lead)
    detail: str = ""                 # human receipts, e.g. "W 14 kn for 11 h, ended ~28 h ago"

    def as_dict(self) -> dict:
        d = {"phase": self.phase, "prime": self.prime, "suppressed": self.suppressed,
             "active": self.active, "source": self.source, "confidence": self.confidence,
             "detail": self.detail}
        if self.since_end_h is not None:
            d["since_end_h"] = round(self.since_end_h, 1)
        if self.run is not None:
            d["run"] = {"start_utc": self.run.start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "end_utc": self.run.end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "duration_h": round(self.run.duration_h, 1),
                        "peak_kn": round(self.run.peak_kn, 1),
                        "dir_deg": round(self.run.mean_dir_deg)}
        return d


def _utc(x) -> datetime:
    if isinstance(x, str):
        x = datetime.fromisoformat(x.replace("Z", "+00:00"))
    if x.tzinfo is None:
        return x.replace(tzinfo=timezone.utc)
    return x.astimezone(timezone.utc)


def _circular_mean_deg(dirs: np.ndarray) -> float:
    r = np.deg2rad(dirs.astype(float))
    m = math.atan2(float(np.sin(r).mean()), float(np.cos(r).mean()))
    return (math.degrees(m) + 360.0) % 360.0


def extract_runs(times, dir_deg, speed_kn, *, threshold_kn: float,
                 min_run_h: float = 6.0, grace_h: float = GRACE_H,
                 sector=FAVORABLE_SECTOR) -> list[Run]:
    """All sustained favorable wind runs in the series, chronological.

    A run is a stretch bracketed by favorable samples (west quadrant AND >= threshold)
    that tolerates sub-threshold lulls up to `grace_h` — real blows flicker below the line
    for an hour without stopping. A true data gap (> 2x median spacing) or a lull longer
    than `grace_h` ends the run at its last favorable sample. `dir_deg` None (variable/calm)
    never satisfies the sector test. Runs shorter than `min_run_h` are discarded.
    """
    t = [_utc(x) for x in times]
    n = len(t)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: t[i])
    t = [t[i] for i in order]
    v = np.array([float(speed_kn[i]) for i in order], dtype=float)
    d = np.array([np.nan if dir_deg[i] is None else float(dir_deg[i]) for i in order], dtype=float)

    has_dir = ~np.isnan(d)
    fav = np.zeros(n, dtype=bool)
    fav[has_dir] = in_sector(d[has_dir], sector) & (v[has_dir] >= threshold_kn)

    if n >= 2:
        gaps = np.array([(t[i + 1] - t[i]).total_seconds() / 3600.0 for i in range(n - 1)])
        gaps = gaps[gaps > 0]
        spacing_h = float(np.median(gaps)) if gaps.size else 1.0
    else:
        spacing_h = 1.0

    runs: list[Run] = []
    i = 0
    while i < n:
        if not fav[i]:
            i += 1
            continue
        last_fav = i  # extend across brief lulls; the run officially ends at the last fav sample
        k = i
        while k + 1 < n:
            step_h = (t[k + 1] - t[k]).total_seconds() / 3600.0
            if step_h > 2.0 * spacing_h:            # real data gap — stop
                break
            if fav[k + 1]:
                last_fav = k + 1
                k += 1
                continue
            # non-favorable sample: continue only if still within grace of the last fav
            if (t[k + 1] - t[last_fav]).total_seconds() / 3600.0 <= grace_h:
                k += 1
                continue
            break
        dur_h = ((t[last_fav] - t[i]).total_seconds() / 3600.0) or spacing_h
        if dur_h >= min_run_h:
            span_d = d[i:last_fav + 1]
            fav_v = v[i:last_fav + 1][fav[i:last_fav + 1]]
            runs.append(Run(start=t[i], end=t[last_fav], duration_h=dur_h,
                            peak_kn=float(fav_v.max()) if fav_v.size else float(v[i:last_fav + 1].max()),
                            mean_dir_deg=_circular_mean_deg(span_d[~np.isnan(span_d)]),
                            n=int(fav[i:last_fav + 1].sum())))
        i = last_fav + 1
    return runs


def classify_at(runs: list[Run], now, *, setup_h: float = 10.0, peak_h: float = 12.0,
                relax_end_h: float = 48.0, source: str = "observed",
                confidence: str = "low") -> Phase:
    """Phase at reference time `now`, given pre-extracted runs.

    The governing run is the most recent run that has started at/before `now`. If `now`
    falls inside it, the blow is still active. Otherwise the phase is set by how long ago
    the blow ended: fresh cold (< peak_h) reads PEAK, the restratification window
    (peak_h..relax_end_h) reads RELAXATION (prime), beyond that NEUTRAL.
    """
    now = _utc(now)
    governing = None
    for r in runs:
        if r.start <= now:
            governing = r  # runs are chronological; keep the latest that has begun
    if governing is None:
        return Phase(NEUTRAL, prime=False, suppressed=False, since_end_h=None,
                     active=False, run=None, source=source, confidence=confidence,
                     detail="no sustained west-quadrant blow in the observed window")

    if now <= governing.end:  # blow still active
        active_h = (now - governing.start).total_seconds() / 3600.0
        ph = SETUP if active_h < setup_h else PEAK
        return Phase(ph, prime=False, suppressed=True, since_end_h=None, active=True,
                     run=governing, source=source, confidence=confidence,
                     detail=_detail(governing, None, active_h))

    since = (now - governing.end).total_seconds() / 3600.0
    if since < peak_h:
        ph, prime, supp = PEAK, False, True
    elif since < relax_end_h:
        ph, prime, supp = RELAXATION, True, False
    else:
        return Phase(NEUTRAL, prime=False, suppressed=False, since_end_h=since,
                     active=False, run=governing, source=source, confidence=confidence,
                     detail="last sustained blow has fully relaxed (> %.0f h ago)" % relax_end_h)
    return Phase(ph, prime=prime, suppressed=supp, since_end_h=since, active=False,
                 run=governing, source=source, confidence=confidence,
                 detail=_detail(governing, since, None))


def classify(times, dir_deg, speed_kn, now, *, threshold_kn: float = OBSERVED_THRESHOLD_KN,
             min_run_h: float = 6.0, sector=FAVORABLE_SECTOR, setup_h: float = 10.0,
             peak_h: float = 12.0, relax_end_h: float = 48.0, source: str = "observed",
             confidence: str | None = None) -> Phase:
    """Extract runs from a wind series and classify the phase at `now`.

    `confidence` defaults to a coverage heuristic: 'high' if samples span the full lookback
    around a governing event, else 'med' — callers may override (forecast leads pass 'low').
    """
    runs = extract_runs(times, dir_deg, speed_kn, threshold_kn=threshold_kn,
                        min_run_h=min_run_h, sector=sector)
    if confidence is None:
        confidence = "high" if len(times) >= 24 else "med"
    return classify_at(runs, now, setup_h=setup_h, peak_h=peak_h, relax_end_h=relax_end_h,
                       source=source, confidence=confidence)


def _card(deg: float) -> str:
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[int((deg % 360) / 45.0 + 0.5) % 8]


def _detail(run: Run, since_h: float | None, active_h: float | None) -> str:
    who = f"{_card(run.mean_dir_deg)} {run.peak_kn:.0f} kn"
    if active_h is not None:
        return f"{who} favorable wind blowing {active_h:.0f} h and counting"
    dur = f"{run.duration_h:.0f} h"
    if since_h is None:
        return f"{who} for {dur}"
    if since_h < 1:
        return f"{who} for {dur}, just relaxed"
    return f"{who} for {dur}, ended ~{since_h:.0f} h ago"
