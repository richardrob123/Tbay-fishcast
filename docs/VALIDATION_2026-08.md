# System Validation — 2026-08-07

A ground-up validation after the ADR-024→032 work, to answer three questions the accumulated
patch-work could no longer answer by inspection: **(1) does the model cohere or is it patches
fighting patches? (2) is it actually accurate, and is the accuracy reporting honest? (3) are we
leveraging all the useful data?** Four independent read-only audits (data-path coherence,
data-source inventory, accuracy/honesty, determinism/robustness/safety) plus a live end-to-end
build and direct output inspection. Findings below are cross-corroborated where two audits
overlapped.

## Verdict

The **live model logic is coherent and defensible** — the fishable-zone conjunction (thermal
suitability ∩ measured structure, per-species depth gate, Voronoi territory clip, phase kept
separate as a timing banner) does what the ADRs claim, with no fitted weights and strictly
disjoint tiers. The **temperature field is real** (6–16 °C, six isotherms derived from the species
niches) and the **structure bars are the strongest, genuinely data-derived part** (pooled p90 over
464k px). A live rebuild **reproduced the committed manifest exactly** — the pipeline is
deterministic.

But the validation surfaced **one safety-critical gap, two real correctness bugs, a cluster of
honesty gaps where the caveats stop at the JSON layer and don't reach the map, and a layer of
patch-on-patch scaffolding that makes the code *read* as incoherent even where it runs correctly.**
The most consequential accuracy truth: **the product is a physically-principled model with almost
no independent outcome validation** (n_effective ≈ 1). That's honestly the ceiling until local
truth (field logs / a Thunder Bay logger) exists — no amount of threshold-tuning changes it.

## Fixed in this pass

- **[SAFETY-CRITICAL] Regs gate wired into the output.** `regs_gate.py` was tested as a security
  invariant but had ZERO callers — closed-water protection rested entirely on human list-curation
  (the Kakabeka-pin failure mode). Now every recommendation surface (stretches, stations,
  river-mouth markers) passes `is_prohibited(name, issue)` in both `build_coast_site.py` and
  `heartbeat.py`; drops are loud; `validate()` provenance violations surface as a go-live blocker.
  No regression (every real water passes today). Tested end-to-end.
- **[CORRECTNESS] Sentinel-through-griddata contamination.** `_iso_field` interpolated the 999
  no-crossing sentinel against real depths, manufacturing spurious 22–899 m isotherms at the cold
  frontier. Now grids depth from real crossings only + emits the sentinel by node-majority, with a
  degenerate-triangulation fallback. Property-tested.

## Consolidation completed (follow-up pass, 2026-08-07)

Nearly all of the ranked open items below were then fixed and pushed:
- **#1 anchor divergence** — map & pins now share `features.nearshore.nearshore_surface_delta`.
- **#2 censored-ref caveat** — the ±m tooltip states it's provisional absolute error vs a stable
  reference (0 moving-obs chains), not validated skill; "no lead trend yet measurable (n=25)".
- **#3 tier cutoffs** — replaced the 0.7/0.15 picks with the cited bands (fair=range, good=optimal
  plateau); then the whole render went **hybrid** (ADR-033): discrete temperature × CONTINUOUS
  structure glow with data-derived (regional p90/p95/p99) band edges. Silver-Islet "prime" 48%→9%.
- **#4 train-on-test** — scorecard flags the diagnostic chain that also feeds the bias (upper bound).
- **#5 phase thresholds** — manifest now reports both the 10 kn observed and 13 kn forecast bars.
- **#6 weak-cue** — the DATA path no longer emits structure glow for salmon/steelhead (tested).
- **#8 dead code** — TARGETS / empty line pipeline / stale docstrings removed.
- **#9 frozen masks** — re-froze the 3 stretches that were silently hitting live Overpass.
- **#10 LSOFS timeout** — 60 s client timeout on the one unbounded hot-path fetch.
- **Data levers:** the GLERL **mooring climatology** is now an offshore LSOFS cross-check
  (`check_offshore_climatology.py`; first run: model offshore mixed-layer 11.42 vs clim 11.54 °C);
  the Landsat nearshore delta now reaches the **pins** too (via #1). The heatmap was
  headless-rendered and eyeballed at many spots (rocky / flat / city / weak-cue).

Still genuinely open (smaller or blocked): nearshore +2.35 °C taper as n grows (#7); heartbeat
missing-anchor flag (#11); a tiny-grid `_overlay` golden for the depth-gate/clip (#12); the
FLake bias-envelope (needs a CDS key in CI); a **live buoy/METAR wind gate** (the next real lever —
same accumulate-then-verify shape as the offshore check, needs the NDBC stdmet wind parser); GLOS
depth-gate still HTTP-000 (route via UMD LLO CSVs). Original ranked list follows for the record.

### Correctness
1. **[HIGH — FIXED] Map vs station-pin surface-anchor divergence.** The map warms the GLSEA anchor by the
   measured nearshore delta (`build_coast_site.py:543`, +2.35 °C); the pin/heartbeat path uses raw
   GLSEA (`forecast_window.py:88`). Two published views disagree on the same water — pins read
   ~2.35 °C colder → deeper isotherm → more "reachable" than the polygons over them. *Fix:* factor
   `_nearshore_delta` into a shared module and apply it in both paths (or record why they differ).

### Honesty (caveats that don't reach the map the angler sees)
2. **±1.79 m and "no lead trend" rest on a censored reference.** The forecast-gate "observations"
   are constant per chain (0 moving-obs), and `lead_trend_detected:false` is partly structural
   (an n≥40 gate can't detect a trend at n=25). The JSON self-flags this; the UI tooltip does not.
   *Fix:* propagate the "absolute error vs a censored reference — provisional" caveat into the UI;
   soften the internal "day 5 ≈ day 1" justification to "no lead trend is yet *measurable*."
3. **`OPTIMAL_SUIT=0.7` / `IN_RANGE_SUIT=0.15` are unsourced picks** that decide the entire
   fair/good/prime map. *Fix:* derive them from the thermal-preference curve shape with a written
   basis, or relabel the tiers as *relative* rather than implying calibrated habitat quality.
4. **The "+47 % correction beats raw" headline is n=1 AND train-on-test** (buoy 45216 is both a
   bias-pool input and the sole diagnostic chain). *Fix:* exclude bias-pool chains from the skill
   number, or report "insufficient independent data" until local truth exists.
5. **`OBSERVED_THRESHOLD_KN=10` silently undercuts the stated 12–17 kt Wedderburn prior**, and the
   whole phase banner rests on picked constants with zero local validation. *Fix:* ADR-justify the
   10 kt choice; label the phase banner explicitly as an unvalidated physics heuristic.
6. **Weak-cue suppression lives only in the UI.** The manifest + geojson still carry confident
   `sp:salmon:s3` prime; any non-web consumer sees false precision. *Fix:* move the suppression (or
   a `weak_cue` flag the data path honors) into the emitted data.
7. **Nearshore +2.35 °C is applied at full strength from n=3 scenes on one date.** *Fix:* taper/cap
   until n and date-spread grow.

### Consolidation cleanup (make the code tell the truth about itself)
8. Dead `TARGETS=(12,10,8)` constant with false provenance (build uses `cfg.band_temps` = 6–16 °C);
   `_bottom_temp_field`/`suitability.py` docstrings describe a superseded design (ADR-029);
   orphaned `thermal_front_gradient`; permanently-empty `lines` pipeline still written; unused
   `cold_reachable` import; vestigial `front_c`; manifest reports phase threshold 10 kn while the
   forecast tail uses a bare `13.0` literal. Delete/relabel so the code matches the ADRs.

### Robustness / determinism
9. **3 stretches miss their committed frozen mask** → live Overpass every build (non-deterministic;
   loud-miss fired). *Fix:* re-freeze all current stretches and commit.
10. **LSOFS fetch has no timeout** (`backfill._cat_bytes`, aiohttp 300 s default) — the one
    unbounded call in the 4×/day hot path. *Fix:* `client_kwargs={"timeout": …60s}`.
11. Missing-GLSEA-anchor is flagged in the coast manifest but silent in the heartbeat/pin path.
12. Untested live-map masking branches: per-species depth gate + Voronoi clip (the named property
    tests cover `sigma.interp_column`, which the live path doesn't use). Add a tiny-grid `_overlay`
    golden + depth-gate/clip unit tests.

## Data-source consolidation — the real accuracy levers (all already-fetched data)

The inventory found **17 sources**; the cheapest high-value moves are consolidating data we
already pull, not adding sources:

- **Wire the GLERL mooring climatology** (`knowledge/mooring_superior_climatology.json`) — built,
  committed, and consumed by nothing but a unit test — as an **offshore plausibility envelope** on
  the live subsurface bias. Free (offline JSON). Catches a bad-bias day before it flips every
  station's verdict.
- **Gate the live bias with ERA5-FLake** (the only genuinely independent subsurface physics model,
  already coded for validation) — pre-compute a half-month envelope offline and commit it; flag
  `degraded` when the live bias falls outside. Bounds the tail, needs a CDS key in CI.
- **Feed the logged Landsat nearshore offset into the *pin* anchor too** (the map already applies
  it; the pins don't — this is finding #1).
- **Live wind cross-check:** NDBC buoy anemometer + CYQT METAR (both already fetched) vs the GFS
  ensemble day-0/1; downweight the upwelling probability when observed and forecast W-quadrant wind
  disagree. The wind-model eval explicitly deferred "the true over-lake test" to this.
- **GLOS reachability emergency:** GLOS ERDDAP is HTTP-000 here, silently killing the
  isotherm-depth scorecard (masked by `continue-on-error`). Route around it via the **UMD LLO
  CSVs** (same physical buoy, reachable host) or allowlist GLOS in CI egress.
- **Cleanup:** `ndbc_slate.py` is dead (`NotImplementedError`, no importers); the "blocked"
  reachability notes on ECCC/slate are stale (now HTTP 200). Correct or retire.

### Honest ceilings (where more data will NOT help)
- **No live, local, depth-resolved Thunder Bay subsurface feed exists.** Every candidate is a
  200 km western-lake proxy, an offline climatology, or a discontinued grab-sample. More *proxy*
  sources can only *bound* LSOFS's bias (the moves above), not give nowcast truth. The genuine
  unlock is a DIY logger string at the actual cast spots — operator field-logging, not another
  remote feed.
- **Bathymetry (NONNA-10) and *forecast* wind skill (ICON tested and refused) are at their
  ceilings.** The remaining wind lever is observed cross-checking, not a finer model.
