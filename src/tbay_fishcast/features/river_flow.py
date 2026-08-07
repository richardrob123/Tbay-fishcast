"""River discharge → plume-strength trend for the run-species river mouths (audit T3a).

The river-mouth markers are THE signal for the weak-temperature-cue species (salmon/steelhead); a
static dot can't say whether the plume is pushing. This classifies the ECCC realtime discharge into:
  • the current flow (m³/s), and
  • a short-window TREND — rising / steady / falling over ~3 days.
A RISING river (a freshet, especially the first cool rain of the run) is the classic staging/run
trigger the calendar encodes as `rain_trigger`; a falling/steady low river is slack. That is the
honest, decidable signal from flow ALONE. What this deliberately does NOT do: call the flow "high"
or "low" — that needs a multi-year per-gauge climatology this system doesn't hold yet, so a bare
absolute value is reported with its trend, not a fabricated percentile (rule 3, provenance).

Pure classification over an already-fetched (datetime, Q) series; no I/O, no LLM (ADR-001)."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import timedelta

# A day-over-day change beyond this fraction is a real trend; within it the river is effectively
# steady. Bounded judgement (rivers this size wander a few % on diel/regulation noise), labelled.
_STEADY_FRAC = 0.12
_TREND_WINDOW_DAYS = 3.0


@dataclass(frozen=True)
class RiverFlow:
    q_cms: float | None          # latest discharge, m³/s
    trend_pct: float | None      # signed % change over the trailing ~3 days (+rising)
    state: str                   # rising | falling | steady | unknown
    freshet: bool                # rising beyond the steady band — the staging/run trigger
    note: str

    def as_dict(self) -> dict:
        return asdict(self)


def classify(samples) -> RiverFlow:
    """Classify a discharge series (list of (utc_datetime, q_cms), any order).

    Uses the newest sample as the current flow and the sample closest to _TREND_WINDOW_DAYS earlier
    as the baseline. Returns state 'unknown' (and the UI shows no flow line) when the series is too
    short or too brief to establish a trend — never a guess."""
    pts = sorted([(t, float(q)) for t, q in samples], key=lambda tp: tp[0])
    if not pts:
        return RiverFlow(None, None, "unknown", False, "river flow unavailable")
    cur_t, cur_q = pts[-1]
    target = cur_t - timedelta(days=_TREND_WINDOW_DAYS)
    base_t, base_q = min(pts, key=lambda tp: abs((tp[0] - target).total_seconds()))
    span_days = (cur_t - base_t).total_seconds() / 86400.0
    if span_days < 0.75 or base_q <= 0:      # not enough history / zero baseline
        return RiverFlow(round(cur_q, 3), None, "unknown", False,
                         f"{_fmt(cur_q)} m³/s; trend unavailable (short record)")
    frac = (cur_q - base_q) / base_q
    pct = round(100.0 * frac, 0)
    if frac >= _STEADY_FRAC:
        state, freshet = "rising", True
        note = f"{_fmt(cur_q)} m³/s and rising ({pct:+.0f}% / {span_days:.0f}d) — freshet pulls staging fish in."
    elif frac <= -_STEADY_FRAC:
        state, freshet = "falling", False
        note = f"{_fmt(cur_q)} m³/s and dropping ({pct:+.0f}% / {span_days:.0f}d) — plume easing."
    else:
        state, freshet = "steady", False
        note = f"{_fmt(cur_q)} m³/s, steady ({pct:+.0f}% / {span_days:.0f}d)."
    return RiverFlow(round(cur_q, 3), pct, state, freshet, note)


def _fmt(q: float) -> str:
    return f"{q:.2f}" if q < 10 else f"{q:.0f}"
