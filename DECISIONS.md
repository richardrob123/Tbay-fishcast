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
  where there is no break. All deterministic, all validated pre-rebuild; 296 tests pass.

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
