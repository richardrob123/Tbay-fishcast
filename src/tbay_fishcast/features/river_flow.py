"""River discharge → plume-strength trend for the run-species river mouths (audit T3a).

The river-mouth markers are THE signal for the weak-temperature-cue species (salmon/steelhead); a
static dot can't say whether the plume is pushing. This classifies the ECCC realtime discharge into:
  • the current flow (m³/s), and
  • a short-window TREND — rising / steady / falling over ~3 days.
A RISING river is what the literature calls the staging/run trigger, and the calendar encodes that
claim as `rain_trigger`. ADR-061 went and MEASURED it here, and could not find it: across 20 dated
pink report days, discharge on the report day sat at 0.84x (Current) and 0.93x (Neebing) of the
trailing week's median — at or slightly BELOW normal water, not on a freshet. That null is
confounded (the ledger is mostly iNaturalist sightings, and nobody photographs fish in the rain),
so it does not prove the opposite. It is enough to stop ASSERTING the trigger: `freshet` below
describes the HYDROGRAPH — this river is rising — and no longer tells anyone what the fish will do.
Re-check `data/calib/run_trigger_skill.json` before that wording changes back.

What this deliberately does NOT do: call the flow "high" or "low" — that needs a multi-year
per-gauge climatology this system doesn't hold yet, so a bare absolute value is reported with its
trend, not a fabricated percentile (rule 3, provenance).

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
    freshet: bool                # rising beyond the steady band. A HYDROGRAPH state, not a fish
                                 # prediction — the run-trigger claim is unmeasured (ADR-061).
    note: str

    def as_dict(self) -> dict:
        return asdict(self)


def classify(samples) -> RiverFlow:
    """Classify a discharge series (list of (utc_datetime, q_cms), any order).

    Uses the newest sample as the current flow and the sample closest to _TREND_WINDOW_DAYS earlier
    as the baseline. Returns state 'unknown' (and the UI shows no flow line) when the series is too
    short or too brief to establish a trend — never a guess."""
    # Keep only physically valid samples: finite and non-negative. ECCC realtime gauges emit
    # occasional negative/NaN artifacts (ice, backwater, sensor glitch); a NaN would otherwise
    # sail through every comparison into a confident "steady" with 'nan m³/s' in the UI note,
    # and a negative flow is impossible — both violate the "never a guess" contract
    # (adversarial stress-test 2026-08, MED-1/MED-2).
    import math as _math
    pts = sorted([(t, float(q)) for t, q in samples
                  if q is not None and _math.isfinite(float(q)) and float(q) >= 0.0],
                 key=lambda tp: tp[0])
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
        note = (f"{_fmt(cur_q)} m³/s and rising ({pct:+.0f}% / {span_days:.0f}d) — plume building. "
                "Rise as a run trigger is unmeasured here (ADR-061).")
    elif frac <= -_STEADY_FRAC:
        state, freshet = "falling", False
        note = f"{_fmt(cur_q)} m³/s and dropping ({pct:+.0f}% / {span_days:.0f}d) — plume easing."
    else:
        state, freshet = "steady", False
        note = f"{_fmt(cur_q)} m³/s, steady ({pct:+.0f}% / {span_days:.0f}d)."
    return RiverFlow(round(cur_q, 3), pct, state, freshet, note)


def _fmt(q: float) -> str:
    return f"{q:.2f}" if q < 10 else f"{q:.0f}"


# ---- Seasonal context from the ECCC daily-mean ARCHIVE climatology ---------------------------
# data/calib/flow_climatology.json (scripts/build_flow_climatology.py): per gauge, day-of-year
# p10/p25/p50/p75/p90 of daily discharge pooled ±10 d across all archive years (Kam: 1926-2025).
# This upgrades the honest-but-mute "20 m³/s" into "20 m³/s ≈ 35th percentile for the date" — a
# MEASURED claim, replacing the documented "no high/low without a climatology" limitation.

from functools import lru_cache
from pathlib import Path as _Path

_FLOW_CLIM = _Path(__file__).resolve().parents[3] / "data" / "calib" / "flow_climatology.json"


@lru_cache(maxsize=1)
def _climatology(path_str: str = str(_FLOW_CLIM)):
    import json
    try:
        d = json.loads(_Path(path_str).read_text())
        return d.get("gauges", {}) if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def seasonal_context(gauge_mid: str, q_cms, when) -> dict | None:
    """Percentile of a discharge vs the gauge's archive climatology for that day-of-year.

    Returns {"pct": int, "pct_label": str, "clim_years": str} or None when unavailable (no
    climatology / no flow / thin bin) — the popup then simply omits the context line, never
    guesses. Percentile is piecewise-linear between the archived p10..p90 quantiles; outside
    that span it reports the honest open-ended "<10th" / ">90th"."""
    if q_cms is None:
        return None
    g = _climatology().get(gauge_mid)
    if not g:
        return None
    doy = min(when.timetuple().tm_yday, 365)
    bin_ = g.get("doy", {}).get(str(doy))
    if not bin_:
        return None
    knots = [(bin_["p10"], 10.0), (bin_["p25"], 25.0), (bin_["p50"], 50.0),
             (bin_["p75"], 75.0), (bin_["p90"], 90.0)]
    q = float(q_cms)
    if q <= knots[0][0]:
        pct, label = 9, "<10th %ile"
    elif q >= knots[-1][0]:
        pct, label = 91, ">90th %ile"
    else:
        pct = 50.0
        for (q0, p0), (q1, p1) in zip(knots, knots[1:]):
            if q0 <= q <= q1:
                pct = p0 if q1 == q0 else p0 + (p1 - p0) * (q - q0) / (q1 - q0)
                break
        pct = int(round(pct))
        sfx = "th" if 10 <= pct % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(pct % 10, "th")
        label = f"≈{pct}{sfx} %ile"
    return {"pct": int(round(pct)), "pct_label": label,
            "clim_years": g.get("years", ""), "clim_n": bin_.get("n")}
