# Pre-registered protocol: catch-report back test of the spatial ranking (ADR-042)

**Status: FROZEN before analysis.** This protocol is written and committed BEFORE any
correlation between observations and model scores has been computed (rule 7 — pre-registration —
applied to our own validation). Any change to this protocol after the first analysis run
requires a new ADR that reports results under BOTH the old and new protocol.

## Question

Do dated, located shore fishing reports (catches / run sightings) concentrate on the days and
places this system scores higher — beyond what effort patterns (weekends, season, popular
spots) explain?

## Outcome data

Rows from `knowledge/observations/observations.jsonl` that pass ALL of:
- `kind` ∈ {catch, run_status}, `confidence ≥ 0.5`;
- `date_precision = day`; date within the ice-free season (May 1 – Nov 30);
- `place_id` resolved (point/tributary scope). Region-scope rows are EXCLUDED from the spatial
  back test (they carry no place signal) but are used in the run-calendar fit.
- Species mapped to a modelled species (chinook/coho/pink → salmon).

## Prediction reconstruction (retro-score)

The LSOFS forecast archive does not exist (nowcast-only — verified 2026-08), so retro-scores
use ONLY layers with true archives, and this degradation is stated with every result:
- STRUCTURE: static (CHS NONNA) — identical to live.
- SURFACE THERMAL: GLSEA daily archive + the measured nearshore delta by exposure class;
  thermocline context from the mooring climatology (no per-day bottom-temp field).
- UPWELLING PHASE: reconstructed from archived CYQT winds with the live phase rules.
- RIVER FLOW: HYDAT/ECCC archive + flow climatology percentiles.
- RUN CALENDAR: the CLIMATOLOGICAL windows as of the analysis date (never windows re-fitted
  from the same reports being tested — no target leakage).
The retro-score for (stretch, species, date) uses the live ranking's boolean-intersection
index over these layers. Where a layer is unavailable for a date, the day is dropped, not
imputed.

## Design: matched controls (effort confounds)

For each qualifying report R at (place p, species s, date d):
- CONTROL SET: all dates d' in the same calendar month of the same year, same weekend/weekday
  class as d, at the same p and s, with NO report of s at p on d'.
- Require ≥ 6 controls; else R is dropped (logged, counted).
- STATISTIC per report: percentile rank of retro-score(p, s, d) within {d} ∪ controls.

## Analysis (all pre-specified)

1. Primary: one-sided Wilcoxon signed-rank test that report-day percentile ranks exceed 0.5,
   per species; α = 0.05 with Holm correction across species. Effect size: median percentile
   rank with bootstrap 95% CI (10,000 resamples).
2. Minimum n: a species is REPORTED (never hidden) but flagged "insufficient" below 30
   qualifying reports.
3. Splits (rule 6): reports dated ≤ 2024-12-31 may inform any tuning; the headline result is
   computed ONLY on reports dated 2025-01-01 onward, untouched by tuning. If no tuning ever
   uses the pre-2025 rows, the headline may pool all years — stated either way.
4. ALL modelled species are analyzed and reported, including failures. No subsetting of
   sources, years, or places beyond the qualification rules above.

## Interpretation commitments

- Median percentile rank ≈ 0.5 (CI covering 0.5): the ranking adds no measurable catch signal
  → the demotion rule (rule 8) applies to the ranking layer's presentation as "Best spots".
- Significantly > 0.5: reported with the effect size and the reconstruction caveats; the
  fitted tier-weight upgrade (top_spots) may then proceed, fitted on ≤2024, validated 2025+.
- Selection-bias caveat stated with every result: reports are effort-and-success biased;
  matched controls address WHEN-bias at a place, not WHERE-bias across places. Cross-place
  claims are limited accordingly.

## Reporting

Results land in `docs/ACCURACY_SCORECARD.md` under "Catch back test", with: n per species,
dropped-report counts and reasons, the per-species median rank + CI + p, the protocol file's
git hash at analysis time, and the date the ledger was frozen for the run.
