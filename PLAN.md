# PLAN.md — phases, gates, kill criteria

## Phase 0 — Hindcast Program (validation BEFORE construction)
**Purpose:** find out where the predictability ceiling actually sits, on archives, before any live alert exists.

Tasks:
1. **Grid bootstrap:** pull LSOFS grid, KDTree nearest nodes for the five stations, persist node IDs + node depths.
2. **LSOFS backfill:** extract temp at station nodes × {2, 6, 10 m} × hourly, Oct 2022 → present, from the historical bucket / NCEI THREDDS. Store parquet.
3. **Reference ingests (historical):** Slate Island buoy archive (NDBC), GLSEA pixels at stations (1995→), ERA5 hourly wind via Open-Meteo historical, HYDAT daily flows (Kam + any Current/Neebing/McIntyre stations).
4. **Event catalog:** detect upwelling events in the LSOFS record (ΔT@6m > 4 °C / 24 h at any north-shore node); build the wind-run ↔ event cross-table from ERA5.
5. **Skill scorecard:** LSOFS vs Slate Island (MAE, bias, by month/depth); event verification (POD, FAR) using GLSEA + buoy as truth proxies.
6. **Threshold tuning:** tune alert thresholds on 2022–2024; validate on 2025–2026 held out.
7. **Synthetic month:** generate ~30 briefs for August 2025 from hindcast data; human face-validity read.
8. **Climatology baselines:** empirical event frequency/magnitude/duration per station-month; seasonal norms from GLSEA anomalies.

**Commissioning gates (provisional numbers; revise only via ADR):**
- G1: LSOFS MAE ≤ 2.0 °C at 6 m, ice-free months, vs best available truth.
- G2: Event detection POD ≥ 0.7 with FAR ≤ 0.3 on held-out years.
- G3: Field-week replay: hindcast reproduces the Aug 2–5 2026 tilt within ~2 °C and ± half a day.
- G4: Alert frequency on held-out years ≈ 2–5 fires/summer (no alarm fatigue).

**Kill/demote rule:** gates fail → temperature layer demoted; system falls back to wind-driven Wedderburn alerts only (smaller, still honest). Document the finding either way.

## Phase 1 — Live core (only after Phase 0 gates)
Actions cron 4×/day: LSOFS + wind ingest → features (T@depth, Wedderburn, wind-run persistence, light windows) → DuckDB. Daily Routine: brief + ntfy + log check + repair PRs. Staleness banner. Canary on URL templates.

## Phase 2 — Fall machine (target: ready by Sept 1)
GeoMet gauge polling + river-phase detector (baseline-relative on the regulated Kam; clean dropping-limb logic on natural streams). Season/regs state machine from `knowledge/`. CSO hygiene flag (rain-24h threshold → 48 h advisory on urban streams). Fishability index (wind × station exposure). MN DNR weekly-report extraction (Haiku) → phenology signals.

## Phase 3 — Scoring & learning (through autumn)
Forecast cube: station × species × date × window; measures = lift vs climatology + raw probability with credible interval + factor receipts. Beta priors from `species_rules.yaml`, Beta-Binomial updates from field-session log, hierarchical shrinkage. Field-session pre-registration + debrief pipeline. Daily static HTML heatmap (single generated file). Quarterly skill review vs climatology; demotion rule enforced.

## Explicitly deferred
ML fish models until ≥ ~300 logged sessions with outcomes (incl. blanks). Castable-sonar ingestion. Genesis Live bathymetry (boat lives elsewhere).

## Standing verification module
Built in Phase 0, runs forever: model-vs-truth MAE tracking (logger, GLSEA, Slate), event contingency tables, brief-vs-outcome Brier/skill scores once sessions accumulate.
