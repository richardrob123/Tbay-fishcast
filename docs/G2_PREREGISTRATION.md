# G2 pre-registration — upwelling-event verification

**Frozen: 2026-08-05, before any 2025–26 validation scoring.** Knowledge-pack version
`21dc1375168b`. This document fixes the ONE primary endpoint and the tuning/validation
protocol *before* results are seen, so "G2 passes" cannot be selected after the fact from
a grid of framings (CLAUDE rule 7; adversarial-critique flaw #4 "garden of forking paths").

## Gate (as stated in PLAN, amended by the G1 finding)

PLAN G2: upwelling-event **POD ≥ 0.70** and **FAR ≤ 0.30** on held-out years.

**Amendment (binding): FAR is DEMOTED from a gate to a caveated characterization.**
Rationale (adversarial-critique fatal flaw #1, grounded in the G1 result): G1 established
that GLSEA's smooth daily satellite composite is *blind* to the sharp 6 m upwelling LSOFS
resolves (Aug 2025: LSOFS ~1.5 °C colder than GLSEA, r≈0.56, error concentrated at the
exposed nodes). A LSOFS-detected event with no GLSEA signature is therefore
**indistinguishable** between "model false alarm" and "satellite missed a real event," and
there is no independent 6 m adjudicator until the logger exists (ADR-019). A FAR gate
scored against a truth known to miss the events being detected is not a skill number.
→ **POD is the primary gate. FAR is reported as a bounded characterization with the
explicit caveat that it conflates model error with satellite blindness.**

## PRIMARY endpoint (single, pre-declared)

- **Detector:** persistence-guarded upwelling onset on the LSOFS **6 m** temperature series
  at each **exposed** shore station (`silver_harbour_outer`, `mackenzie_point`,
  `marina_east_mcvicar`), seasonally gated to the upwelling window **Jun 15 – Sep 30**.
  Drop trigger only (**not** the absolute cold-band, which conflates cold season with
  events — see below): a ≥ `DROP_C` cooling within 24 h **sustained ≥ `PERSIST_H` hours**.
- **Truth:** GLSEA_ACSPO_GCS **differential** cold event — the station pixel cools by
  ≥ `DG_C` over 48 h *relative to* an offshore-basin reference pixel (differential removes
  lake-wide seasonal cooling and isolates the shore-localized upwelling signature).
  Independent of LSOFS (satellite product; independence caveat above).
- **Matching:** EVENT-based, per station (each detection matched to its **own** station's
  truth — never lake-aggregate/any-node, which mechanically inflates skill), one-to-one,
  temporal tolerance **τ = 1 day**, episodes merged across gaps ≤ 1 day.
- **Aggregation:** pooled across the 3 exposed stations. Per-station tables reported as
  secondary context, not as the endpoint.
- **Metric:** POD = hits/(hits+misses) via `verification/scorecard.contingency`, with a
  Wilson interval **and** an explicit note that spatially-correlated station-episodes
  violate independence (one wind event upwells all three) → effective n < nominal n.

## Tuning vs validation (temporal split, ADR-004/018)

- **TUNE on 2024 (regulargrid, ice-free Jun 15 – Sep 30):** fit `DROP_C`, `PERSIST_H`, and
  the surface↔6 m coupling used to set `DG_C`. Lock them. `regulargrid` gives 6 m as an
  **exact z-level** (probe-confirmed), so no sigma interpolation on the tune side.
- **VALIDATE on 2025–2026 held out (fields nowcast, 6 m sigma-interpolated).** Thresholds
  are frozen from the tune fit and never touched on validation data.
- **Instrument cross-check (DONE — `scripts/xval_6m_instruments.py`, 2024-08-15, 75 matched
  station-hours):** regulargrid (exact 6 m level) vs fields (6 m sigma-interpolated) agree at
  **r = 0.99, RMSE 0.28 °C, mean offset −0.22 °C** (regulargrid marginally cooler). This is
  negligible against the ~4 °C event threshold, so the tune(regulargrid)→validate(fields)
  handoff is NOT a confound for event detection. Offset recorded; not applied (well within
  detector tolerance), declared as instrument uncertainty.

## Parameters to be fit on 2024 (values recorded here AFTER tuning, before validation)

| param | meaning | tuned value |
|---|---|---|
| `DROP_C` | 24 h cooling to flag an onset | _pending tune_ |
| `PERSIST_H` | hours the drop must persist (de-spike) | _pending tune_ |
| `DG_C` | GLSEA differential 48 h drop = truth event | _pending tune_ |
| surface↔6 m coupling | regression mapping 6 m event to expected GLSEA-surface signal | _pending tune_ |

## Honesty guards (from the adversarial critique)

- **No-skill baseline:** a random alarm generator emitting the SAME number of alarms as the
  detector, pushed through the IDENTICAL clustering/matching, reported alongside POD.
- **Base rate / power:** report episodes/season and event-days/season; state effective-n and
  that at n≈2–5 events/station/season the CIs are wide (±0.25–0.30). No over-claiming.
- **Cloud-gap bias:** GLSEA gaps correlate with the storms that drive upwelling; gap-day
  handling is declared (not silently dropped) and its direction on POD/FAR is stated.
- **Depth caveat (deepened):** a 6 m cold anomaly need not breach the surface in a stratified
  summer column, so GLSEA can be legitimately blind even to real events — reinforcing that
  FAR is characterization, not gate, and that the logger is the true adjudicator.
- **Independence caveat:** POD is conditional on LSOFS not assimilating GLSEA-SST (T4,
  pending the T1 LSOFS tech memo; host currently egress-blocked).

## Sensitivity (explicitly NON-primary)

τ∈{0,1,2}, merge_gap∈{1,2}, `DG_C`/`DROP_C` ±1 step, sector width, `PERSIST_H`, drop-only
vs drop+cold_band, and the alternative GLSEA_GCS day-of-year climatology-anomaly truth form
— reported as a robustness grid, never as the headline.
