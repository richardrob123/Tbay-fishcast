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

## TIER 2 — the temporal dimension (biggest value; cheap, honest priors)

- **T2a Dawn/dusk low-light window** — deterministic sun/civil-twilight from lat/lon/date, ZERO
  fetch. A temporal banner + optional map dimming. The strongest cue for the crepuscular species the
  model underserves (salmon/steelhead/coaster). `species_rules.yaml` already encodes the multipliers.
- **T2b Barometric pressure level + TREND** — pre-frontal feed / post-frontal bluebird lockjaw.
  METAR `altim` (at the bay) + Open-Meteo `pressure_msl` (one-line add to the call we already make) +
  NDBC `PTDY`. Ship as a labelled directional prior next to the phase banner (no fitted weight).
- **T2c Cloud cover** — one-line `cloud_cover` on the same Open-Meteo add; modulates the light window
  (overcast extends it).

## TIER 3 — dynamic plume + run timing (decisive for salmon/steelhead)

- **T3a River discharge → live plume strength.** ECCC GeoMet (`api.weather.gc.ca`) is REACHABLE now
  (the `hydat.py` "403-blocked" note is STALE — verified live: Kaministiquia 20.0, N Current 0.084,
  Neebing 0.228 m³/s). Modulate the river-mouth markers by flow/freshet instead of static dots.
- **T3b Wire the spawning-run calendar** (`events_calendar.yaml`, already committed): light up a
  river mouth when its run window is active (chinook/coho/steelhead), off-window when not.
- **T3c Precipitation/freshet trigger** — Open-Meteo `precipitation`; the "first cool rain = starting
  gun" that the calendar already encodes as `rain_trigger`.

## TIER 4 — UI as a real tool (turn the map into an answer)

- **T4a "Top spots today" synthesized list** — per species, ranked, with the reason. The tool
  currently shows a map to EXPLORE (zoom 10 stretches × 4 species by hand); it should hand the
  angler the answer. Highest product-value lever.
- **T4b Mobile layout** — the two cards squeeze the map into a thin strip; the forecast is the
  smallest thing on a phone (the primary use case). Reclaim map space (collapsible header, etc.).
- **T4c Species-aware station pins** — green/blue = cold-water reachability (a laker signal) shown
  unchanged for warm-preferring salmon/steelhead, and not legended.
- **T4d Wave / safety layer** — the product tells humans to stand on exposed Superior points; a
  wave/chop layer (NDBC `WVHT` + Open-Meteo marine, cross-checked) is a safety completion + a
  feeding-trigger note.

## Explicitly NOT chasing (tested-and-rejected or no cheap source)

FVCOM currents (u/v coherent at only 1 of 3 spots — demoted, DATA_AUDIT.md), net_heat_flux (not
orthogonal), spatial SST fronts (LSOFS r≈0.14, GLSEA coarser), chlorophyll/turbidity (null at the
Thunder Bay nearshore), tributary temperature (not served by ECCC), creel/stocking/forage surveys
(static climatology, not a dynamic input). The one true unlock remains operator field-catch logs.
