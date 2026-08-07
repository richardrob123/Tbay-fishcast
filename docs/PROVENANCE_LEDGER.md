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
| Nearshore surface warm-delta | +2.35 °C (n=3) | **DATA** | Landsat 30 m shore − GLSEA, `data/nearshore_anchor.csv` (small n, labelled) |
| Cast reach / max depth | 75 m / 22 m | **DEFINITIONAL** | shore-cast product scope — not a quality judgment |
| How temp × structure combine | conjunction | **METHOD** | no fitted weights — there is no catch data to fit them (rule 7); each axis stays separate |

**Every spatial good-vs-bad boundary is DATA, LITERATURE, or DEFINITIONAL. No picked numbers.**
The structure bars/bands are measured from the shore's own bathymetry; the temperature bands are the
cited literature niches; the tier boundaries are those bands themselves.

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
