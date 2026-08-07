# DECISIONS.md — Architecture Decision Records (locked; changes need a new ADR + human sign-off)

- **ADR-001 — LLM out of the heartbeat.** Ingest/features/scoring/alerting are deterministic Python on cron. LLM roles: builder, daily brief writer, repair-PR author, research subagents, calibration reviewer. Rationale: reliability, cost, and the toy-test — an LLM vibing about upwelling is worse-calibrated sense-checking.
- **ADR-002 — Two schedules, zero servers.** GitHub Actions cron 4×/day for the deterministic heartbeat (free, reliable); ONE Claude Routine daily for brief + log check + repair PRs. Desktop scheduled tasks rejected (skipped when machine sleeps).
- **ADR-003 — DuckDB + parquet in-repo.** No Postgres, no Airflow, no queues, no dashboards. ~500–800 lines total target.
- **ADR-004 — Temporal splits only.** Tune 2022–2024, validate 2025–2026. No threshold tuning on reported data. Intervals always displayed.
- **ADR-005 — Pre-registration + propensity column.** Forecast frozen at session start; frozen score stored on every session record. Blanks logged (negative class). Prevents selection bias from poisoning future baselines.
- **ADR-006 — Demotion rule.** Any layer failing to beat climatology in quarterly review is benched. The system must be capable of concluding "season + regs + climatology was most of the signal."
- **ADR-007 — Regs are safety-critical.** Tier-1 sources only; `verified_on` dates; brief nags at 12 months stale; gates tested as invariants. System incapable of recommending closed water.
- **ADR-008 — Provenance mandatory.** Every knowledge field: source, retrieval date, tier T1–T4. Contradictions stored side-by-side with tiers, never silently resolved. (Origin: Bare Point, Mission Marsh, Kakabeka, the mill upstream/downstream flip.)
- **ADR-009 — Rules before ML.** Deterministic species rules (temp bands, month matrix, windows, triggers) until ≥ ~300 logged sessions. Solunar: display-only, weight zero.
- **ADR-010 — Two-number output.** Every cube cell: lift vs climatology (decision number) + raw probability with credible interval (expectation number) + factor receipts. A score without receipts is a vibe.
- **ADR-011 — Staleness is loud.** Data-age banner on every brief; stale never presented as current.
- **ADR-012 — Model-per-task policy.** Top model for interactive orchestration; Sonnet-class for defined engineering subagents and the daily Routine; Haiku-class for bulk extraction. Verify current lineup before wiring.
- **ADR-013 — Knowledge versions pinned per forecast.** Every brief/backtest records the knowledge-pack version that produced it.
- **ADR-014 — UTC in storage.** Local time at display only.
- **ADR-015 — Self-repair via PR only.** Scoped `--allowedTools`; human merges. API-key billing for scheduled runs (predictability) — confirm current guidance at docs.
- **ADR-016 — ToS respect.** No Facebook/auth-walled scraping; robots.txt + rate limits everywhere; bait-shop and association intel enters via the human.

## Phase 0 ADRs (accepted 2026-08-04, human sign-off)

These arose from the first-hour verifications (see `docs/FIRST_HOUR_VERIFICATION.md`) and are ACCEPTED deviations from PLAN.

- **ADR-017 — LSOFS ingest via fsspec bulk-fetch + in-memory netCDF, not OPeNDAP/THREDDS.** PLAN implied node extraction over CO-OPS/NCEI THREDDS OPeNDAP. Verified 2026-08-04: THREDDS hosts are blocked by this environment's egress policy, and netCDF's own `#mode=bytes` HDF5 opener costs ~37 s/file (latency-bound range GETs). Chosen mechanism: `fsspec` fetches the file (one bulk GET, ~4–5 s at ~40 MB/s) and it is opened in-memory with `netCDF4` so one reader path serves fixtures and live reads. Achieves PLAN's node-subset goal better. Node-index byte-range is preserved as a fallback. Further optimization if bandwidth becomes binding: fsspec+h5py 16 MB-block partial reads (~3 s, ~40 MB/file) or a kerchunk reference over the identical FVCOM structure.
- **ADR-018 — LSOFS backfill starts 2024-03-26, not Oct 2022; temporal split revised.** The NODD archive has NO LSOFS before 2024-03 (verified by listing both buckets). **Archive layout has three forms (see `docs/DATA_SOURCES.md`):** recent bucket (≤30 d, node `fields`, nowcast); nested `YYYY/MM/DD` native `fields` nowcast present only for **2024-11, 2024-12, 2025, 2026**; and flat `YYYYMM` months **2024-03..2024-12** which are `regulargrid`-dominated (lat/lon grid) with sparse/inconsistent node `fields`/nowcast. Consequence: the node-`fields` nowcast pipeline reliably covers **2024-11 → present**; the **ice-free 2024 tune season (Apr–Oct) is `regulargrid`**, which this node pipeline does NOT read. So ADR-004's "tune 2022–2024, validate 2025–2026" is infeasible twice over. **Revision (supersedes ADR-004 year boundaries for LSOFS): validate on 2025–2026 (fully covered); tuning that needs the 2024 ice-free season requires a separate `regulargrid` (lat/lon) reader — deferred, flagged, NOT silently skipped.** Encoded in `stations.yaml:temporal_split`. The split *principle* (never tune on reported data) is unchanged. G1/G2 tune data is thin — a real constraint on gate confidence, documented not hidden.
- **ADR-019 — Reference ingests are egress-blocked; gates use a surface truth proxy with an explicit depth caveat.** ERA5/Open-Meteo, GLSEA/GLERL, GeoMet, HYDAT, and NDBC are all blocked by this environment's egress policy; only the LSOFS S3 buckets are allowlisted (verified 2026-08-04). Their clients are written to the real API shape and raise `SourceUnavailable` until a host is allowlisted (a change to the environment's network policy — chat approval does not effect it). Until an in-situ 6 m logger exists, G1/G2 are evaluated against **GLSEA surface SST** (primary) and **Slate Island buoy 1 m** (secondary), both SURFACE references vs the gates' 6 m target — every scorecard carries a `depth_caveat` so no result is read as a clean 6 m verification.

## Phase 1 ADRs (accepted 2026-08-07, human sign-off)

- **ADR-020 — Frozen authoritative shoreline (no live Overpass in the data path).** The OSM land/water mask that sets shore-distance was fetched live from Overpass on every build and gitignored; a partial/rate-limited fetch shifted the shoreline and dropped the within-cast cold wedge off narrow points (Silver-tip flicker). Masks are now resolved once and COMMITTED (`data/watermask_frozen/*.npz`); `water_mask()` returns the frozen raster before any network call (live fetch remains an auto-freezing fallback). Deterministic builds, ~3.5× faster. Re-run `scripts/freeze_watermask.py` only when STRETCHES/HALF_M/PX change. (The earlier "per-node GLSEA anchor" idea that also carried this number was checked and DROPPED: GLSEA spans only ~0.3 °C across a box, so a per-node anchor moves the isotherm <0.3 m — see docs/OVERNIGHT_ITERATION.md.)
- **ADR-021 — Forecast-lead verification gate + (deferred) lead-dependent band.** The isotherm-depth gate scored only the nowcast; the product ships a +0…120 h forecast. `accumulate_forecast_gate.py` now scores the FORECAST at each lead (issued L hours before the valid day, anchored to issue-time GLSEA — no truth-leak) into `data/forecast_gate_log.csv`, so the scorecard can report how skill decays with lead. The lead-dependent band-widening it enables is DEFERRED until that log has respectable per-lead n — the widening factor must come from the measured lead error, not a hand-picked coefficient (rules 6/8).
- **ADR-022 — Coverage beyond the continuous city arc: detached SW (Little Trout Bay/Cloud Bay) and NE (Silver Islet/Sleeping Giant) clusters.** Added shore stretches SW toward Little Trout Bay and NE at Silver Islet. These are NOT continuous with the city arc — the shore between (≈48.15–48.25 SW; Black Bay to the NE) is unsurveyed in NONNA, so they render as separate fishable clusters. Centers were geocoded (OSM Nominatim) after a coordinate error put earlier guesses tens of km offshore; each was re-probed for NONNA coverage before inclusion (Little Trout Bay ~14 %, Silver Islet ~70 %). Access/regs for Sleeping Giant PP are the operator's to verify (owner handles regs, per this session). Little Trout Bay's low NONNA coverage is disclosed, not hidden (docs/COVERAGE.md); satellite-derived bathymetry is the only path to fill it further and is left as a future option.
- **ADR-023 — LSOFS file bytes cached in-process (bounded LRU).** The coast build opened each LSOFS lead file once per stretch; caching the fetched bytes by URL (files are immutable once posted) cuts N_stretches×N_leads fetches to one per distinct file — the efficiency lever that makes coverage expansion cheap. Bounded (8-slot LRU) so long backfill day-loops can't OOM.

- **ADR-024 — Species-aware PREFERRED-RANGE map (not "colder = better").** The fish-behavior
  review (docs/FISH_BEHAVIOR_REVIEW.md) showed the single cold-band map conflated *where cold-
  adapted fish live* with *where they're catchable*, and inverted the signal for warmer-preferring
  species. The map is now species-aware: per species a `range_c=[cold,warm]` preferred band (bottom
  temperature within range, reachable within a cast) with the warm-edge **front** drawn as the prime
  mark (fish feed at the thermal edge / on upwelling relaxation, not the coldest trough). Species
  and their ranges live in `stations.yaml species:` (behavioural T3, refine vs GLFC/USGS); the build
  computes each isotherm once and emits one range band per species (temp='sp:<id>'); the UI chips
  swap species and flag temp_cue=weak (salmon/steelhead are plume/season driven). NEXT (not yet
  shipped): an upwelling-PHASE indicator driven by OBSERVED recent winds (setup/peak/relaxing — the
  relaxation phase is the prime bite), and river-mouth structure markers. lake_trout stays the
  default and best-calibrated.

- **ADR-025 — Upwelling-PHASE indicator from OBSERVED wind + river-mouth markers.** The map showed
  *where* cold water is, never *when* in the upwelling cycle we sit — yet the fish review's biggest
  correction is that a fresh upwelling suppresses the bite (cold shock) and the RELAXATION after it
  is the prime window. A new deterministic classifier (`features/upwelling_phase.py`) reads a wind
  series and labels setup / peak / relaxation / neutral from the most recent sustained west-quadrant
  blow (run detection with grace for anemometer flicker; timescales from CLAUDE physics — setup
  ≈10 h, restratification ≈40 h). Day 0 is driven by OBSERVED wind — Thunder Bay airport METAR,
  `ingest/metar.py` (the operator's requirement that the "past couple days" conditioning today be
  actual data, not our own forecast fed back in); forecast leads stitch the observed tail to the
  ensemble control. The manifest carries a per-lead `phase` block; the UI shows it as a banner that
  updates with the day slider. River mouths (Kaministiquia, Current, Neebing–McIntyre) are added as
  structure markers (plume/forage rival temperature for shore catch, decisive for the weak-cue
  species), dimmed when the selected species doesn't stage there. Airport wind under-reads over-lake
  speed, so the observed threshold sits at ~10 kt (documented, tier T4).

- **ADR-026 — Graded per-species suitability (one variable, literature curve) + CONTINUOUS
  upwelling response — and an explicit refusal to fuse signals without data.** Two changes and one
  deliberate non-change. (1) The flat in/out range fill is replaced by a GRADED thermal-suitability
  field: the isotherm-depth stack is inverted to a bottom-temperature field, then graded through
  each species' published preference curve (`features/suitability.thermal_suitability`) — 1.0 across
  the optimal core, tapering to 0 at the range edges — and emitted as three nested contours (s1
  total range → s3 optimal core) so the sweet spot reads inside the habitable band. `optimal_c` per
  species added to `stations.yaml` (tier T3). (2) The binary "≥13 kt sustained or nothing" upwelling
  wind readout (which collapsed persistent moderate west wind to a bare, misleading 0 %) is replaced
  by a CONTINUOUS favorability (`suitability.upwelling_favorability`, logistic across the Wedderburn
  range) surfaced as `ensemble_favorability`; a 0 reading now always carries its context (peak
  favorable wind). We attempted to CALIBRATE that response to observed data (`calibrate_upwelling.py`
  fits P(surface cooling | favorable wind) from NDBC buoy history) — and it FAILED to discriminate
  (offshore western-Superior buoys don't show the coastal-upwelling signal; AUC≈0.5, corr slightly
  negative), so the curve stays a labelled physics prior and the null result is recorded to
  `data/calib/upwelling_favorability.json`, not hidden. (3) The NON-change: we deliberately do NOT
  fuse temperature × phase × front × structure into a single "probability" with hand-picked weights.
  Without catch/field-session outcomes there is nothing to fit those weights against, so a composite
  would be exactly the guessing the operator (and CLAUDE rules 6–8) forbid. Each signal is shown
  distinctly and honestly; the weighting is deferred to when the pre-registered field logs let it be
  fit and temporal-split validated.

- **ADR-027 — "Where the fish are" = thermal niche ∩ thermal front, combined by CONJUNCTION (no
  fitted weights).** The operator wanted the zones actually combined into one "where the fish are"
  ranking, as accurately as possible, but still without guessing. Resolution: fish LOCATION (not
  catch) is driven by two signals we can supply from data, and they combine without inventing
  relative weights by using a conjunction (a resource-selection form) rather than a weighted sum.
  (1) thermal niche — `suitability.thermal_suitability`, published preference curves. (2) thermal
  front / edge / drop-off — `suitability.thermal_front_gradient`, the spatial gradient of the
  modelled bottom-temperature field itself (data-derived; fish select thermal edges and structure,
  telemetry). The overlay emits three ranked tiers per species: **fair** (in the preferred range),
  **good** (optimal-temperature core, OR in range AND on a strong edge), **prime** (optimal core
  AND on a strong edge — hold + feed). The "strong edge" threshold SELF-CALIBRATES to each scene
  (top-tercile gradient present, floored so flat water can't fake a front) — no picked magnitude.
  The upwelling PHASE stays a SEPARATE timing layer (banner), not multiplied into the spatial field,
  because its magnitude effect on the bite is a catch-outcome question with no data yet. Honest
  ceiling, stated in the UI: this is a data-driven RANKING, not a catch percentage — the two inputs
  are data and the combination needs no weights, but the functional form (conjunction) and the
  strength of edge-selection are literature-direction-certain, not yet fit to Thunder Bay fish data.
  That final calibration is the field-log job (demotion rule as backstop).

- **ADR-029 — "Prime" must be ABSOLUTE, not best-of-scene; temperature stays primary.** Field
  review of the live map (operator) surfaced two flaws. (1) The edge threshold that defined prime
  was a per-scene PERCENTILE (top ~third of each stretch's gradient/slope), so ~a third of every
  stretch was always flagged prime regardless of whether real structure existed — "best available
  today", not "truly good". Replaced with an ABSOLUTE physical bar from the measured NONNA slope
  distribution: a real drop-off/break is >=0.16 rise/run (the p90 of the pooled regional slope; scripts/analyze_bathy_slope.py, data/calib/bathy_slope.json). Now a flat,
  featureless stretch yields little or no prime; prime concentrates on genuine breaks (city arc
  prime fell to ~0-9% of shaded, staying high only where the geography really is steep-and-cold —
  Silver Islet, and the low-confidence Little Trout Bay). (2) The modelled thermal-FRONT gradient
  was DROPPED from the spatial edge: LSOFS is too coarse to resolve real fronts (Landsat couldn't
  validate it, ADR-028) and it's largely redundant with depth structure, so it was over-calling
  prime. The edge is now the directly-measured bottom structure only; the thermal/relaxation aspect
  lives in the temperature grading and the phase banner. Tiers simplified and made temperature-first:
  fair = in the preferred range (faint context wash), good = the OPTIMAL-temperature core, prime =
  optimal temp AND a real measured drop-off. Fill opacities re-weighted so fair recedes (0.13) and
  good/prime carry the signal. NOTE (honest limit, unchanged): in shallow enclosed basins (e.g. the
  marina) LSOFS offshore-node profiles interpolate cold into shallow water, so "good/fair" can
  over-cover there — a nearshore-temperature accuracy limit (ADR-019/028), not a tiering flaw.

- **ADR-028 — Push accuracy to the no-logs limit: bathymetric structure, thermal-band provenance,
  seasonal regime, and an honest Landsat-front finding.** Asked to push accuracy as far as possible
  without field logs, we did four things. (1) **Bathymetric structure** added to the ranking:
  `suitability.bathymetric_structure` = the slope of the CHS NONNA soundings (drop-offs / breaks /
  shoal edges), DIRECTLY MEASURED so higher-confidence than the modelled thermal front. The "edge"
  in the fair/good/prime conjunction is now `thermal-front OR bathymetric-structure` — fish relate
  to both, either one with optimal temperature makes a spot prime. Same self-calibrating threshold,
  no fitted weights. (2) **Thermal-band provenance** upgraded T3→T2 (a background research pass
  confirmed every `range_c`/`optimal_c` is defensible against the primary literature — no numeric
  change needed); fixed a real mis-citation (the 10.1/12.5 °C age-0 figures are **Edsall & Cleland
  2000**, not "McCauley & Tait"), added the caveat that adult 6–9.5 °C occupancy comes from a
  subarctic-lake telemetry study (not Superior), and flagged that coaster brook-trout `optimal_c` is
  a deliberately cold REALIZED band, not the physiological optimum. Not T1: the primary PDFs are
  egress-blocked, so values came from abstracts/records — true T1 waits on pulling them to
  `knowledge/`. (3) **Seasonal regime** (`features/season.py`): a context badge that states the map
  is a SUMMER thermal/edge model and how the driver shifts (spring = plumes/structure, upwelling
  matters least; fall = shoal staging), direction-only from the review, no fabricated spatial
  params. (4) **Landsat front-validation — honest null.** Landsat C2-L2 surface temperature reads
  ~20–42 °C over a ~5 °C June lake; its over-water absolute values are too unreliable to co-locate
  against cold offshore nodes (confirms the script's own caveat). So the computed fronts are NOT
  validated by Landsat here; the model's horizontal structure rests on the in-situ isotherm-depth /
  forecast-lead gates instead, and the newly-added bathymetric edge is directly measured. Remaining
  no-logs ceiling: the nearshore SUBSURFACE temperature bias (one offshore-buoy scalar + GLSEA
  surface anchor; Landsat too sparse to fold in) and the cross-signal weighting (needs catch logs).
