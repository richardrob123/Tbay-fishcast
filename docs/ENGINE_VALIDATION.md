# Engine validation — temperature skill against buoy truth

> **⚠ AUDIT_ROUND3 (2026-08-06): the headline numbers below are contaminated by
> fit/validate overlap.** The pooled correction was fit on Aug 1–4 2026 at the same
> three buoys this document scores it on, over a window containing those days. The
> decision-skill table (POD 0.81 / FAR 0.00 / acc 0.92) and the 78% band coverage are
> **in-sample**; LOBO holds out a buoy but not time (fold corrections ≈ identical).
> Treat every number here as an optimistic in-sample ceiling. The honest numbers are
> in `docs/AUDIT_ROUND3.md` §honest-numbers; clean re-derivation is accumulating via
> `data/gate_log.csv`.

**Question.** Thunder Bay has no in-water thermometer. Can LSOFS + our correction
tell where the target-temperature water sits, well enough to trust — and does it
*generalize* to a site with no local sensor?

**Method.** Every NDBC subsurface buoy in Lake Superior with a live sensor (45027 @
6 m, 45023 @ 5 m, 45216 @ 3 m), over the full ~30-day window where LSOFS and buoy
data overlap (July–Aug 2026), tight-matched to model time. 88 paired model/truth
points. Reproduce: `python scripts/validate_engine.py gather && … report`.

## Results

### 1. Temperature skill — the correction halves the error
| buoy | z | n | raw MAE | raw bias | corrected MAE | r | anomaly r |
|------|--:|--:|--------:|---------:|--------------:|--:|----------:|
| 45027 | 6 m | 28 | 3.35 | +3.34 | **1.74** | 0.76 | 0.82 |
| 45023 | 5 m | 30 | 3.43 | +3.43 | **1.36** | 0.95 | 0.93 |
| 45216 | 3 m | 30 | 3.25 | +3.25 | **1.09** | 0.78 | 0.65 |

LSOFS runs a consistent **+3.3 °C** warm at these depths; removing it cuts MAE from
~3.3 to ~1.1–1.7 °C, with correlation 0.76–0.95 and detrended **anomaly correlation
0.65–0.93** (real timing skill, not just tracking the seasonal trend).

### 2. Generalization — leave-one-buoy-out (the Thunder Bay transfer test)
Fit the correction on the *other* buoys, predict the held-out one:

| held out | correction (from others) | MAE | raw MAE |
|----------|-------------------------:|----:|--------:|
| 45027 | −3.34 | 1.74 | 3.35 |
| 45023 | −3.29 | 1.34 | 3.43 |
| 45216 | −3.38 | 1.10 | 3.25 |

**LOBO mean MAE = 1.39 °C.** This is the honest number for transferring the
correction to a site with no buoy — i.e. what to expect at Thunder Bay. The
correction is spatially stable (−3.3 ± 0.05 across folds).

### 3. Decision skill — "is 12 °C water at this depth?"
Contingency of the actual product decision (corrected model vs buoy truth):

- n = 88 · TP 29 · FP **0** · TN 52 · FN 7
- **POD 0.81 · FAR 0.00 · accuracy 0.92**

The model calls cold-water-present correctly 92 % of the time, with **zero false
alarms** — it never says cold water is reachable when it isn't (fail-safe for the
angler). Its errors are *misses* (7): it sometimes says "not there" when cold water
had in fact reached the depth. Conservative, which is the right way to be wrong.

### 4. Band calibration — the honest interval is honest
The product's ±band (subsurface correction 3.3, band 1.5…5.5 °C) **contains the
truth in 78 % of cases** (target ~68 % for ±1σ) — slightly conservative, well
within calibration. The uncertainty we advertise is real.

### 5. Baseline — the humbling part (ADR-008)
Versus persistence ("same temperature as yesterday"):

- 45027: corrected 1.80 vs persistence 1.76 — **loses**
- 45023: 1.33 vs 1.15 — **loses**
- 45216: 1.11 vs 1.22 — beats

Day-to-day at a fixed point, the model is only *competitive* with persistence, not
clearly better. **But persistence needs a local measurement yesterday — which
Thunder Bay does not have.** Where you have no sensor, persistence is unavailable
and the model's LOBO 1.39 °C is the operative number. The model's real edge is the
two things persistence cannot do: resolve the **spatial** field (where along the
shore the cold water is, via the bathymetry transect) and catch **turning points**
(upwelling onset — the anomaly correlation, and the Aug-4 event in
`BACKTEST_UPWELLING.md`).

## Verdict

The temperature engine is **validated and generalizes**: ~1.4 °C transfer error,
92 % decision accuracy with zero false alarms, honest calibration. The correction is
data-driven (measured, not assumed) and spatially stable. The candid limit: for
day-ahead point temperature it doesn't beat persistence — so the product is honest
to sell as a **spatial + turning-point** tool (where is the cold water today, is it
shoaling), not a precision thermometer. A single Thunder Bay subsurface logger would
both collapse the band and let persistence/data-assimilation improve the temporal
side.
