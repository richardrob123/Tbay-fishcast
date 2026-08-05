# G2 scorecard — upwelling-event verification

**Verdict: G2 is INCONCLUSIVE against available remote truth — not a pass, not a fail.**
LSOFS produces physically-credible, wind-driven 6 m upwelling events at the right cadence,
but no reachable independent source can witness them to score detection skill. The in-situ
6 m logger (ADR-019) is the decisive next step; until then the event layer is **retained,
not demoted, but unvalidated.**

Executed per the frozen `docs/G2_PREREGISTRATION.md` (tune 2024 → validate 2025 & 2026 held
out). Reproduce: `python scripts/g2_scorecard.py`.

## What LSOFS detects (held-out years)

Persistence-guarded onsets on the 6 m series, locked thresholds (DROP_C=4 °C, WINDOW_H=48 h,
PERSIST_H=12 h):

| year (window) | Silver | MacKenzie | Marina | total |
|---|---|---|---|---|
| 2025 (Jun 15–Sep 30) | 2 | 3 | 3 | 8 |
| 2026 (Jun 15–Aug 3, partial) | 1 | 1 | 1 | 3 |

~2–3 events/station/full-season — **within the G4 target (2–5/summer)** — concentrated at the
exposed stations, sparser at sheltered Marina. Cadence is sane; alarm fatigue is not a risk.

## The events are physically real (in the model), not numerical noise

Season correlation between trailing-48 h favorable (west-quadrant) wind-run and 6 m
temperature, held-out years:

| year | Silver | MacKenzie | Marina | mean |
|---|---|---|---|---|
| 2025 | −0.47 | −0.46 | −0.46 | **−0.46** |
| 2026 | −0.38 | −0.36 | −0.41 | −0.39 |

Correct sign and solid magnitude at every station: **more upwelling-favorable wind → colder
6 m water**, with the response strongest at short lag and decaying by ~48 h (verified
separately: r goes −0.47 → −0.25 → ~0 across lag 0 → 24 → 48 h). This is textbook wind-driven
upwelling; the detector is not firing on model noise.

**Caveat (honest):** LSOFS is *wind-forced*, so this confirms the model behaves like real
upwelling physics — it does **not** prove the events match the real lake in timing or
magnitude. Only in-situ 6 m data can close that gap.

## Why POD cannot be scored: GLSEA is blind to 6 m upwelling

The pre-registered truth (GLSEA differential cold event, ≥3 °C/48 h) fired **zero events at
every station in both years**. Quantified blindness on the full 2025 season:

- GLSEA **absolute** SST at the station pixels drops **≤1.6 °C over 48 h** all summer (0 days ≥3 °C).
- GLSEA **differential** (station − offshore basin) max 48 h drop **2.2–2.4 °C** (0 days ≥3 °C).
- Meanwhile the LSOFS **6 m** signal at the same spots swings **5–6 °C over 48 h**.

So a 6 m cold anomaly does not surface as a skin-SST cooling the daily 1 km satellite composite
can resolve — exactly the depth caveat (ADR-019) and the adversarial critique's fatal flaw #1.
POD = hits/(hits+misses) is **undefined** (no truth events). FAR would be 1.00 by construction,
which is why FAR was pre-registered as characterization, not a gate: it measures the satellite's
blindness, not the model's error.

## Truth sources considered and why each fails here

| candidate truth | verdict |
|---|---|
| GLSEA differential SST (primary) | Blind — max 48 h drop 2.2–2.4 °C ≪ event scale. |
| GLSEA absolute SST | Blind — ≤1.6 °C/48 h all season. |
| NDBC Slate buoy 45136 | ~165 km E, open-lake surface (8.8 °C vs 17 °C embayment) — not co-located, surface only. |
| ERA5 wind | The *driver*, not a witness; LSOFS is wind-forced so it's not independent of the model. |
| **in-situ 6 m logger** | **The only adequate truth — does not yet exist (human task, ADR-019).** |

## Consequence for the commissioning decision (ADR-006)

G1 (temperature) is marginal vs a surface proxy; G2 (events) is unscoreable against any remote
truth. Both gates are stymied by the same root cause: **no independent subsurface truth is
reachable.** This is the central Phase-0 finding — the predictability ceiling for the
temperature/upwelling layer cannot be established from remote data alone.

Decision: **do NOT demote the layer** (it is physically sound, wind-consistent, correctly
paced) and **do NOT ship alerts on it as validated** (its real-world skill is unproven).
The critical path is the Silver Harbour 6 m logger. Once ~one upwelling season of logger data
exists, G1 and G2 become answerable at 6 m and this scorecard is re-run against real truth.
