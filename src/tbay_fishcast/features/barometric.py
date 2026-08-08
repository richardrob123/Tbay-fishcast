"""Barometric pressure level + trend → an honestly-labelled directional feeding PRIOR.

Why this is a PRIOR, not a scored layer: the pre-frontal-feed / post-frontal-'bluebird'-lockjaw
pattern is real and widely reported by anglers and in secondary fisheries writing (tier T3), but it
is NOT locally calibrated here — no catch log exists to fit an effect size (rule 7). So this module
classifies the pressure tendency into a DIRECTION (a falling barometer ahead of a front tends to
switch fish on; a sharp post-frontal rise under bright bluebird skies tends to shut them down) and
the product presents it as timing context beside the upwelling-phase banner — never multiplied into
the spatial score (no fitted weight, ADR-001/rule 7). The trend is the signal; the absolute level is
secondary context.

Pure classification over an already-fetched pressure series (I/O lives in ingest/surface_meteo).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone

# Tendency thresholds over a 3 h window (WMO-style pressure-tendency bands, hPa/3h). A change of
# ~1 hPa/3h is a meaningful synoptic tendency; below that is effectively steady.
_STEADY_HPA_3H = 1.0
_TREND_WINDOW_H = 3.0


@dataclass(frozen=True)
class Barometric:
    level_hpa: float | None      # pressure at (nearest to) the issue instant
    trend_hpa_3h: float | None   # signed change over the trailing ~3 h (+ rising, − falling)
    state: str                   # falling | rising | steady | unknown
    prior: str                   # improving | slowing | neutral | unknown  (bite direction)
    note: str
    tier: str = "T3"

    def as_dict(self) -> dict:
        return asdict(self)


def _parse(ts: str) -> datetime:
    t = datetime.fromisoformat(ts)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def classify(times, pressure_hpa, at_utc: datetime) -> Barometric:
    """Classify the pressure tendency at `at_utc` from a UTC-stamped hourly series.

    Falling barometer  → 'improving' (pre-frontal feed).
    Sharp rise         → 'slowing'  (post-frontal bluebird).
    Steady             → 'neutral'.
    Insufficient data  → 'unknown' (the UI then shows no barometric line, not a guess)."""
    import math as _math
    pts = [(_parse(t), float(p)) for t, p in zip(times, pressure_hpa)
           if p is not None and _math.isfinite(float(p))]   # NaN would leak into level/trend/note
    if len(pts) < 2:
        return Barometric(None, None, "unknown", "unknown",
                          "barometric prior unavailable (no pressure series)")
    if at_utc.tzinfo is None:
        at_utc = at_utc.replace(tzinfo=timezone.utc)
    # nearest sample to the issue instant = current level
    cur_t, cur_p = min(pts, key=lambda tp: abs((tp[0] - at_utc).total_seconds()))
    # sample closest to _TREND_WINDOW_H before the current sample
    target = cur_t.timestamp() - _TREND_WINDOW_H * 3600
    prev_t, prev_p = min(pts, key=lambda tp: abs(tp[0].timestamp() - target))
    dt_h = (cur_t - prev_t).total_seconds() / 3600.0
    # Require at least half the tendency window of real baseline: extrapolating a couple of
    # minutes of sensor noise ×(3h/dt) manufactures a huge fake trend (stress-test 2026-08 —
    # the sibling river_flow module already guards this identical failure).
    if prev_t >= cur_t or dt_h < _TREND_WINDOW_H / 2.0:
        return Barometric(round(cur_p, 1), None, "unknown", "unknown",
                          f"{cur_p:.0f} hPa; trend unavailable (series too short)")
    trend = (cur_p - prev_p) * (_TREND_WINDOW_H / dt_h)   # normalize to per-3h
    if trend <= -_STEADY_HPA_3H:
        state, prior = "falling", "improving"
        note = f"{cur_p:.0f} hPa and falling ({trend:+.1f}/3h) — a dropping barometer ahead of a change often switches fish on (prior)."
    elif trend >= _STEADY_HPA_3H:
        state, prior = "rising", "slowing"
        note = f"{cur_p:.0f} hPa and rising ({trend:+.1f}/3h) — a sharp post-frontal rise / bluebird sky often slows the bite (prior)."
    else:
        state, prior = "steady", "neutral"
        note = f"{cur_p:.0f} hPa, steady ({trend:+.1f}/3h) — no barometric push either way."
    return Barometric(round(cur_p, 1), round(trend, 1), state, prior, note)
