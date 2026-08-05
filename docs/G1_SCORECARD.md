# G1 scorecard — LSOFS surface SST vs GLSEA (first real read)

**Gate G1:** LSOFS MAE ≤ 2.0 °C, ice-free months, vs best available truth.
**Verdict (two validation months, July & August 2025): G1 is NOT met — pooled MAE
2.03–2.30 °C, consistently just over the 2.0 threshold, with the error concentrated at
the exposed upwelling stations.** The sheltered station (Marina) passes in August. This
is a real result feeding the keep/demote decision, not a number to tune away
(ADR-006/008).

## Method (see `scripts/g1_scorecard.py`, tested in `tests/test_g1_pairing.py`)

- **Model:** LSOFS surface temperature = topmost sigma layer (~0.3 m), extracted from
  every nowcast valid-hour, deduped across cycles (latest issuance wins), averaged to a
  **daily-mean** per UTC day (≥18 valid hours required, else the day is dropped, not
  thinly averaged). Daily-**max** is also reported to bracket GLSEA's unknown temporal
  weighting. Diurnal aliasing is thus removed, not hand-waved.
- **Truth:** `GLSEA_ACSPO_GCS` daily SST at the nearest valid water pixel to each LSOFS
  **node's own coordinate** (model and truth sample the same water; pixel distance
  recorded, mean 0.5 km). Cloud-gapped pixels drop that day (0 gaps in July 2025).
- **Stats:** MAE, bias, RMSE, median|e|, p90|e|, Pearson r — per station and pooled.
- **Honest limit (ADR-019):** surface model SST vs surface skin truth. NOT the 6 m gate;
  the in-situ logger is the real adjudicator. Every line carries the caveat.

## Result — two validation months, 2025 (daily-MEAN model vs GLSEA)

**July 2025** (93 station-days, 0 truth gaps):

| station | n | MAE | bias | RMSE | med\|e\| | p90\|e\| | r |
|---|---|---|---|---|---|---|---|
| silver_harbour_outer | 31 | 2.28 | −0.26 | 2.79 | 2.43 | 4.33 | +0.18 |
| mackenzie_point | 31 | 2.21 | −0.63 | 2.89 | 1.79 | 4.63 | +0.20 |
| marina_east_mcvicar | 31 | 2.42 | +0.94 | 2.75 | 2.79 | 3.98 | +0.11 |
| **POOLED** | **93** | **2.30** | **+0.01** | **2.81** | 2.23 | 4.26 | **+0.15** |

**August 2025** (93 station-days, 0 truth gaps) — peak upwelling month:

| station | n | MAE | bias | RMSE | med\|e\| | p90\|e\| | r |
|---|---|---|---|---|---|---|---|
| silver_harbour_outer | 31 | 2.09 | −1.77 | 2.68 | 1.27 | 4.55 | +0.60 |
| mackenzie_point | 31 | 2.50 | −2.26 | 3.19 | 1.80 | 5.43 | +0.71 |
| marina_east_mcvicar | 31 | **1.49** | −0.48 | 1.93 | 1.15 | 3.34 | +0.49 |
| **POOLED** | **93** | **2.03** | **−1.50** | **2.65** | 1.34 | 4.78 | **+0.56** |

Daily-MAX framing (brackets GLSEA's temporal weighting): July pooled MAE 2.73 (bias +1.79);
August pooled MAE 2.03 (bias −0.01).

## What it means (and what ruled out the alternatives)

- **Both months fail G1** (pooled MAE 2.03–2.30 > 2.0), consistently and by a small margin.
- **The error is concentrated at the exposed upwelling nodes.** Marina (breakwall-sheltered)
  is best in both months and **passes in August (1.49)**; Silver Harbour and MacKenzie
  (open north-shore, the upwelling-exposed stations) are worse. The temperature layer is
  least trustworthy exactly where the product most relies on it — the upwelling shore.
- **The disagreement's *character* is seasonal, its magnitude steady.** July: ~0 bias but
  near-zero correlation (r 0.15) — scatter. August: a −1.5 °C cold bias with *good*
  correlation (r 0.56) — LSOFS tracks the day-to-day signal but runs cold, consistent with
  it resolving more/colder upwelled water in the peak-upwelling month ("the August
  exception"). Either way MAE sits ~2.0–2.3 °C.
- **Verified real, not an artifact.** A whole-column 3 °C change in 9 h at Silver Harbour
  (impossible by solar heating; upwelling relaxation, setup ~10 h / seiche ~40 h per the
  seed physics) confirms LSOFS is resolving genuine upwelling that GLSEA's smooth daily
  composite (daily std ≈ 0.8 °C) does not. Method checks: layer 0 is the surface (monotonic
  column); node and GLSEA pixel sample the same water (0.5 km apart); daily-mean removes
  diurnal aliasing; bias survives the daily-max framing; GLSEA has genuine variance so the
  July low-r is meaningful, not ill-conditioned.

**Who is right — LSOFS's upwelling swings or GLSEA's smooth field — cannot be settled
from these two alone.** That is precisely the ADR-019 gap: GLSEA is a surface satellite
composite with known near-shore limitations; LSOFS is the model under test. The in-situ
6 m logger at Silver Harbour is the adjudicator, and until it exists G1 is a
LSOFS-vs-satellite agreement number with a stated caveat, not a clean 6 m verification.

## Implication for the gate / demotion rule

Against GLSEA-surface truth, the temperature layer does **not** clear G1 in either month
(pooled MAE 2.03–2.30 > 2.0), and its error is worst at the exposed upwelling nodes it most
needs to get right. Per ADR-006 this is a candidate for demotion toward wind-driven
Wedderburn alerts — **pending** (a) the in-situ logger (ADR-019: only it can say whether
LSOFS or GLSEA is right about the upwelled water), and (b) event-level verification (G2),
because a layer can miss daily SST yet still call upwelling *events* well — and August's
higher correlation (r 0.56) hints it may. G2 is the next real read; it needs ERA5 wind
(now reachable) and, for threshold tuning on the 2024 season, a `regulargrid` reader
(ADR-018).

## Reproduce

```bash
python scripts/g1_scorecard.py 2025-07-01 2025-07-31   # ~6 min, 896 files, live sources
python scripts/g1_scorecard.py 2025-08-01 2025-08-31
```
