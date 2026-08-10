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

- **ADR-050 — CORRECTION to ADR-049: the failure is local to the validation mooring, not a verdict
  on the product.** Operator challenge, and it was right: "we aren't even getting the direction
  right? Are you sure we don't have a bug?" There is no bug — and the conclusion was still wrong.

  **What a third source says.** GLSEA/ACSPO satellite SST, sampled 0.43 km from the buoy and
  independent of both the mooring and LSOFS, tracks the BUOY:

      date          GLSEA    buoy 1 m    LSOFS top
      2025-07-14    16.59      13.34        8.93
      2025-07-28    21.28      16.82        9.54
      2025-08-12    20.81      17.41        8.60
      2025-09-12    15.08      15.25       15.47

  So the observation is sound, the pipeline is sound (it already agreed with the product's own
  `fields` reader to 0.000 C), and the model is genuinely 11-12 C too cold at that node in
  midsummer.

  **But only at that node.** Same model, same hour, 2025-08-12 12Z: the deep offshore stations
  (45001, 45006, 45004, 45136) show a textbook Superior summer profile — 17.6-21.7 C surface over
  a **3.97-3.99 C hypolimnion**, which is the physical signature of a model getting Lake Superior
  right. Thunder Bay's own node (10050) reads 19.50 C surface / 11.28 C bottom, entirely normal.
  **45027 is the only cold, unstratified station in the lake.**

  **What that means.** ADR-049 measured a LOCAL LSOFS pathology at one mooring on the Minnesota
  upwelling coast. The measurement is real and the statistics stand for that site. The sentence
  "every forecast lead fails the ADR-006 bar" was scoped to the product and should have been
  scoped to the mooring; `thermal_skill.json` now says "at llo1: ..." and carries an explicit
  `scope` field plus the satellite evidence in its caveat. No demotion follows from this.

  **The methodological lesson, which is the durable part.** Every check in ADR-049 was a check for
  a bug in OUR pipeline — reader agreement, time axis, sigma mapping, selection bias, bootstrap
  degeneracy — and all of them passed, which is exactly why the wrong conclusion looked so solid.
  None of them asked the different question: *is the validation SITE representative of what we are
  claiming about?* A single glance at the model's other 18 stations answered it in seconds. A
  validation gate needs a site-validity check as much as it needs a statistics check, and this one
  now has one: the model is cross-checked against independent satellite SST at the validation
  point, and a site where the model contradicts the satellite cannot ground a product verdict.

  **Where this leaves the local question.** Still unanswered, and now with a better route to an
  answer: GLSEA is available historically at ANY location, and LSOFS station 10050 sits 2.3 km off
  the Thunder Bay waterfront. The same machinery can score the model's SURFACE temperature at
  Thunder Bay against satellite over a full season — a real, local, T1 validation the project has
  never had. It does not reach the subsurface profile, which still needs the Bare Point intake
  (task #8). The LLO1 result also leaves a hypothesis worth testing there rather than assuming:
  LSOFS may fail specifically on upwelling-driven shores, and Thunder Bay's north shore is one.

- **ADR-049 — Score the forecast in degrees, hindcast a whole season, and get a real ADR-006 answer:
  every lead loses to the cheap baseline.**
  Signed off after ADR-048 established that the isotherm-depth gate could not measure anything.
  Three changes, then the answer.

  **(1) The metric.** Isotherm depth is derived, censored, and — the part that matters most — its
  UNITS FLOAT: sensitivity is dz = dT/|dT/dz|, so the same 0.5 °C error is 10 cm across a sharp
  thermocline and 30 m in a mixed column. Pooling that across days produces a number whose meaning
  rescales with whatever stratification was present, biased OPTIMISTIC because the days that yield
  a crossing at all are the sharply stratified ones. Scoring TEMPERATURE at the observed sensor
  depths fixes all three: never censored, stable units, ~10 samples per profile instead of ≤1.

  **(2) The data, which turned out to exist all along.** NOAA publishes
  `lsofs.tHHz.YYYYMMDD.stations.forecast.nc` — 10 MB, ~2 s, 19 stations × 20 sigma layers × 1201
  time steps, i.e. the ENTIRE f000–f120 window at 6-minute resolution in ONE file per cycle,
  archived from 2024-12. Station 45027 sits **0.11 km** from the Duluth LLO1 chain and station
  10050 sits 2.3 km off the Thunder Bay waterfront, so the model can be scored where the
  observation is rather than at whatever node `nearest_node` picks. On the observation side, the
  buoy's ARCHIVE dataset (`obs_42`) carries the chain WIDE — `sea_water_temperature_1..N` with
  per-sensor `_depth` and full QARTOD flags, back years — whereas the dataset the product reads
  (`obs_42_thermistor_latest`) is a THREE-DAY ROLLING WINDOW with no flags. That single fact is
  why no hindcast had looked possible and why a stuck thermistor could enter the gate as truth.
  Result: **6,291 paired samples over 118 issue days (2025-05-15 → 2025-09-16)** instead of 5.

  **(3) The statistics, four guards.** Paired samples only. The baseline is the HARDER of OBSERVED
  persistence (not the model's own nowcast held forward — that is model-versus-model) and
  other-year climatology, per sample. The interval is a block bootstrap whose block length is
  MEASURED from the error ACF (1/e at lag 8; 10-day blocks used, 12 blocks, flagged "approximate"
  because a 2.5% tail needs ~40). And a lead is benched only when the interval EXCLUDES 1.0.

  **THE ANSWER, and it is not close.** At every forecast lead the model loses:

      lead    fcst MAE   persist   clim    ratio  95% CI          days forecast better
       24 h    2.86 °C    1.07     2.39    3.67   [2.95, 5.04]     4 / 118   p=5e-29
       48 h    2.79       1.59     2.40    2.48   [1.91, 3.57]    10 / 118   p=6e-22
       72 h    2.79       1.90     2.41    2.13   [1.64, 3.07]    13 / 118   p=5e-19
       96 h    2.82       2.05     2.43    2.08   [1.59, 2.95]    17 / 118   p=1e-15
      120 h    2.88       2.19     2.44    2.05   [1.54, 2.96]    22 / 118   p=3e-12

  The bootstrap and a fully non-parametric day-level sign test — which assumes nothing about
  magnitude or how long the error stays correlated — agree at every lead.

  **The diagnostic that says what kind of failure this is.** Lead 0 (the model's own NOWCAST,
  free in the same file) has MAE **2.90 °C**, indistinguishable from 120 h's 2.88 °C. The error
  does not grow with lead at all, so this is not forecast decay — it is the model's STATE at this
  location. The bias is depth-structured: 5–20 m runs 1.2–1.7 °C too COLD, 30–45 m runs 1.5–1.8 °C
  too WARM. That is a thermocline sitting too shallow and too diffuse, not an offset.

  **Would the product's bias correction rescue it?** Measured, not assumed: reconstructing a
  surface-anchored correction from the log with a PERFECT anchor (the observation itself) and the
  product's tapered shape improves 2.75 → 2.15 °C at a 30 m taper (a uniform offset makes it
  WORSE, 3.54 — the taper is the right family). Still roughly twice persistence at 24 h.

  **Three checks the claim had to survive, all passed.** (a) Am I reading the same model the
  product reads? The station file and the product's own `fields` + `nearest_node` pipeline agree
  to **0.000 °C** at every depth on the same cycle. (b) Is the block length binding? The ACF
  decays properly (1.0 → 0.35 by lag 8), so the 10-day blocks are conservative rather than
  truncated. (c) Is any cell selection-biased? Yes, one: the 1.0 m sensor is admitted only when
  the surface elevation puts the model's top sigma layer above it — ~1% of days — and it was
  reporting a +5 to +6 °C bias on n≈13 that looked like a finding. An explicit
  missing-not-at-random guard drops any (lead, depth) cell present on fewer than half the days.

  **A real bug the tests caught.** With a single bootstrap block every resample is the same
  sample, so the interval collapsed to a point and a point below 1.0 was reported as a PROVEN win
  — false certainty from 500 rows carrying one day of information. Now guarded, and the block
  count and interval quality are published rather than implied.

  **What ships now.** The measurement layer, the 2025 hindcast, and the verdict — plus the
  metres-at-the-point-of-use conversion: each station's trajectory carries `iso_band_m`, computed
  from sigma_T(lead, depth) divided by THAT day's own gradient at the isotherm, withheld entirely
  when the column is mixed or the band exceeds a shore cast's reach. What does NOT ship is a
  decision about the thermal layer itself: benching it is a product judgement the operator has to
  make, and this ADR only establishes the evidence.

  **Standing caveat, stated because no amount of statistics removes it.** All of this is measured
  at LLO1, a nearshore Duluth buoy on an upwelling-dominated coast 271 km from Thunder Bay — very
  possibly LSOFS's worst case, and certainly not a measurement of the Thunder Bay column. It is
  the only subsurface truth within reach of this model; the Bare Point intake (task #8) remains
  the one source that would settle the local question.

- **ADR-048 — The forecast gate was scoring against a censored bound: 30 of 35 rows were not observations.**
  Chasing why `skill_vs_persistence` was still null (only 10 rows carried the persistence column,
  which shipped 2026-08-08 — a benign wait), the analyzer's own `n_effective_chains: 0` pointed at
  something worse. `obs_iso_m` was a single constant per chain: **3.00 m on every Duluth LLO1 row,
  six days running**. Pulling the raw profiles from GLOS ERDDAP settled it — LLO1's entire
  3-38 m column runs **4.0-8.2 °C** in August and never reaches the 12 °C target, so
  `cross_shore.isotherm_depth` fell into its whole-column-colder branch and returned `depths[0]`,
  the shallowest thermistor. That branch is CORRECT for the map ("the cold water reaches the
  surface" is a real statement about upwelling) and a fabrication as a validation reference: it is
  the bound "the isotherm is at or above the top of the chain", and differencing a forecast
  against it measures the distance to the top of the chain rather than to any isotherm.

  **The asymmetry that hid it for a week.** A column too WARM to reach the target already returned
  `None` and was skipped — which is why Ontonagon 45216 (16-19 °C over 2-12 m, also censored)
  contributed almost nothing while LLO1 contributed 30 rows. Only the too-cold direction leaked
  through, and it leaked as a plausible number rather than as an error.

  **What it was feeding.** `pooled_mae_m` = 1.388 m, published by the build into the manifest and
  rendered on the map as "Thermal-field position error ≈ ±1.4 m". Computed almost entirely from
  differencing two censored quantities that both sat near the top of the chain, which is exactly
  why it looked so good. The five genuinely uncensored rows we have — 45216 on 2026-08-04, a real
  interpolated crossing at 8.25 m — show per-lead MAE of **2.74-4.61 m**. The band was not merely
  unvalidated; it was optimistic by roughly a factor of three.

  **Fixes.** (1) `cross_shore.isotherm_crossing()` returns `(depth, status)` with status in
  {crossing, above_top, below_bottom}; `isotherm_depth` is now a thin wrapper so the map's
  behaviour is unchanged and the two can never diverge. (2) Both gates keep only
  `status == "crossing"`, and the log records `obs_status` / `fcst_status` so a row SAYS what it
  is instead of leaving a reader to infer it — model-side censoring is recorded but not dropped,
  since a too-warm model column is a real error. (3) The analyzer quarantines pre-guard rows whose
  observed isotherm is constant ACROSS TWO OR MORE DISTINCT VALID DATES — the pinned-reference
  signature. Across dates, not across rows: one valid date fans out into five lead rows that
  necessarily share an observation, and the first version of the rule duly quarantined 45216 for
  having a single logged day. (4) `pooled_mae_m` and `skill_vs_persistence` are both withheld —
  the band below 20 usable rows, the skill ratio whenever `n_effective_chains == 0`, because a
  ratio under 1.0 against a constant reference is not skill and would read as a passed ADR-006
  demotion bar. Each writes its reason into the file. (5) When nothing is usable the analyzer now
  WRITES the empty result instead of returning early: bailing would have left the previous
  `forecast_lead_error.json` — the fabricated 1.388 m — in place, still labelled "measured".
  (6) The map states the withheld band rather than dropping the tooltip, since silence is
  indistinguishable from a feature that was never built.

  **Standing consequence.** The map currently publishes NO measured ± on thermal-field position,
  which is the honest state. The deeper problem is unresolved and needs a decision: both GLOS
  chains are 180-200 km from Thunder Bay and NEITHER brackets 12 °C in August — LLO1 is far too
  cold, 45216 far too warm — so this gate can only ever validate on the handful of days one of
  them happens to cross. The proposed replacement is to validate TEMPERATURE AT FIXED SENSOR
  DEPTHS rather than the depth of an isotherm: never censored, ~9 samples per chain per day
  instead of 1, and it tests the same model field. That is a design change and awaits sign-off.

- **ADR-047 — River seams drew in the woods: reach width vs local width, and a channel needs banks.**
  Operator, from the live map: "the river seams don't look right." Screenshots showed segments
  sitting 60-110 m off a narrow meandering stream, in the trees and across a dirt road. Two
  independent bugs, both silent, both positional.

  **(1) The wrong width was used for a positional job.** `hrdem.reach_width` is a 1 km running
  median, and its docstring justifies the smoothing correctly — for the SLOPE WINDOW, where a
  width that tracked the local channel would make the classifier's resolution follow the very
  property it is classifying. That reasoning does not transfer to anything positional, and the
  bend-seam bank offset (half a width, to the outer bank) was using it. On the Neebing the local
  measured width was 10 m and the 1 km median was 148 m, because the same window contained a wide
  confluence; on the Current it was Boulevard Lake. The seam drew 74 m out. Fixed by measuring
  both and keeping them apart: `local_width` fills ONLY the sampling stride (a longer run of
  `None` is saturation — no bank found, so no channel — and stays `None`), and it now drives the
  bank offset, the R/w bend criterion and Manning's channel width, while `reach_width` keeps the
  slope window. Because `bend_seams` already skips stations with no width, impoundments and
  drowned mouths suppress themselves rather than needing a special case.

  **(2) A channel has banks, and the width measurement did not require them.** With local width
  wired in, the Neebing still reported 228 m at some stations. The transect test was "everything
  within 0.60 m of the water surface", which is the channel wherever the stream is incised and the
  whole FIELD wherever it is not: at 48.4506,-89.3603 the ground west of a 12 m stream is a plain
  at 280.0-281.0 m for 150 m, so it measured 152 m wide. The fix is the physical definition — the
  wet run must be bounded, on BOTH sides, by ground unambiguously above the water (twice the
  tolerance that defined "wet", not marginally) reached within 16 m, i.e. a bank slope of ~7.5%,
  at or below the gentlest natural bank and far above any floodplain gradient. One side is not
  enough: a stream at the toe of a valley wall has one good bank and one floodplain, and it is the
  floodplain side that runs away. Measured across all five rivers at look distances 6/10/16/24 m,
  the reach MEDIAN is unchanged everywhere while the maximum collapses to a plausible channel
  (Neebing 252 -> 92-106 m, McVicar 196 -> 74-82 m, Current 220 -> 134 m) and the genuinely wide
  Kam barely moves (258 -> 254 m). The choice inside 10-24 m is therefore not load-bearing; what
  is load-bearing is requiring 2x the tolerance rather than 1x, which does nothing at all — the
  first cell outside the run clears 1x by construction.

  **Result and gate.** 388 -> 304 seam features; bends 131 -> 79, with the removals concentrated
  exactly where no channel could be measured (Neebing 47 -> 17, McVicar 18 -> 9) and the Kam's
  genuinely wide reaches retained. Every drawn bend vertex now sits at 1.000 x the local
  half-width. Independent check against the frozen water masks — a different source (OSM water
  polygons) from the lidar widths: of the four bends inside mask coverage, the two whose
  centreline is on mapped water land exactly on the water's edge (0 m); the other two are upstream
  of the coastal mask, where the nearest "water" is the lake. Because nothing about this failure
  ever raised, the build now asserts the invariant where the geometry is created — no bend seam
  may be offset by more than half the measured channel width, and it aborts rather than deploying.
  The check is made against the ORIGINATING station, not the nearest centreline vertex: on a
  narrow river the latter measures the 20 m densification spacing and false-positives at 1.49x.

- **ADR-046 — Coloured shoreline access: Ontario land tenure on the very edge of the coast.**
  Operator asked for a public/private shoreline so the map shows where you may legally stand
  ("green is public and accessible, yellow is public but unknown accessibility, red is private").
  Built as a FROZEN artifact (`web/data/shore_access.geojson`, `scripts/build_shore_access.py`) for
  the same reason as `data/river_geometry.json`: both inputs are static — the water masks are
  already committed and the parcel fabric changes on the timescale of land transactions — so
  rebuilding it four times a day would burn ~3,000 ArcGIS requests to reproduce the same bytes.
  Result: 227.7 km of traced shoreline, 39.2% public / 8.0% unknown / 52.9% private, 146 KB.

  **The trap the whole design is built around.** "Patented land = private" is the obvious rule and
  it is wrong: patented merely means granted out of the Crown, and a city park is patented land.
  That rule marks Marina Park, Silver Harbour and the Mountdale launch RED. `TITLE_HOLDER_TYPE`
  fixes it (of 1,591 patented parcels here, 334 are Municipal / provincial-agency / Federal) — but
  title records who OWNS land, not who may walk on it, and Conservation Authorities hold theirs as
  "Private". So conflicting evidence is DEMOTED to `unknown` rather than resolved: private title
  beside an official provincial Fishing Access Point yields yellow, not red. That single move took
  official access points reading PRIVATE from 8 to 0, and the build now FAILS if that count is
  ever non-zero again.

  **The asymmetry that sets every tie-break:** a false GREEN sends someone onto private property, a
  false RED only keeps them off legal water. Both are errors; the first is worse. Hence (a) First
  Nation reserve land (LIO Indian Reserve, T1) OVERRIDES every other signal — 3.6 km of our traced
  shoreline is Fort William 52, which carries exactly the federal/Crown attributes read as public
  elsewhere, and nothing may promote it; (b) the OSM conservation-area layer is DEMOTION-ONLY —
  it is T3 and CLAUDE.md rule 3 puts access-legality at T1-or-field-verified, so it may withdraw a
  red claim (6.0 km inside Big Trout Bay Nature Reserve went red→yellow) but may never manufacture
  a green one; (c) `Conservation Authority Admin Area` is deliberately NOT used — it is
  jurisdiction, not ownership (its polygons are whole 450 km² watersheds), and using it would paint
  every private cottage lot inside a CA's jurisdiction green.

  **Two fetch bugs that ship as confident wrong colours, not as errors.** `resultOffset` paging
  silently returned a short page on the Crown layer (996 of 1,380 parcels) with no
  `exceededTransferLimit` flag — the missing polygons then read as unclassified, a fetch bug
  wearing a data gap's clothes. Fetch is now id-list-first so completeness is CHECKABLE, and it is
  asserted. Separately, ArcGIS packs exterior and interior rings into one list; treating each ring
  as a solid turned every HOLE into fake coverage, which is how Silver Harbour first came out
  private (it sits in the hole of the surrounding parcel).

  **Geometry.** The coast is traced as the exact lattice boundary of the frozen water mask — every
  4-adjacent water/land pixel pair contributes the unit edge they share — rather than a smoothed
  contour: no interpolation, no marching-squares ambiguity, no new dependency, and each edge
  remembers which side was land so the classification point is genuinely on the bank. The frozen
  set is a tile pyramid (coarse masks sit wholly inside finer ones; the 6 m tiles overlap 13–40%),
  so edges are de-duplicated finest-first — without that, 124 km of coast would be drawn twice, at
  two resolutions, with two independent classifications fighting over the same pixel.

  **An inland probe was built and then deleted, on its own evidence.** Where the bank pixel found
  no tenure record the first version walked up to four mask pixels inland, justified as closing the
  registration gap between a raster waterline and a surveyed parcel fabric. If that were the cause
  it would resolve at ONE pixel; the measured histogram was flat (22/20/20/20 at 1/2/3/4 pixels),
  the signature of walking inland until something is hit. It bought 2.2% of samples by attributing
  parcels that do not front the shore, so it is gone and those samples are `unknown`.

  **What ships, stated in the artifact itself (`meta.means` / `meta.validation`) so no consumer can
  strip it:** green means the land behind the shore is PUBLIC — it does NOT mean the bank is
  walkable or reachable, and accessibility is published in none of these layers. ACCURACY REMAINS
  UNVALIDATED against surveyed points; the ownership is T1 but the three hand-typed ground-truth
  misses each have a public parcel within ~330 m, which is equally consistent with my coordinates
  being wrong. The operator's GPS pins are the real test. Known gap: Silver Harbour Conservation
  Area is not tagged in OSM, so only the frontage covered by the province's own access point is
  demoted. Red/green retained at the operator's explicit request; line STYLE (solid/dashed/dotted)
  carries the same distinction so the layer survives red-green colour vision deficiency, and every
  segment is clickable and states its reason — a colour with no receipt is exactly the kind of
  confident-looking claim this project refuses to make, especially where red means "stay off".

- **ADR-042 — Research agent: observation ledger + run confirmation + pre-registered catch back test.**
  Operator request: a daily agent researching runs/catches, a FULL historical scan of all viable
  sources, and true integration — "not just another 'layer' of reports" — up to back-testing our
  predictions against actual catches. Fits ADR-001's carve-out exactly: the LLM researches and
  extracts; the heartbeat stays deterministic. DESIGN: (1) an append-only OBSERVATION LEDGER
  (`knowledge/observations/observations.jsonl`) of schema-validated claims — catch / run_status /
  stocking / trap_count / derby_result — each with species, date(+precision), gazetteer-resolved
  place_id, verbatim quote, source URL, tier, confidence. Places resolve ONLY against the known
  gazetteer; the agent can never mint new water (Kakabeka lesson), and regs/legality claims are
  rejected outright (rule 4 — never auto-applied). (2) INTEGRATION, structural not decorative:
  run-calendar windows re-FIT from multi-year first/last-report distributions when n suffices
  (`fit_run_windows.py` → data/calib); in-season markers flip to "confirmed active (date, source)"
  from fresh reports with ~7-day decay (`confirmations.json`, consumed by the deterministic build);
  and the ledger is the outcome dataset for the CATCH BACK TEST — protocol PRE-REGISTERED in
  docs/BACKTEST_PROTOCOL.md BEFORE any correlation is computed (rule 7 applied to ourselves):
  retro-scores rebuilt from ARCHIVED layers only (GLSEA, wind phase, flow, calendar, static
  structure — LSOFS has no forecast archive, degradation documented), matched-control design
  against effort confounds, temporal splits (tune ≤2024, validate 2025+), all species reported.
  If the ranking fails the back test, the demotion rule applies to it. (3) OPERATOR DECISIONS
  (2026-08-08): research outputs AUTO-COMMIT (observations and data-fitted calendar updates —
  the operator explicitly chose this over per-batch PRs; code and regs still go through review);
  no raw-report feed in the UI; backfill + daily watcher in parallel. Sources: ToS-respecting
  only (agency/open data, news, public forums, derby pages; no Facebook/auth-walled — rule 12);
  all fetched text treated as hostile (schema gate, no tool-steering, esc() at render).

- **ADR-041 — Chart view + settings sheet (declutter round 2).** User: "I want a toggle to have the
  water swap out with contour lines… we need a settings menu… the UI still too crowded." (1) CHART
  VIEW: a basemap toggle swaps the satellite for a dark nautical-chart ground with ISOBATH polylines
  (2/4/6/8/10/12/15/20/25 m; 10/20 m index-weighted, depth-labelled) generated per stretch from the
  SAME native-smoothed CHS NONNA field the structure marks are computed on — one bathymetry, two
  views, so the chart can never disagree with the gold. Static, emitted once, lazily loaded only when
  toggled. (2) SETTINGS SHEET (⚙ beside Play): basemap toggle + overlay-strength slider, persisted in
  localStorage — operator controls off the main screen on every viewport. (3) DECLUTTER: mobile drops
  the app title and the issue-date prefix (age + Today tick carry it; STALE warnings stay loud on all
  viewports), the "100 = strongest" hint (in how-to-read), and the persistent unverified-survey line
  (moved into how-to-read, still stated). The main screen now carries only fishing information:
  species, best-today, phase, dawn/dusk, day slider, Play.

- **ADR-040 — Visual-truth pass (user review of the live map).** Three user-caught issues, each root-caused:
  (1) *"Still looks stacked"* — 6 ramp bands left visible contour steps; the ladder is now THIRTEEN
  measured percentiles (p75,77,79,…,95,97,99 from `strength_pcts`), each stacked step ~0.05 opacity +
  a tiny hue shift — below the visible-banding threshold, so the gold reads as a continuous gradient
  while every edge stays a measured number. (2) *Angular "CAD-facet" teal* — `griddata(linear)` over
  the sparse LSOFS nodes is piecewise-planar, so temperature-band contours inherited straight
  triangle-facet edges (isolated facets even rendered as detached angular blobs). The model carries no
  information below its node spacing, so `_iso_field` now low-passes the interpolated field at HALF THE
  MEASURED MEDIAN NODE SPACING per stretch (data-derived scale, capped 300 m) — the bands render at the
  field's honest effective resolution, consistent with the stated ~100–300 m edge uncertainty. Facet
  edges were interpolation artifacts, not data; nothing about the underlying field changed. (3) *River
  pins off* — OSM continues rivers as flowlines INSIDE the lake polygon; the "downstream way end"
  tracing put the Kam pin 3.4 km out (by the Mission Is. lagoons) and the floodway pin 1.9 km off. The
  mouth is now the crossing of the waterway centerline with the Lake Superior water-polygon boundary:
  Kam (48.3661, −89.2525), floodway (48.3995, −89.2191); Current R. was already correct. Play button
  restored on mobile (user request); only the overlay-opacity slider stays hidden.

- **ADR-039 — Structure marks as a measured percentile RAMP (g75…g99), not three near-binary steps.**
  User review of the ADR-038 render: "is this truly a heat map at this point? Like we have the 'gold' but
  is it just on or off?" — correct: three steps starting at p90 read as on/off at map scale. The marks are
  now SIX nested CUMULATIVE bands (filled-contour stacking — g99 ⊆ g95 ⊆ … ⊆ g75; disjoint rings were
  tried first and shredded into sliver speckle the audit caught) whose edges are the MEASURED percentiles
  of the pooled regional strength distribution — p75/p80/p85/p90/p95/p99, the `strength_pcts` ladder from
  scripts/analyze_bathy_slope.py (n=463,960 reachable px across 9 surveyed stretches) — rendered faint
  amber rising to bright gold, so brightness IS the break's regional rank. Every band edge is a measured
  number; the only judgments are bounded display choices (ladder floor p75 = "context shading begins";
  the opacity/colour ramp). Everything else is UNCHANGED: marks remain static (no lead property, audit
  hard-fails otherwise), temperature-free, per-species depth-gated; and the RANKING keeps the ADR-029
  "real break" bars exactly (boolean intersections at p90/p95/p99 only — sub-p90 shading never scores),
  so "Best spots today" is identical before and after the ramp. Loader (`_load_struct_calib`) reads the
  ladder with a lockstep-tested fallback. Mobile main screen decluttered in the same pass: Play +
  overlay-opacity controls and the pin/channel key moved off-screen (key folded into the collapsible
  "how to read this") — operator controls are not fishing information.

- **ADR-038 — Bivariate display: temperature and structure in SEPARATE visual channels (supersedes the
  ADR-037 product).** Operator review: multiplying the uncertain thermal curve into the measured structure
  percentile — even weightlessly — "feels arbitrary… doing too much"; show them "separately but at the same
  time." That instinct is standard bivariate cartography, and it is the more honest design: it removes the
  LAST picked method (the combine form) from the display entirely. The render is now two independent layers
  in two channels: the TEMPERATURE WASH (teal; s1 in-range ring / s2 optimal core — the cited bands, moving
  with the forecast because the water does) and the STRUCTURE MARKS (amber/gold; g3/g4/g5 = regional
  p90/p95/p99, emitted ONCE per stretch with NO lead property, so they are static across forecast days BY
  CONSTRUCTION — the self-audit hard-fails if a structure feature ever carries a lead). The conjunction is
  read by eye — a gold mark inside bright teal — with zero fusion math. Structure marks now show for ALL
  species within their depth band (the bottom is a fact; the weak-cue caveat stays on the wash + ranking,
  and the marks fade for plume-followers). The only place needing an ordering — "Best spots today" — uses
  plain BOOLEAN INTERSECTIONS (area of breaks within in-range/optimal water; set logic, no scalar product).
  Side benefits: structure geometry is emitted once instead of six times (smaller geojson), phase-suppression
  dims only the wash (geology doesn't get cold-shocked), and "does the glow move?" is no longer an empirical
  question the audit must watch — it is impossible.

- **ADR-037 — Continuous-conjunction heatmap: glow = thermal_suitability × structure (no fitted weights).**
  After ADR-036 fixed the data bugs, the residual "structure moves between days" traced to a design
  error, not a bug: the render used the LEAST-certain, coarsest signal (temperature — a few LSOFS nodes,
  near-planar per stretch) to draw CRISP per-pixel boundaries (hard fair/optimal/glow tiers). Granting
  the uncertain field that precision is what made a static break blink on/off as the thermal edge swept
  across a near-uniform stretch. Correct fix (end-goal reasoning): a spot's value is the CONJUNCTION of
  right structure AND right temperature, and a conjunction is a PRODUCT — which carries no fitted weights
  (rule 7). So `intensity = thermal_suitability(bottom_c) × structure_strength`, and the glow levels are
  crossings of that continuous product at the SAME data-derived p90/p95/p99 structure percentiles. A real
  break scores its full percentile at optimal temperature and DIMS THROUGH THE LEVELS IN PLACE as its
  water goes marginal — it never relocates and never blinks off at a knife-edge. Structure (precise,
  static, survey-grade NONNA) sets WHERE the bright spots are; temperature (uncertain, cited bands) sets
  HOW BRIGHT today. Every result-driving number has provenance: thermal edges cited (Edsall & Cleland
  2000 / coaster telemetry / GLFC, stations.yaml), structure measured (NONNA) and normalized to regional
  percentiles (bathy_slope.json), combine = product with ZERO fitted coefficients. The faint temperature-
  context wash (s1 in-range / s2 optimal) is kept for cruising/holding water (cited band edges). This
  completes the "continuous heat-map with a floor" direction — the earlier version made only the
  STRUCTURE half continuous; this makes the whole product continuous so nothing flips.

- **ADR-036 — Heat-map data-correctness pass: the shading had four real DATA bugs (operator field review).**
  Zooming the live map on a phone showed the suitability shading was, in the operator's words, "random
  blobs" that bled onto land and "moved between days" — so we stopped and verified every data layer at
  the pixel level (marina + Silver Islet + Little Trout Bay). Four genuine defects, each fixed at the
  ROOT, not painted over:
  (1) **Land/dock/island bleed** — the fill used only `isfinite(depth)` as its water definition, so
  bridged depth + small shore-distance leaked shading onto the beach/breakwall (measured: **7.6 % of
  shaded pixels sat on OSM land** at the marina). Fix: intersect the fill with the authoritative frozen
  OSM water mask (`within &= water_mask`). (2) **"Structure" was mostly grid noise** — CHS NONNA-10's
  10 m soundings are nearest-neighbour upsampled to the ~4 m raster (adjacent-pixel depth diff ≈ 0 within
  a cell, a hard STEP at each cell edge), and `|∇depth|` read those steps as **~140 isolated ≤3-px "strong
  breaks" per stretch** — the blobs. Fix: compute slope/relief on depth **smoothed to native resolution**
  (`suitability.native_smoothed`, Gaussian σ≈½ native cell), which cut Silver Islet strong-break speckle
  213→19 px while keeping 18 real breaks; the p90/p95/p99 glow bands were **recalibrated on the same
  smoothed field** (`bathy_slope.json`: slope bar 0.162→0.123) so provenance stays consistent; `MIN_AREA`
  120→300 m² clears residual fragments. (3) **Nearshore warm-delta was a single-scene artifact AND
  spatially wrong** — the uniform **+2.35 °C** came from ONE warm 2026-07-28 pass. A historical backfill
  of 63 clear Landsat 8/9 summer scenes (2019-2025, `backfill_nearshore_anchor.py`), QC'd to the
  summer-stratified regime + clear + near-station pixels (n=24), shows the true region-wide value is
  **≈ +0.2 °C** — and that it is SPATIALLY VARIABLE: ~0/slightly-negative at the exposed points (Silver
  Harbour −0.65, MacKenzie −0.33) and +1.6 only at the sheltered marina. The old +2.35 over-warmed the
  whole exposed shore by ~2 °C, wrongly pushing the shallow band out of the laker range. Fix:
  `nearshore_surface_delta` now QC-filters the multi-year log and returns the robust **median** (exposure-
  aware delta is a future refinement). (4) **Structure glow "moved" between forecast days** — it was gated
  on the per-lead OPTIMAL-temperature core, so a STATIC break drifted as the coarse thermal field shifted.
  Fix: gate the glow on the STABLE in-RANGE mask (`fair`), so a real break glows in the same place every
  day while its water stays in the species' preferred range; the optimal core is still emphasized (s2)
  where there is no break. (5) **The self-audit (`audit_coast_output.py`, added this pass) then caught
  that the glow STILL drifted — and tracing it found the real root:** `_bottom_temp_field`'s clamp keyed
  off the EXTREME target isotherms (18 °C / 4 °C), but at a cold nearshore stretch the column never
  reaches 18 °C (nor 4 °C absent strong upwelling), so those were sentinel everywhere and the clamp
  silently failed — leaving MOST of the reachable band as NaN. Which isotherms crossed shifted between
  forecast leads, so the NaN COVERAGE swung and the shaded area flickered ~3× day to day (the raw
  bottom temps were stable ~7 °C; only the valid coverage swung — Silver Islet valid-coverage was
  39/10/55% across leads). Fix: clamp to the WARMEST/COLDEST isotherm that ACTUALLY crosses, so the
  whole band gets a stable bottom temperature (valid coverage → 100% at every lead); when water genuinely
  reaches 4 °C that isotherm is used (T1a preserved). Regression-tested (`test_bottom_temp_cold_lake_
  clamps_to_crossing_not_extreme_target`). The RESIDUAL glow movement that remains is now REAL thermal
  (the forecast warming crossing the range edge, amplified by the near-planar few-node bottom_c — the
  documented coupling limit), not an artifact. All deterministic; 297 tests pass. The self-audit is the
  lasting guard so these don't silently return.

- **ADR-035 — The temporal layer: WHEN, added as honestly-labelled priors beside the spatial map (audit T2/T3/T4).**
  The comprehensive live-UI+data+behavior audit found the map answered WHERE but nothing answered
  WHEN-within-a-day, WHEN-in-the-season, or "so where do I actually go" — the drivers that decide
  shore catch on a given morning, and the ones the weak-cue migratory species (salmon/steelhead)
  depend on most. Added four deterministic, no-fitted-weight signals, each presented as TIMING
  CONTEXT beside the map (never multiplied into the spatial score — rule 7, no catch data to fit):
  (1) **Dawn/dusk low-light windows** (`features/daylight.py`) — NOAA/Meeus solar geometry, ZERO
  fetch, exact times; the ±45 min prime-window widths are bounded behavioral picks. The strongest
  intraday cue, decisive for the crepuscular species. (2) **Barometric level+trend + cloud**
  (`ingest/surface_meteo.py` + `features/barometric.py`, Open-Meteo `pressure_msl`/`cloud_cover`) —
  a DIRECTIONAL prior only (falling→improving, rising→post-frontal-bluebird→slowing), WMO-style
  tendency band, labelled T3 folklore-plus-literature, gracefully absent on fetch failure (rule 5).
  (3) **Spawning-run phenology** (`features/run_calendar.py`, reads the committed
  `events_calendar.yaml` that nothing had parsed) — river-mouth markers gold-highlight only inside a
  species' typical run window and read "no run" honestly off-window; `freeze_up` end pinned to
  12-01 (bounded pick). Plus **live river discharge** (T3a; `hydat.py` realtime GeoMet — the
  "403-blocked" note was stale, verified live — + `features/river_flow.py`): each mouth's popup shows
  current flow + a 3-day rising/steady/falling trend (rising=freshet=staging trigger), no fabricated
  high/low percentile (would need a per-gauge climatology). (4) **"Best spots today"** (`features/top_spots.py`) — ranks stretches per
  species by the map's OWN lead-0 weighted habitat area (0–100 relative index, weights an ORDERING
  of the disjoint tiers, not a fit), with a data-derived reason; weak-cue species carry the
  run-timing caveat and their active runs are surfaced. Hands the angler the shortlist instead of a
  ten-stretch × four-species explore task. 26 new unit tests; all signals deterministic (ADR-001).
  Provenance in `PROVENANCE_LEDGER.md` ("temporal layer"). What stays a prior: the magnitude of each
  effect on catch — that is the field-log job (rule 7), by design not yet done.

- **ADR-034 — Consolidate the already-fetched data into live independent cross-checks (+ two honest non-actions).**
  The validation inventory found several sources fetched but under-used. Two are now wired as
  accumulating cross-checks (offline of the 4x heartbeat, like the isotherm gate): (1) the **GLERL
  mooring climatology** — the one committed multi-year in-situ Superior profile, previously read by
  nothing but a unit test — now cross-checks the LSOFS offshore stratification each day
  (check_offshore_climatology.py → data/offshore_check_log.csv); first run corroborated the model
  (offshore mixed-layer 11.42 vs climatology 11.54 °C). (2) A **live over-lake wind gate** — the
  "true over-lake test" ADR-032 deferred — compares the GFS forecast to the REAL observed NDBC buoy
  wind (new stdmet parser) in the upwelling-favorable west quadrant (accumulate_wind_gate.py →
  data/wind_gate_log.csv); first run: ~2.3 kn MAE, GFS over-predicts the W-quadrant ~1 kn. The
  Landsat nearshore delta also now reaches the station pins (ADR — the shared nearshore module).
  **Two things were deliberately NOT done, recorded so they aren't re-attempted:** (a) *tapering the
  nearshore +2.35 °C delta for small n* — the delta is MEASURED and directionally certain (the
  nearshore IS warmer); shrinking it toward zero would re-introduce the exact cold bias it fixes, so
  it stays applied and clearly labelled n=3 rather than under-corrected. (b) *ERA5-FLake as a second
  bias envelope* — deferred: it is redundant with the mooring cross-check for the independent-opinion
  need, and CDS is queued/flaky and needs key management in CI; not worth a fragile dependency now.
  GLOS remains HTTP-000 (depth-gate scorecard route via UMD LLO CSVs is the open path).

- **ADR-033 — Hybrid render: cited discrete TEMPERATURE bands × CONTINUOUS measured-structure glow.**
  The fair/good/prime tiering rested on two unsourced picks (OPTIMAL_SUIT=0.7, IN_RANGE_SUIT=0.15)
  and a binary structure gate (edge > regional p90), which painted 40–48 % of genuinely-rocky
  stretches (Silver Islet, Little Trout Bay) a uniform "prime" — a blob, not the actionable "best
  few spots." Fixed in two moves, keeping the two axes SEPARATE (conjunction, no fitted weights —
  rule 7): (1) TEMPERATURE tiers are now the published bands themselves — fair = inside range_c
  (suit>0), good = the optimal_c plateau — so the boundaries are cited (stations.yaml T2/T3), not
  numbers. (2) STRUCTURE is now CONTINUOUS: within the optimal-temp zone the fill glows teal→green→
  gold by measured break STRENGTH = max(slope/STRUCT_SLOPE_ABS, relief/STRUCT_RELIEF_ABS), with the
  glow-band edges the regional p90/p95/p99 of the pooled strength distribution (data/calib/
  bathy_slope.json "strength_bands" = 1.45/2.06/3.68) — data-derived, not picked. Result verified
  on a live build: "top break" (gold) drops from ~48 % to 0–9 % (only the genuinely-best structure,
  rare), flat featureless water shows 0 % (the floor holds), and rocky stretches now show an
  INTERNAL gradient so the strongest breaks stand out. Considered and rejected a single blended
  heatmap (one colour = temp × structure): blending needs weights we have no catch data to fit, so
  it would re-introduce the arbitrary fusion the project forbids. Also unified all frozen water
  masks (re-froze the 3 stretches that were silently hitting live Overpass each build).

- **ADR-032 — Wind model chosen by test, not by "finer = better" (a null result that prevented a regression).**
  Before switching the upwelling-driving wind from GFS 0.25° to the finer icon_seamless (2.2–7 km),
  scripts/validate_wind_model.py scored each candidate's ARCHIVED FORECAST against ERA5 at the
  over-lake point (35 d), on the metric the product depends on: upwelling-favorable west-quadrant
  wind SPEED. ICON won on OVERALL MAE (2.01 vs 2.14 kn) but carried a ~2.2 kn cold bias in the
  favorable sector (−2.21 vs GFS −0.50) and was slightly worse on favorable-sector MAE — it would
  systematically UNDER-call the west blow, the single worst failure for an upwelling forecast. So
  the default stays `gfs025`, now evidence-backed (data/calib/wind_model_eval.json), and the
  rationale is pinned in wind_forecast.py so it isn't re-litigated on the "finer" intuition. The
  true over-lake test is the NDBC buoy wind (a live wind gate), deferred. This is the discipline
  rules 6/8 buy: a plausible upgrade, tested and refused because the data said it would hurt.

- **ADR-031 — Uncertainty is MEASURED, not fabricated: kill the lead-fade heuristic; weak-cue honesty.**
  Continuing the Round-4 sweep toward a real data-driven product (not a pretty map):
  (1) **Weak-cue species (salmon/steelhead, `temp_cue: weak`) no longer get a confident "prime".**
  The model's own stance is that temperature barely locates these plume/season-driven fish, so the
  UI hides the prime tier + its outline for them and fades the fair/good context to faint, pointing
  the angler at river mouths and warm plumes instead (paintFills/renderLegend, DOM-stub tested).
  Drawing a crisp "prime" where we've said temperature doesn't predict is exactly the false
  precision rule 13 forbids. (2) **The forecast-lead uncertainty is now the MEASURED number, and
  the fabricated one is deleted.** A brief UI experiment faded far-lead thermal zones on a made-up
  `1 − lead/240` decay. `scripts/analyze_forecast_error.py` checked that against the accumulated
  gate (data/forecast_gate_log.csv → data/calib/forecast_lead_error.json): pooled isotherm-depth
  MAE 1.79 m with **NO detectable lead trend** over 24–120 h (OLS slope +0.0007 m/h). So the fade
  was wrong — the LSOFS thermal-field position is about as accurate at day 5 as day 1 — and it is
  removed. The map now states the measured ±1.79 m directly (with its n=25 / 0-moving-obs caveat),
  and lead-dependent TIMING uncertainty stays where it's real: the phase banner. (3) **Frozen
  water-mask lookup is content-addressable** (ADR-020 hardening): the mask key was md5 of the
  mm-rounded WCS-snapped bounds, so sub-metre server drift silently reverted to live Overpass; a
  5 m tolerance content match now resolves the committed mask through that drift. This is the
  discipline the end goal needs: prefer a measured number with an honest small-sample caveat over a
  plausible-looking heuristic, and delete heuristics the data contradicts (rules 5/6/13).

- **ADR-030 — Audit Round 4: data-derive the thresholds, per-species depth, honest degradation.**
  A four-lane audit (docs/AUDIT_ROUND4.md) drove a sweep to replace judgment with evidence.
  (1) **Per-species hold depth** (stations.yaml `min_depth_m`/`max_depth_m`): the shallow/marina
  over-cover was the offshore stratification applied to 2 m water; adult lake trout don't hold
  <4 m in summer daylight (coasters do), so gating `within` per species removed the shallow flats
  from the laker map for a biological reason (marina laker ha 62→13.5), not a temperature guess.
  (2) **Measured nearshore surface delta** (+2.35 °C, Landsat 30 m shore vs GLSEA, n=3, from
  data/nearshore_anchor.csv) added to the surface anchor — GLSEA's ~1 km pixel reads the nearshore
  too cold. (3) **Structure bars DERIVED FROM DATA**, not picked: `analyze_bathy_slope.py` pools
  |grad depth| and local relief over reachable water across all 9 surveyed stretches; the bars are
  the p90 (slope 0.16 rise/run, relief 1.66 m), recorded in data/calib/bathy_slope.json. Added the
  **relief** term so the edge catches shoal-TOPS and point-TIPS (locally shallow but flat) that the
  slope alone missed. (4) **Phase forecast tail** now uses the 13 kn over-lake bar, not the 10 kn
  airport bar (day 0 keeps airport). (5) **Honest scorecard**: skill is pooled over the diagnostic
  (moving-obs) chains only, n_effective printed (currently 1); the "+47%" is one real comparison,
  not a pooled number; the lead-decay table is flagged not-yet-diagnostic. (6) **Per-stretch health**
  (degraded shore, stale/missing anchor) is threaded into the manifest and badged client-side — no
  more silent degradation (rule 5). (7) **Loud frozen-mask miss** so a live-Overpass revert can't
  undo ADR-020 invisibly. GLOS multi-depth profiles (the ideal nearshore-bias input) stay unwired:
  GLOS ERDDAP is HTTP-000 unreachable from this environment — documented, not faked.

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
