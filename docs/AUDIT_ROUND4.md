# Audit Round 4 — full accuracy / data / modeling review (2026-08-07)

Four parallel read-only audits (data & ingest, temperature & forecast accuracy, fishable-zone
modeling, robustness/determinism) after the ADR-024→029 fishable-zone work. Findings synthesized
and de-duplicated below, ranked into execution tiers. Line refs were accurate at audit time.

## Headline reframe

Two things I had called "data-limited" are actually **fixable now**:
1. **The shallow/enclosed-basin "too cold" over-cover is a BUG, not a sensor limit.** `TARGETS`
   tops out at 12 °C, so `_bottom_temp_field` *clamps* any pixel shallower than the 12 °C isotherm
   to 12 °C — the field literally cannot represent water warmer than 12 °C — and then silently
   nearest-fills the no-node marina from offshore (deep, cold) isotherm depths. So 18–22 °C marina
   water renders as 12 °C laker habitat. Fix: add warm targets (16/18/20 °C) + stop the silent
   extrapolation. No sensors needed.
2. **Better nearshore subsurface truth is already coded but unused.** GLOS multi-depth thermistor
   chains (Ontonagon 0–12 m, Duluth 0–43 m), the GLERL mooring prior, and ERA5-FLake are wired into
   validation scripts only — never into the live bias correction. Wiring the GLOS 0–12 m profile in
   makes the correction profile-based instead of one offshore scalar.

The validation is *methodologically* honest (no truth-leak, temporal splits, loud degraded mode,
demotion gate) but *statistically near-empty*: the "+47% skill / 2.12 m MAE" rests on **n=1** real
varying comparison; the rest is a pinned sensor floor.

---

## TIER 1 — real accuracy bugs + honesty gaps (do first; no new data)

- **A1. Warm-target grid + stop silent extrapolation (marina over-cover).** Add 16/18/20 °C (and a
  cold clamp, see A2) to the isotherm targets so shallow warm columns resolve to their real temp and
  fall OUT of the laker range. Carry the linear-NaN hull mask from `_iso_field` as an
  "extrapolated / no local profile" flag; leave those pixels unshaded or low-confidence instead of
  nearest-filling offshore values. Optionally cap `bottom_c` at the GLSEA surface anchor for columns
  shallower than the nearest contributing node. *(temp H2, data, modeling)*
- **A2. Cold clamp for strong upwelling.** A fully-cold column (<8 °C throughout — the exact event
  the product exists to flag) currently maps to 999→NaN→unshaded. Add a cold clamp symmetric to the
  warm clamp so the strongest-upwelling water shades, not blanks. *(temp L1)*
- **A3. Per-species minimum hold depth.** `within` has a global max depth (22 m) but no minimum, so
  lake trout can be flagged "prime" in 1–3 m water they don't hold in summer daylight — while the
  same shallow-cold shading is *correct* for coaster brook trout (<7 m). Add `min_depth_m` per
  species in `stations.yaml`, gate per-species. *(modeling H1)*
- **A4. Surface per-stretch health flags → manifest + UI.** `degraded` shore (fabricates 65–90 %
  fake castable shore), a missing GLSEA anchor (`anchor_day=None` → surface leg of the correction
  vanishes, inflating reachable area), and a dropped stretch are all computed but only `print`ed —
  the client shows a *global* staleness banner and cannot warn per stretch. Thread a per-stretch
  health flag into the manifest and badge it. *(robustness #3/#4/#5, rule 5)*
- **A5. Show lead-dependent uncertainty on the map.** The reachability/isotherm layer reuses the
  nowcast bias band at every lead, so a +120 h "GO" reads as confidently as today (the phase banner
  already decays; the zones don't). Fold the per-lead corrected MAE (already in
  `forecast_gate_log.csv`) into the band half-width per lead, and decay the verdict confidence by
  lead. *(temp H3)*
- **A6. Honest scorecard.** Exclude `obs_constant` (floor-pinned) chains from the pooled *skill*
  number, print `n_effective` (currently 1) beside every %, and suppress the lead-decay table until
  ≥2 chains have a moving obs across ≥2 leads. *(temp H1/M3)*
- **A7. Phase forecast threshold.** The forecast tail is the over-lake ensemble control but is tested
  at the *airport* 10 kn threshold → over-detects blows on forecast days. Use 10 kn for the observed
  segment, ~13 kn for the ensemble-fed segment. *(modeling M3)*
- **A8. Frozen-mask determinism + loudness.** The frozen key is `round(bounds,3)` on the live CHS
  WCS float bounds; sub-mm server drift → frozen miss → **silent** live-Overpass fallback (undoes
  ADR-020 invisibly). Derive the key from deterministic inputs (center + HALF_M + PX) or round to
  ~1 m, and log loudly on a miss. *(robustness #1/#2)*

## TIER 2 — accuracy via wiring existing/reachable data

- **B1. Wire GLOS 0–12 m thermistor profiles into the live bias** so the nearshore correction is
  profile-based, not one offshore scalar (highest-leverage accuracy work; mostly existing code).
  *(data H1)*
- **B2. Depth-resolved bias** — regress LSOFS bias vs sensor depth instead of pooling 3/5/6 m sensors
  into one scalar stamped at z_ref=6 m. *(data H2)*
- **B3. Shoal-top / point-tip structure** — `bathymetric_structure` is `|∇depth|` only, catching
  drop-off flanks but missing the flat top of a shoal or the tip of a point (classic prime structure
  that reads low-slope). Add a plan-curvature / local-bathymetric-maximum term. Directly measured.
  *(modeling opp #2)*
- **B4. Weak-cue species treatment** — salmon/steelhead get full thermal authority where the model
  says temperature barely predicts; fade their thermal shading and lean on a plume signal (B5).
  *(modeling H2)*
- **B5. River-plume proximity field** — replace the static pins with a distance-decay (ideally
  wind-advected) plume field; the honest home for the weak-cue species. *(modeling M4)*
- **B6. Finer wind** — switch the ensemble to `icon_seamless` and/or add an HRDPS 2.5 km
  deterministic day-0/1 overlay; GFS 0.25 °C (~28 km) poorly resolves the over-lake blow.
  *(data M1)*
- **B7. Nearshore band inflation** — add the documented ±~1 °C inter-site bias spread as an explicit
  labelled band term until a Thunder Bay logger exists. *(temp M2)*

## TIER 3 — modeling realism + polish

- Relaxation window is capped at 48 h; the review's prime window is 1–5 days — extend toward
  72–96 h or decay rather than a hard cliff; scale peak-suppression by event magnitude. *(modeling M6)*
- Cold-side thermal taper (lakers are fine to ~4 °C; currently <6 °C reads unshaded). *(modeling L8)*
- Dead uncertainty ribbon: `f_sh/f_de` bookends are computed then discarded (`lines` ships empty),
  contradicting the docstring/ADR-026 "ribbon" claim — wire it or delete + correct the docs. *(temp M1)*
- Time-of-day low-light window (dawn/dusk); deterministic from sun times. *(modeling opp #4)*
- Seasonal *spatial* shift (fall shoal-staging is a different target), not just a text badge.
  *(modeling opp #5)*
- Exposure-weighted phase — use `exposure_bearing_deg` vs live wind so "when" is spatially
  differentiated, not one region-wide banner. *(modeling opp #6)*
- Provenance: commit the NONNA slope-distribution measurement that justifies `STRUCT_SLOPE_ABS=0.15`
  (currently asserted, not recorded — rule 3). *(modeling M5)*
- Robustness/perf: interpolate only the near-shore (`dist<=cast`) mask (the ~10-min hot path); test
  the degraded-shore branch + a headless map-render smoke test; make the missing-LSOFS-lead skip
  loud; stabilize the live-mask component sort. *(robustness #6/#8/#9/#10)*
- Regs: the coast map draws over raw water with no in-code closed-water clip (station pins are
  gated); currently enforced only by human curation of the STRETCHES list (ADR-007, by design) —
  consider an in-code regs clip for the map too. *(robustness #7)*

## What is already solid (keep)

No truth-leak in the forecast gate (issue-time GLSEA anchor); temporal-split discipline; loud
degraded/frozen-prior mode end-to-end; client-side age recompute; demotion gate; safe rebased
commit-back with abort-on-conflict + failure alerting; LSOFS fsspec+LRU byte-cache; correct
0–360/±180 longitude handling on both readers; clamped (not extrapolated) sigma interpolation with
property tests; the hard-won NONNA NaN-as-water artifact handling; CHS NONNA-10 + LSOFS FVCOM are
genuinely the best free nearshore-resolving sources for this shore.
