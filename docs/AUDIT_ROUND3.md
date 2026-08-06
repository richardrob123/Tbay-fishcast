# Audit round 3 — full-system: accuracy, robustness, completeness, meaningfulness

**Date:** 2026-08-06. **Scope:** everything (rounds 1–2 predate the map overlays,
ERA5/Landsat/mooring, and the accumulation machinery). Four independent adversarial
auditors (data-path correctness, statistical methodology, pipeline robustness, product
meaningfulness) + verification of every finding against source before action. The
regs/provenance dimension was **deliberately excluded** — the operator is reviewing
regs/access-legality directly.

## Verdict

**The engine's architecture is sound and the data-source layer is complete, but the
audit found (a) the flagship accuracy numbers were contaminated by fit/validate
overlap, (b) four independent fail-silent modes in the pipeline, (c) real bugs in the
deterministic path including two that pushed false alerts, and (d) shipped surfaces
that discarded the uncertainty the engine computes.** All confirmed findings below are
FIXED in this commit except where marked. The honest accuracy statement changes: see
"The honest numbers".

## The honest numbers (what we can actually claim today)

| claim | honest value | source |
|---|---|---|
| Cross-year temporal transfer (corrected MAE) | **3.23 °C** | validate_temporal, fit 2024 → held-out 2025 |
| Cross-fold constant-offset LOO | **1.59 °C** | optimize_correction (cleanest analysis in repo) |
| G1 GLSEA-blindness gate | **FAIL** (honestly reported) | G1_SCORECARD |
| G2 verification study | **INCONCLUSIVE** (0 truth events) | G2_SCORECARD |
| Same-month in-sample ceiling (was sold as "the" accuracy) | 1.09–1.74 °C MAE, POD 0.81/FAR 0.00, 78 % band | ENGINE_VALIDATION — **in-sample, contaminated** |
| Isotherm-depth gate | ~2.4–3.5 m, **n=4, leaky** (fit-window overlap + truth-leak fallback) | now re-accumulating cleanly in gate_log.csv |
| Nearshore anchor error (same-day pairs, n=3) | **+1.7…+2.8 °C** shore-warm | nearshore_anchor.csv (regenerated) |

Re-derivation path: `data/gate_log.csv` + `data/nearshore_anchor.csv` now accumulate
clean observations daily (post-Aug-4 days only can serve as temporal holdout for the
frozen prior); re-score when n is respectable and prefer the frozen-vs-live columns.

## Confirmed findings and their fixes

### CRITICAL — fixed
1. **Heartbeat pushed false alerts twice a day.** The 04:30/10:30 UTC crons ran before
   that day's t12z cycle existed; every lead failed to open; `points == []` became
   `now_reach=False` → "Window closed" pushes from missing data, reversed at 16:30.
   *Fix:* heartbeat resolves the newest existing cycle (walks back ≤3 days); a station
   with no readable leads is **unknown** (state preserved, no transition); total outage
   pushes a data-outage warning, never a verdict.
2. **Fit/validate contamination across the flagship numbers** (rule 6). The 3.31 °C
   pooled bias was fit Aug 1–4 2026 on buoys 45027/45023/45216, then scored on windows
   containing those days at those buoys; decision-skill and band-coverage tables were
   fully in-sample. *Fix:* docs re-labeled (ENGINE/TEMPORAL banners); the honest
   numbers promoted (above); 3.31 formally demoted to a *dated frozen prior*
   (`bias_live.FROZEN_PRIOR`) used only in loud degraded mode; clean accumulation
   redesigned (below) to support future re-derivation.
3. **Pin verdicts used the naive NaN=shore distance the map had to abandon** (fabricated
   coastlines; islands counted as castable shore). *Fix:* one shared
   `reachability.corrected_fields` pipeline (seam fill + imagery shore mask + gap
   bridging + mainland-only distance) now feeds BOTH the map and the pin/heartbeat
   path, with a loud degraded note if imagery is unavailable.
4. **Uncertainty was computed, then stripped from every shipped surface.** The isotherm
   band exists precisely to avoid false precision; production took `["central"]` only;
   the measured boundary-position error (~100–300 m on local slopes) exceeds the 75 m
   cast band. *Fix:* the map now ships three band members per lead
   (certain/central/possible — two-tone green + front bookend ribbon), pins/popups/
   briefs carry certain-vs-uncertain, the legend discloses the position uncertainty,
   cast assumption, depth cap, and that this forecasts **water, not fish** (page
   retitled cold-water forecast).

### HIGH — fixed
5. **Site showed stale data as fresh forever** (age baked at build time). *Fix:* age is
   computed client-side from `issued_utc` every minute; >48 h shows days; degraded
   bias flagged in the banner.
6. **Silent zero-bias fallback**: no buoy matchups → correction silently 0.0 → raw
   warm-biased LSOFS presented as valid. *Fix:* `pooled_or_prior` falls back to the
   frozen prior with `source="frozen-prior"` surfaced in manifest, brief, and banner.
7. **Every accumulation failure was invisible forever** (`continue-on-error` + green
   workflow = no signal, ever; and GitHub disables idle crons at 60 d, so silent
   accumulation death would eventually kill the map too). *Fix:* the workflow's final
   step alerts via ntfy when the build or either accumulator fails.
8. **Gate log punched permanent holes** (contiguity `break` + one-row-per-run cap) and
   **scored a frozen constant while the product ships a live bias**, with a
   **truth-leak fallback** (observed profile anchoring the prediction scored against
   it) that was never recorded. *Fix:* full rewrite — every unlogged day attempted,
   both frozen and live corrections logged with `bias_source`, truth-leak fallback
   removed (`glsea_anchor` column records absence), CSV regenerated.
9. **Anchor log was an invalid calibration pair** — Landsat scene vs GLSEA at *run*
   date, up to 20 days apart (the committed rows proved it: scene 07-28 vs GLSEA
   08-06); newest-scene-only lost passes; a missing GLSEA pair consumed the key
   forever. *Fix:* full rewrite — same-day GLSEA at the scene date, every unlogged
   clear scene logged, unpaired scenes skipped without consuming the key; CSV
   regenerated. Same-day deltas are **larger** (+1.7…+2.8 °C vs +1.5…+1.9 cross-day),
   confirming the contamination mattered. Clear-sky/skin caveats documented in-file.
10. **Rule-7 pre-registration didn't exist in code.** *Fix:* `scripts/log_session.py` —
    `start` freezes the current forecast (verdict/iso/band/bias-source) into
    `data/field_sessions.csv`; `debrief` appends the protocol's ≤2-min fields.
    Blanks are logged.

### MEDIUM — fixed
11. **Commit-back could silently drop the data commit** (`pull --rebase || true`).
    *Fix:* fetch + rebase with abort-and-fail on conflict, commit-ahead verification
    before push; failure now trips the alert step. `cancel-in-progress` also set to
    false so a code push can't kill accumulated rows mid-run.
12. **`isotherm_depth` bottom-equality inversion** — a column whose only 12 °C water is
    the exact bottom sensor returned the *surface* (maximally reachable). Reproduced,
    fixed (both target directions), regression-tested.
13. **Map/pin depth-cap divergence** — pins had NO depth cap while the map capped at
    22 m; plus `target_c`/`cast_m`/cap were hardcoded in 3+ places. *Fix:* single
    source of truth in `stations.yaml product:` (with provenance and tier for each
    number — 12 °C laker niche T3, 75 m operator-measured T1, 22 m judgment T4),
    loaded via `Config.product`, used by map, pins, and briefs; cap now applied in
    `reachability` too.
14. **45216 pinned 2.8 km off** in glos.py vs ndbc.py (2.4 km apart from each other).
    *Fix:* both unified on the NDBC station table's official 46.907, −89.354.
15. **Half-pixel georeference skew** between contour lines, polygons, and the sampled
    iso field (`/(n-1)` centers vs `/n` edges). *Fix:* pixel-center mapping everywhere.
16. **Frames mislabeled as whole days in UTC.** Each frame is one instant; with a ~40 h
    seiche the cold band moves within a day. *Fix:* frame labels are now local-time
    snapshots ("Thu, Aug 7, 8 a.m. snapshot", America/Thunder_Bay), and the wind line
    says what the number is ("% of ens. members").
17. **`member_favorable` bridged data gaps** into fake "sustained" wind runs. *Fix:*
    runs break across gaps >2× nominal spacing; regression-tested. (The upwelling
    fraction is now labeled as ensemble agreement, not "calibrated" probability.)
18. **ERA5-FLake fill values passed as temperatures** (masked→raw ~9.97e36 finite).
    *Fix:* `np.ma.filled(..., nan)`. **Truthy-zero anchor bug** (`if g_sst:` dropped a
    valid 0.0 °C GLSEA anchor): fixed to `is not None` in both paths.
19. **Nothing enforced test hermeticity.** *Fix:* autouse socket-blocking fixture;
    suite passes fully offline (199 tests).

### Documented, NOT fixed (structural/external — decisions or data required)
- **The gate's transfer limit**: only 2 chains exist, both far away (45216 ~170 km,
  LLO1 ~270 km — note BACKTEST_UPWELLING's "~90 km" corrected to ~270 km); a
  site-aware correction cannot survive leave-one-site-out with 2 sites. The log
  supports a pooled-offset check + per-site tracking only. The real unlock stays the
  Bare Point intake (FOI drafted).
- **Band statistics coherence** (median center vs mean±1σ band on ~12 correlated
  samples): the band is now honestly labeled a prior; a principled interval needs the
  accumulated clean data.
- **G2's design** could not produce FAIL as amended (0-event truth ⇒ FAR undefined);
  the pre-registered Wilson interval was never implemented. Next G-study must
  pre-register achievable failure conditions.
- **Season/regs gating of the shipped surfaces** (e.g. laker season closes Sep 30;
  `regs_gate.py` exists but nothing imports it; the map paints unvetted shoreline,
  including the Sturgeon Bay embayment the config itself excludes): **operator is
  handling the regs dimension** — the map now carries a check-the-regs disclaimer, and
  wiring `regs_gate` into build/heartbeat is ready work once the regs pack lands.
- **Depth-zone extrapolation**: the bias is measured at 3–6 m sensors but corrects
  isotherms at 8–14 m (held constant below z_ref). Documented; needs the deeper truth
  (GLOS profiles via the gate log / Bare Point) to constrain.

## Completeness (data sources)
The exhaustive sweep (tested against live endpoints) confirmed **no material source is
missing**: sub-daily geostationary SST does not exist for the Great Lakes (ABI is
ocean-only); everything else is the same sensor stream already ingested
(MODIS/VIIRS/AVHRR→GLSEA; GLOFS *is* LSOFS FVCOM), lake-masked (GLORYS), coarser than
held (CCI LSWT ~1 km/10-day), or carries no water temperature (GeoMet, WU-PWS,
CoCoRaHS, iNaturalist, WSC hydrometric, decommissioned Thunder Bay GS). The stack —
LSOFS + GLSEA + Landsat 100 m + ERA5-FLake + buoys/chains + GLERL mooring prior —
spans every real, live, open source. Remaining upside is private data (Bare Point FOI)
and time (accumulation).

## What "as good as it can get" means now
1. **Short term (done):** surfaces no longer overclaim; failures are loud; the data
   path's known bugs are fixed and regression-tested.
2. **Season term (running):** gate + anchor + field-session logs accumulate clean,
   uncontaminated observations daily; re-derive the corrections against them with real
   holdout when n allows.
3. **External:** send the Bare Point request (docs/BARE_POINT_DATA_REQUEST.md) — the
   single biggest remaining accuracy unlock, and the only in-water nearshore truth.
