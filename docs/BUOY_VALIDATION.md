# Buoy validation — LSOFS vs in-situ truth (the first legit commissioning read)

**This is the real one.** Not satellite, not physics-plausibility — LSOFS scored against a
real thermistor at a 52 m-deep Lake Superior upwelling shore (NDBC 45027/45028), matched in
depth and location, full 2024–25 seasons at 1 m plus 2026 at 6 m. Reproduce:
`python scripts/validate_buoy.py 45027 2025`.

## Verdict

**Raw LSOFS is not accurate enough to ship — it fails G1 at every depth (MAE 2.7–3.7 °C vs
a 2.0 target) — BUT the failure mode is depth-dependent and, at 6 m, largely correctable:**

- **1 m (near-surface): irreducible.** Error is variance-dominated (centered RMSE ~4 °C);
  removing the bias barely helps and correlation is weak (r 0.36–0.62). LSOFS cannot track the
  fast, sharp 1 m surface signal. Not salvageable by calibration.
- **6 m (the product's target depth): salvageable.** Error is *bias*-dominated — a systematic
  **+3.5 °C warm offset** — with a much smaller residual (centered RMSE **2.5 °C**) and the
  best correlation seen (**r 0.68**) and moderate event skill (**POD 0.60, FAR 0.40**). LSOFS
  gets the 6 m *timing/variability* roughly right but sits too warm (it under-cools the
  upwelled water — thermocline too deep/weak).

**Implication:** the original design instinct to target **6 m** is vindicated, and the path to a
usable product is **LSOFS 6 m + an in-situ bias correction.** That sharpens the logger's role
from *validator* to *calibrator* — the single most valuable thing you can deploy.

## The numbers (matched depth + bathymetry; fair comparisons)

| case | depth | MAE | bias | RMSE | centered RMSE¹ | r | POD | FAR | n |
|---|---|---|---|---|---|---|---|---|---|
| 45027 2024 | 1 m | 3.50 | −1.66 | 4.45 | 4.13 | 0.47 | 0.33 | 0.86 | 321 |
| 45027 2025 | 1 m | 3.28 | −0.87 | 4.33 | 4.24 | 0.36 | 0.33 | 0.89 | 429 |
| 45028 2025 | 1 m | 2.70 | +1.02 | 3.24 | 3.08 | 0.62 | 0.00 | 1.00 | 429 |
| **45027 2026** | **6 m** | **3.73** | **+3.55** | 4.32 | **2.46** | **0.68** | **0.60** | 0.40 | 175 |
| 45028 2026 | 5 m | 6.54 | +6.54 | 6.73 | 1.59 | 0.21 | — | — | 35² |

¹ centered RMSE = √(RMSE² − bias²) = the error that remains after perfect bias removal.
² n=35, buoy range 5.1–6.5 °C — a just-deployed/suspect sensor; down-weighted, not load-bearing.

## What each depth is telling us

- **The unifying physical story:** LSOFS **under-represents the cold upwelled water** — too warm
  in early-season at 1 m (both buoys ~5 °C warm in June, missing the cold pulses) and a steady
  +3.5 °C warm bias at 6 m. Its shore upwelling is too weak/shallow.
- **Why 1 m is worse than 6 m:** the 1 m signal is fast (diurnal + skin + sharp upwelling fronts)
  and LSOFS can't time it → variance error. The 6 m signal is seiche-paced/smoother, so the
  model tracks the timing (r 0.68) and only the level is off → bias error, which is correctable.
- **Events:** 1 m POD 0.00–0.33 (useless); 6 m POD 0.60 / FAR 0.40 (approaching, not yet at, the
  0.70/0.30 gate). Depth helps here too.

## Honest limits

- The decisive **6 m** read rests on **one partial season** (45027, Jun–Aug 2026, 175 pts). It is
  strongly suggestive, not definitive; more 6 m in-situ data (a logger, or future buoy seasons)
  is needed to confirm the +3.5 °C bias is stable and correctable.
- These buoys are **western Superior**, not Thunder Bay. They validate the *model's* 6 m upwelling
  behaviour (which transfers — same model, same lake), but the **bias magnitude is likely
  location-specific**, so a Thunder Bay bias correction still needs the Thunder Bay logger.

## Consequence for the commissioning decision (ADR-006)

- **Raw LSOFS: do NOT ship** — fails G1/G2 against ground truth at every depth.
- **Do NOT fully demote either** — at 6 m the error is bias-dominated with r 0.68 and POD 0.60,
  i.e. there is real, extractable signal once calibrated.
- **The product is a bias-corrected LSOFS 6 m layer**, and the enabling step is an in-situ 6 m
  logger at (ideally) each shore station for the local offset. Next analysis: does bias-corrected
  LSOFS 6 m beat day-of-year climatology (the ADR-006 bar)? — the remaining piece to close the
  keep/demote call.
