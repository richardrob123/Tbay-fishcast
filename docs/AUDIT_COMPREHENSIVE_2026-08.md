# Comprehensive audit — 2026-08-07 (live UI + data + fish behavior + modeling)

Four deep lanes against the LIVE deployed site (built 21:55 UTC, 5-tier heatmap): a live-UI
inspection (desktop + mobile, all species), a fish-behavior-completeness audit, a full data-source
utilization audit, and an adversarial modeling-accuracy audit. Goal: a REAL tool with genuinely
valuable info — optimal accuracy, no arbitrary thresholds, all viable data used, fish behavior to
its full extent. Findings below are cross-corroborated where lanes overlapped.

## Headline

The spatial/thermal engine is **disciplined and mostly honest**, but three things surfaced:
1. **Real accuracy bugs at the temperature-clamp ends** (cold water over-shades the default laker;
   warm water over-shades steelhead) — and the map is **maximally green exactly when the phase
   banner says the bite is off**.
2. **The "fully data-driven / two independent axes" framing is overstated** — within a stretch the
   bottom-temperature field is a near-planar remap of the depth raster (few LSOFS nodes over an
   8.4 km box), so "temperature × structure" is largely one bathymetry raster read twice + a coarse
   thermal offset; and several "not picked" spatial choices are in fact bounded judgments.
3. **The entire TEMPORAL dimension is missing** — dawn/dusk light, barometric trend, spawning-run
   timing, dynamic plume strength — the drivers that actually decide shore catch on a given morning,
   especially for the run-driven species. Much of the fish-behavior knowledge is already committed
   (`events_calendar.yaml`, `species_rules.yaml`) but the map never reads it, and there is no
   sun-time computation anywhere.

## TIER 1 — model accuracy + integrity (do first; no new data)

- **T1a [accuracy] Cold-clamp over-shade of the default laker.** `band_temps` bottoms at 6 °C =
  laker cold floor, so `_bottom_temp_field` clamps all sub-6 °C water UP to 6 °C = laker *optimal*;
  it can't distinguish 6 from 3 °C, and a strong upwelling paints the whole reachable area optimal-
  green — the "coldest = best" failure, dressed as "no extrapolation." Fix: add a 4 °C band + a
  warm 18 °C band and extend the laker range so cold water TAPERS (and >16 °C reads out-of-range
  for steelhead — T1a warm end).
- **T1b [integrity] Load the structure bars from `bathy_slope.json` at runtime.** They're hand-
  transcribed into source and already drift (code 0.16 vs measured 0.162); the "reproducible" claim
  is broken. Single source of truth.
- **T1c [presentation] Dim the whole spatial layer when `phase.suppressed`** (peak upwelling cold
  shock) — a display coupling (NOT a fitted weight), so the map stops shouting "prime" when the
  banner says the bite is off.
- **T1d [accuracy] Fall laker depth-gate contradiction.** The fall season badge says "fish shallow
  shoals" while the static `min_depth_m:4` laker gate excludes that water. Make the gate season-
  aware, or suppress the laker spatial layer in fall.
- **T1e [honesty] Correct the provenance ledger + ADR-033.** Reclassify the picked estimator choices
  (relief `radius_m=60`, the p90/p95/p99 percentile CHOICE, the `max(slope,relief)` combine form,
  `min_reach_px`/`MIN_AREA`/sentinel-majority cut) as PICKED-but-bounded; relabel the nearshore
  +2.35 °C delta as a **single-scene** prior (n=3 stations but ONE Landsat pass, 2026-07-28,
  city-arc only, applied region-wide + year-round); soften "two independent axes" (bottom_c is
  largely a depth remap); reclassify per-species depths as T3/T4 judgments.

## TIER 2 — the temporal dimension (biggest value; cheap, honest priors) — ✅ DONE

- **T2a Dawn/dusk low-light window** — ✅ `features/daylight.py` (NOAA/Meeus solar geometry, ZERO
  fetch, deterministic; 7 unit tests). Manifest `light` block → timing strip in the UI. The strongest
  intraday cue for the crepuscular species the thermal model underserves.
- **T2b Barometric pressure level + TREND** — ✅ `ingest/surface_meteo.py` + `features/barometric.py`
  (Open-Meteo `pressure_msl`; 6 unit tests). Labelled directional prior (falling→improving,
  rising→slowing) in the timing strip; NEVER fused into the spatial score (rule 7).
- **T2c Cloud cover** — ✅ `cloud_cover` on the same Open-Meteo call; shown as a `☁ %` context note
  on the barometer line (overcast extends the light window).

## TIER 3 — dynamic plume + run timing (decisive for salmon/steelhead)

- **T3a River discharge → live plume strength.** ✅ `ingest/hydat.py` (realtime GeoMet fetch;
  `hydrometric-realtime`, since daily-mean lags ~7 mo) + `features/river_flow.py` (7 unit tests).
  Each river-mouth marker carries the current discharge + a 3-day trend (rising=freshet=staging
  trigger / steady / falling) in its popup — no fabricated "high/low" percentile (needs a per-gauge
  climatology the system doesn't hold). Gauges: Kam 02AB006, Current 02AB014, Neebing 02AB008.
- **T3b Wire the spawning-run calendar** — ✅ `features/run_calendar.py` (reads the committed
  `events_calendar.yaml`; 7 unit tests). River-mouth markers gold-highlight in-window for the
  selected species, dim off-window; the popup states the active run. "Best spots" surfaces active
  runs for the weak-cue species.
- **T3c Precipitation/freshet trigger** — Open-Meteo `precipitation`; the "first cool rain = starting
  gun" that the calendar already encodes as `rain_trigger`.

## TIER 4 — UI as a real tool (turn the map into an answer)

- **T4a "Top spots today" synthesized list** — ✅ `features/top_spots.py` (6 unit tests). Ranks
  stretches per species by the map's OWN lead-0 weighted habitat area (0–100 relative index), with a
  data-derived reason; weak-cue species carry the run-timing caveat + active runs. Renders as the
  "Best for … today" chip row in the header; clicking a spot flies the map there.
- **T4b Mobile layout** — the two cards squeeze the map into a thin strip; the forecast is the
  smallest thing on a phone (the primary use case). Reclaim map space (collapsible header, etc.).
- **T4c Species-aware station pins** — ✅ station pins (cold-water reachability = a lake-trout
  signal) now recolor to a muted tone + dim to 0.5 for the warm-preferring weak-cue species, and the
  popup says plainly "this is cold-water reachability, a lake-trout signal; for <species> fish the
  river mouths + run windows instead." UI-only, no fabricated data.
- **T4d Wave / safety layer** — ❌ REJECTED on data quality (tested 2026-08-07). No trustworthy
  nearshore wave source is cheaply available: Open-Meteo `marine` returns 0.66–0.74 m for Thunder
  Bay on a light-wind day when the nearest real buoy (NDBC 45027) observes 0.10 m — a ~7× disagreement
  (the marine model is not validated for Lake Superior nearshore), and the offshore NDBC buoys are
  ~30–150 km out with WVHT often `MM`, so they don't represent shore chop. Shipping a safety-critical
  layer from unvalidated data is worse than none (rule 3/8). Moved to the tested-rejected list.

## Explicitly NOT chasing (tested-and-rejected or no cheap source)

FVCOM currents (u/v coherent at only 1 of 3 spots — demoted, DATA_AUDIT.md), net_heat_flux (not
orthogonal), spatial SST fronts (LSOFS r≈0.14, GLSEA coarser), chlorophyll/turbidity (null at the
Thunder Bay nearshore), tributary temperature (not served by ECCC), creel/stocking/forage surveys
(static climatology, not a dynamic input), **nearshore wave/chop (T4d — Open-Meteo marine unvalidated
for Superior, ~7× off the one real buoy obs; offshore buoys too far/missing).** The one true unlock
remains operator field-catch logs.

## Adversarial stress-test campaign — 2026-08-08

Three parallel attack lanes against the finished system (scripts under the session scratchpad;
findings fixed same-day, each with a regression test where testable):

**Lane 1 — pure-function edge attacks.** Solstices/leap-days/polar latitudes, year-boundary run
windows, NaN/negative/degenerate inputs into every math function, all-sentinel isotherm fields.
Result: no HIGH; 2 MED in `river_flow.classify` — NaN discharge classified "steady" with a literal
"nan m³/s" in the UI note, and negative discharge (real ECCC ice/backwater artifact) confidently
classified. FIXED (finite+non-negative intake filter). Cheap hardenings from the LOW list also
applied: barometric short-baseline guard, thermal_suitability optimal-clamped-into-range,
top_spots non-finite/negative area guard. `daylight`, `run_calendar`, `_iso_field`,
`_bottom_temp_field`, `_species_tiers` survived everything thrown at them.

**Lane 2 — failure injection on the build.** Corrupt calib jsons, garbage CSVs, simulated network
failures at every ingest call site, workflow/deps consistency, determinism, gitignore traps.
Result: H2 GLSEA 200-with-junk-body escaped the try and killed the whole build (parse moved inside,
exceptions widened, all three glsea endpoints); H3 the per-station loop was unwrapped so a NONNA
blip AFTER all stretch work discarded the entire build (wrapped, loud skip); M1-M4 loader
type-guards + `_node_columns_in_box` and bias-pipeline call-site wraps (degrade to frozen prior);
M5 stations that produce no forecast now log their absence (rule 5). PASS: nearshore-log QC
bulletproof, workflow deps complete, build deterministic, every runtime-read file committed.

**Lane 3 — UI manifest fuzzing.** All 20 planned mutations degraded gracefully. Three real
findings from the deeper audit: HIGH — empty `stretches` produced a silent blank page (now fails
loudly to the error screen); HIGH — 11 manifest string fields flowed raw into innerHTML (stored-XSS
path once the knowledge pipeline mines external text; all sinks now escaped via `esc()`, verified
by live payload injection); MED — a future-dated manifest read as fresh with negative age (now
⚠ STALE, rule 5). Verified post-fix: payloads inert, empty-manifest error screen shown, zero JS
errors across species/days/popups.
