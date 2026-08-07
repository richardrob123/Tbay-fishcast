# Provenance ledger — every "good vs bad" judgment, and where its number comes from

The product decides which water is *good* for a species. This is the receipt for **every threshold
that drives that decision** (CLAUDE rule 3 & 10: a score without provenance is a vibe). Each is
classified:

- **DATA** — measured from the system's own data (with the source + n). Reproducible, not picked.
- **LITERATURE** — a published, cited value (fish-thermal / telemetry lit, tier T1–T3).
- **PHYSICS** — grounded in a physical law or the basin's own timescales.
- **DEFINITIONAL** — product scope (the castable window), not a quality judgment.
- **PICKED** — a bounded judgment with no data/physics derivation. These are the ones to watch;
  they are named here so nothing hides.

Regenerate the DATA rows from their scripts; the classification is reviewed whenever a constant moves.

## WHERE the fish are — the spatial map (the core "good vs bad")

| Judgment | Value | Class | Source / basis |
|---|---|---|---|
| Structure = a real break (slope bar) | `STRUCT_SLOPE_ABS` 0.16 | **DATA** | p90 of pooled \|∇depth\| over reachable water, 9 stretches, n=464k px — `analyze_bathy_slope.py` → `data/calib/bathy_slope.json` |
| Structure = a shoal/point (relief bar) | `STRUCT_RELIEF_ABS` 1.55 m | **DATA** | p90 of pooled local relief, same measurement |
| Glow bands (break / strong / top) | 1.45 / 2.06 / 3.68 | **DATA** | p90 / p95 / p99 of the pooled regional structure-*strength* distribution (`strength_bands`) |
| "in range" (fair) boundary | `suit > 0` | **DEFINITIONAL** | = the species' preferred **range_c** — the literature band edge, not a picked cutoff |
| "optimal" (good) boundary | `suit ≥ 1` (plateau) | **DEFINITIONAL** | = the species' **optimal_c** core — the literature band |
| Species thermal range / optimal | per species | **LITERATURE** | stations.yaml, tier T2/T3 with citations (Edsall & Cleland 2000; coaster telemetry; GLFC) |
| Per-species min/max hold depth | e.g. laker ≥4 m | **LITERATURE** | telemetry (adult lakers off the summer-daylight flats; coasters <7 m) |
| Bottom-temp isotherm targets | 6–16 °C, 2 °C grid | **LITERATURE** | union of the species band endpoints (`config.band_temps`) |
| Nearshore surface warm-delta | +2.35 °C | **DATA (single scene) — a prior, not n=3** | Landsat shore − GLSEA, but the 3 rows are 3 stations on ONE pass (2026-07-28, city arc); applied region-wide + every lead + every calendar day. Direction certain; magnitude/spatial-transfer/seasonal-stability are one summer snapshot (audit T1e). |
| Per-species min/max hold depth | e.g. laker ≥4 m | **T3/T4 judgment (not flat LITERATURE)** | round-number behavioral gates; laker 4 m is a map-mover from a *subarctic-lake* telemetry study (transfer caveat), not a Superior measurement |
| Relief neighborhood radius | 60 m | **PICKED (bounded)** | sets the spatial scale of "shoal/point"; self-consistent (live path + calib both use it) but the scale is a judgment |
| Structure percentile choice | p90 bar; p90/95/99 glow | **PICKED (bounded)** | the *values* are measured; *which* percentile means "a real break" is a judgment ("pin at a high percentile") |
| Structure combine form | `max(slope/bar, relief/bar)` | **METHOD (picked, defensible)** | avoids double-counting a spot that is both steep and high-relief — the right form, but a chosen one |
| Speckle/area/sentinel cuts | min_reach_px 4, MIN_AREA 120, sentinel-majority >0.5 | **PICKED (bounded)** | polygon hygiene; small effect on where the edge is drawn |
| Cast reach / max depth | 75 m / 22 m | **DEFINITIONAL** | shore-cast product scope — not a quality judgment |
| How temp × structure combine | conjunction | **METHOD (with a caveat)** | no fitted weights (rule 7). CAVEAT: within an 8.4 km box only a handful of LSOFS nodes feed the isotherm interp, so `bottom_c` is a near-planar remap of the depth raster — "temperature × structure" is largely ONE bathymetry raster read twice + a coarse thermal offset. Drop-offs at the right depth *are* where you fish, but the axes are less independent than ADR-033 implies. |

**The measured VALUES (structure bars/bands, temperature niches, nearshore delta) are DATA or cited
LITERATURE. But the ESTIMATOR CHOICES around them — the relief radius, the percentile that defines
"a break," the combine form, the polygon cuts — are PICKED, bounded judgments, and the nearshore
delta is a single-scene prior, not n=3 independent data.** The earlier flat claim "no picked
numbers" was too strong (audit T1e): the map is *mostly* data-driven with honestly-bounded picks,
not a system with zero judgment. What is genuinely un-picked: the tier boundaries (= the cited
literature bands) and the structure bar/band *values* (reproducible from the pooled distribution).

## WHEN — the upwelling phase / timing banner

| Judgment | Value | Class | Source / basis |
|---|---|---|---|
| Upwelling wind threshold | 13 kn (obs & fcst) | **PHYSICS** | Wedderburn low end (12–17 kt, CLAUDE.md); **data-checked** — the airport-vs-buoy offset is within ~1 kn (`wind_gate_log.csv`), so no separate airport bar (ADR — refuted the old 10 kn guess) |
| Favorable wind sector | 225–315° (W quad) | **PHYSICS** | west-quadrant wind → upwelling on the city/north shore |
| Favorability curve centre/width | s50 13 kn, width 3 | **PHYSICS prior** | Wedderburn; `calibrate_upwelling.py` **refused** the observed fit (non-discriminating), so the prior stands and is labelled prior, not calibration |
| Setup duration | `setup_h` 10 h | **PHYSICS** | documented setup timescale (~10 h, CLAUDE.md) |
| Sustained-blow minimum | `min_run_h` 6 h | **PICKED** | a blow must persist to matter; 6 h is a bounded judgment, not derived |
| Gust-lull tolerance | `GRACE_H` 2 h | **PICKED** | airport gust structure; bounded judgment |
| Peak → relaxation window | `peak_h` 12 h, `relax_end_h` 48 h | **PHYSICS-ish / PICKED** | keyed to the ~40 h basin seiche; 48 h lets the bite window run just past one seiche — the exact edges are judgment |

The phase banner is an **honestly-labelled physics heuristic** — the manifest carries both wind bars
and the UI presents it as timing, not a measured catch signal. `min_run_h`, `GRACE_H`, and the
peak/relax edges are the only **PICKED** numbers left in the whole system; they are bounded by the
basin's physical timescales and cannot be data-driven until upwelling-event *outcome* logs exist
(the offshore buoys couldn't discriminate cooling — `calibrate_upwelling.py`'s null result).

## WHEN-within-the-day + season — the temporal layer (added audit T2/T3)

The map is spatial; these answer *when* to be there. They are shown as **timing context beside** the
map, never multiplied into the spatial score (rule 7 — no fitted weights, no catch data to fit).

| Judgment | Value | Class | Source / basis |
|---|---|---|---|
| Dawn/dusk low-light window | civil twilight (−6°) → sunrise+45m / sunset−45m → dusk | **PHYSICS (astronomy) + PICKED edges** | NOAA/Meeus solar geometry (`features/daylight.py`, deterministic, ZERO fetch) — the *times* are exact; the ±45 min prime-window widths are bounded behavioral picks |
| Low-light matters most for weak-cue species | direction only | **LITERATURE (prior)** | crepuscular feeding; `species_rules.yaml` window_multipliers (dawn 1.6 / dusk 2.0 / day 0.7). Effect size NOT locally calibrated — presented as timing, not a catch factor |
| Barometric level + trend | ±1 hPa/3h steady band; falling→improving, rising→slowing | **LITERATURE/FOLKLORE (prior, T3)** | pre-frontal feed / post-frontal bluebird (`features/barometric.py`, Open-Meteo `pressure_msl`). A DIRECTION only; the ±1 hPa/3h band is a WMO-style tendency cut, not a fitted threshold |
| Cloud cover | raw % | **DATA (context)** | Open-Meteo `cloud_cover`; shown as a modifier note (overcast extends the light window), not scored |
| Spawning-run windows | per calendar entry | **LITERATURE (phenology, T1–T3 per entry)** | `events_calendar.yaml` typical dates (chinook 08-25→09-15, etc.); a mouth lights up only in-window. `freeze_up` end pinned to 12-01 (**PICKED, bounded**) |
| River discharge + trend | live m³/s, ±% / 3d | **DATA (measured) + PICKED trend band** | ECCC GeoMet realtime (`hydat.py`); rising ≥+12%/3d = freshet (staging trigger). The VALUES are measured live; the ±12% steady band is a bounded pick. No "high/low" claim (would need per-gauge climatology) |
| "Best spots today" ranking | index = Σ tier_weight × area | **METHOD (derived, no new judgment)** | ranks stretches by the map's OWN lead-0 tier areas (`features/top_spots.py`); weights escalate s1→s5 (an ORDERING of the disjoint literature/measured tiers, not a fitted weight). Reported as a 0–100 RELATIVE index, never a catch rate |

**These are all PRIORS or deterministic clocks, explicitly labelled as timing** — the honest stance is
identical to the upwelling-phase banner: real drivers, correct direction, but the *magnitude* of the
effect on catch is a behavioral prior awaiting field logs (rule 7), never a locally-fitted weight.

## The honest ceiling (what the map is, and is NOT)

The map says **"conditions here match this species' known thermal + structural preferences."** It
does **not** say "fish were caught here" — because **no catch/field-outcome data exists yet.** So:

- The **inputs** are validated against observations: the LSOFS thermal field (isotherm-depth gate +
  offshore mooring cross-check), the wind (over-lake buoy gate), the bathymetry (survey-grade NONNA).
- The **boundaries** are data-derived or cited (above).
- The **link from "good conditions" to "fish are biting"** is a biological/physical **prior**, not
  locally calibrated — and the system says so (calibrated intervals not certainty; the demotion rule
  benches any layer that can't beat climatology; pre-registration freezes forecasts so a future fit
  is honest). That final calibration is the field-log job (rule 7/9), by design not yet done.

This is the boundary between *data-driven* (everything above the link) and *prior* (the link). It is
named, not hidden — and nothing in the spatial map is a guess.
