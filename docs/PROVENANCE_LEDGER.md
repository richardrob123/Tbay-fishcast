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
| Structure = a real break (slope bar) | `STRUCT_SLOPE_ABS` 0.123 | **DATA** | p90 of pooled \|∇depth\| over reachable water, 9 stretches, n=464k px, computed on depth **smoothed to NONNA's native ~10 m** (removes the 4 m upsampling-step speckle — ADR-036) — `analyze_bathy_slope.py` → `data/calib/bathy_slope.json` |
| Structure = a shoal/point (relief bar) | `STRUCT_RELIEF_ABS` 1.43 m | **DATA** | p90 of pooled local relief, same (smoothed) measurement |
| Glow RAMP band edges (display) | 13 edges, 0.716 (p75) → 3.258 (p99) | **DATA** | p75,77,79,…,95,97,99 of the pooled regional structure-*strength* distribution on the smoothed field (`strength_pcts`, ADR-039/040) — nested cumulative bands; brightness = measured regional rank |
| Break bars (ranking: break / strong / top) | 1.384 / 1.907 / 3.258 | **DATA** | the p90 / p95 / p99 entries of the SAME ladder (`strength_bands`) — "Best spots" boolean intersections use ONLY these; sub-p90 ramp shading never scores |
| Temp-field render resolution | σ = ½ median LSOFS node spacing (≤300 m), per stretch | **DATA-derived scale** | `_iso_field` low-pass: linear griddata over sparse nodes is piecewise-planar; smoothing at the field's own sampling scale removes triangle-facet artifacts (ADR-040) |
| Shore water-level anomaly bar | 3 cm | **PICKED (bounded)** | must clear gauge + wave noise to count as an event; Superior wind setup runs a few cm to ~20 cm (ADR-043). The VALUES (3-min level series, CHS gauges 10050/10047) are measured; the threshold is a stated judgment |
| Upwelling phase corroboration | drawdown / rebound / steady | **DATA** | CHS shore level vs the preceding 30 h — an offshore blow draws the shore level down as the thermocline tilts up. Reported as agreement with the wind-derived phase, never as attribution (the anomaly also carries seiche + barometric pressure) |
| Chart-view isobaths | 2–25 m ladder, 9 levels | **DATA** | contours of the SAME native-smoothed CHS NONNA depth the structure marks use (ADR-041) — one bathymetry, two views; level choice is presentation (nearshore-weighted, index at 10/20 m) |
| "in range" (fair) boundary | `suit > 0` | **DEFINITIONAL** | = the species' preferred **range_c** — the literature band edge, not a picked cutoff |
| "optimal" (good) boundary | `suit ≥ 1` (plateau) | **DEFINITIONAL** | = the species' **optimal_c** core — the literature band |
| Species thermal range / optimal | per species | **LITERATURE** | stations.yaml, tier T2/T3 with citations (Edsall & Cleland 2000; coaster telemetry; GLFC) |
| Per-species min/max hold depth | e.g. laker ≥4 m | **LITERATURE** | telemetry (adult lakers off the summer-daylight flats; coasters <7 m) |
| Bottom-temp isotherm targets | 6–16 °C, 2 °C grid | **LITERATURE** | union of the species band endpoints (`config.band_temps`) |
| Nearshore surface warm-delta | **≈ +0.2 °C (QC'd median, n=24)** | **DATA (multi-year, QC'd)** | Landsat shore − same-day GLSEA over 63 clear L8/9 summer scenes 2019-2025 (`backfill_nearshore_anchor.py`), QC'd to summer + clear + near-station, robust median. SUPERSEDES the old uniform **+2.35** (a single warm-scene artifact that over-warmed the exposed shore ~2 °C — ADR-036). SPATIALLY VARIABLE: exposed points ~0/−, sheltered marina ~+1.6 — exposure-aware delta is a future refinement. |
| Per-species min/max hold depth | e.g. laker ≥4 m | **T3/T4 judgment (not flat LITERATURE)** | round-number behavioral gates; laker 4 m is a map-mover from a *subarctic-lake* telemetry study (transfer caveat), not a Superior measurement |
| Relief neighborhood radius | 60 m | **PICKED (bounded)** | sets the spatial scale of "shoal/point"; self-consistent (live path + calib both use it) but the scale is a judgment |
| Structure percentile choice | p90 bar; p75–p99 display ramp; p90/95/99 ranking | **PICKED (bounded)** | the *values* are measured; *which* percentile means "a real break" (p90) and where context shading begins (p75) are judgments |
| Structure combine form | `max(slope/bar, relief/bar)` | **METHOD (picked, defensible)** | avoids double-counting a spot that is both steep and high-relief — the right form, but a chosen one |
| Speckle/area/sentinel cuts | min_reach_px 4, MIN_AREA 120, sentinel-majority >0.5 | **PICKED (bounded)** | polygon hygiene; small effect on where the edge is drawn |
| Cast reach / max depth | 75 m / 22 m | **DEFINITIONAL** | shore-cast product scope — not a quality judgment |
| How temp × structure combine | **NOT combined** (bivariate display) | **NONE — the combine choice is eliminated** | ADR-038: temperature (teal wash, cited bands, moves with forecast) and structure (gold marks, measured percentiles, static by construction) render in separate visual channels; the conjunction is read by eye. The only ordering ("Best spots") uses boolean set intersections. CAVEAT (unchanged): `bottom_c` is near-planar within a stretch (few LSOFS nodes) — displaying it as its own soft wash is exactly the honest treatment for that. |

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
| ~~Barometric level + trend~~ | — | **DEMOTED / REMOVED (2026-08)** | The direct pressure→salmonid-feeding effect is debated in the literature and mostly an INDIRECT proxy for the frontal weather already read from wind; with no local catch logs it can never be measured here, so it failed the operator's "proven, measurable effect" bar and was pulled from the UI + manifest. Module retained, unused. |
| ~~Cloud cover~~ | — | **DEMOTED / REMOVED (2026-08)** | Was displayed but never actually consumed (decorative). Cut with the barometer. |
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
