# ERA5 FLake — the independent subsurface cross-check (closes the DATA_AUDIT gap)

**The gap.** `DATA_AUDIT.md` round 2 concluded the subsurface field "rests entirely on
one model (LSOFS FVCOM)" and that no independent, live, open, no-auth model existed to
cross-check it — RTOFS masks the lakes, GLCFS is the same FVCOM lineage, CoastWatch's
FVCOM reanalysis ends 2022. The one unclosed lead was **ERA5's FLake** lake scheme (a 1-D
two-layer lake model, ECMWF-forced, *not* FVCOM), which needs a free ECMWF CDS key.

**Closed.** With a CDS key configured (`~/.cdsapirc`, never committed), `ingest/era5_flake.py`
pulls FLake's `lake_mix_layer_temperature`, `lake_mix_layer_depth`, and
`lake_bottom_temperature` over Lake Superior. FLake resolves the whole grid (45/45 cells
valid — no lake masking). It is genuinely independent physics.

## Is it trustworthy here? — yes (adjudicated against the buoys, same test that killed MUR)

`scripts/validate_era5_flake.py` — a buoy inside FLake's mixed layer should read FLake's
mixed-layer temperature; one below it should read colder. Target 2026-08-01:

| buoy | z | buoy WTMP | FLake mixT | FLake MLD | FLake botT | verdict |
|------|--:|----------:|-----------:|----------:|-----------:|---------|
| 45027 | 6 m | 11.7 | 18.1 | **1.6 m** | 6.7 | below layer → buoy colder ✓ (FLake shows shallow MLD + cold bottom = the upwelling) |
| 45023 | 5 m | 18.5 | 18.5 | 7.4 m | 6.3 | in-layer, **Δ 0.0 °C** ✓ |
| 45216 | 3 m | 18.9 | 16.9 | 3.8 m | 6.6 | in-layer, Δ 2.0 °C ✓ |

**In-mixed-layer FLake-vs-buoy MAE = 0.99 °C → TRUST.** For contrast, MUR SST was ~4 °C
cold-biased in the lake and was rejected. FLake also independently reproduced the shallow
mixed layer + cold bottom at the upwelling buoy 45027 — it gets the *structure*, not just
the number.

## What it can and cannot do

- **Resolution 0.25° (~28 km), latency ~5 days (ERA5T).** So it is a **regional/offshore
  QA cross-check and a seasonal mixed-layer-depth prior — NOT a live nearshore anchor.**
  It cannot resolve the Thunder Bay embayment; the nearshore tilt still comes from the
  wind physics + LSOFS.
- **What it adds:** the first *independent* opinion on where the thermocline sits. This
  lets us (a) flag days/regimes where LSOFS diverges from independent physics (calibrate
  the honest band up on those days), and (b) run a standing check that LSOFS's
  thermocline depth isn't drifting.

## The depth-bias study (done, 13-day window) — real direction, not yet a correction

Compared LSOFS-derived mixed-layer depth (surface − 1 °C threshold) to FLake MLD at the
three subsurface buoys, 2026-07-19→08-01 (39 records):

- **LSOFS deeper than FLake in 92% of records; median +3.2 m** (pooled mean +4.8 m, but
  outlier-inflated; std 5.8 m).
- **Leave-one-buoy-out** still improves the held-out buoy, but weakly — residuals 2–5 m.
- **Buoy adjudication:** where FLake and LSOFS disagree on whether the buoy sits in the
  mixed layer, **stratified** cases favor FLake (LSOFS too deep ✓); **upwelling** cases at
  45027 show LSOFS "MLD" of 14–28 m, which is the surface−1 °C *threshold breaking on a
  cold near-uniform column* — a definition artifact, not a real bias.

**Independent confirmation via the clean gate.** When GLOS came back, the isotherm-DEPTH
gate (product's 12 °C-crossing depth vs observed thermistor profiles — definition-
consistent, no MLD-threshold trap) corroborated it: at **45216 the product isotherm is
11.8 m vs observed 8–9 m — ~3 m too deep**, matching the FLake diagnosis. (AUDIT_ROUND3
caveat: those early gate runs used a fit-overlapping window and a GLSEA fallback that
could leak the truth profile into the prediction; the redesigned `accumulate_gate.py`
logs cleanly — trust the accumulated log over these first spot numbers.) But it is
**site-dependent** (Duluth LLO1 error only 0.3–2.0 m), and GLOS realtime returned only 2
chain-days per site (n=4) — too sparse to fit a robust site/condition-aware correction now.

**Verdict:** a corroborated, real tendency — **LSOFS's thermocline runs too deep in
stratified conditions** (FLake, buoys, AND the observed-profile gate all agree) — but NOT
a clean constant correction: too noisy, site/regime-dependent, and (for the MLD proxy)
confounded by definition. Shipping it needs **more isotherm-depth-gate observations
accumulated over the season** (the standing gate now runs — GLOS is back) then a
LOBO-surviving site-aware fit. Shipping a −3 m shift would
fail the same generalization bar the wind-conditioning idea failed (DATA_AUDIT). The clean
finish is the **isotherm-depth gate against GLOS profiles** (validates the product's actual
12 °C-crossing output with a consistent definition, sidestepping the MLD-definition trap);
that endpoint was down at study time. Until then FLake stays a QA cross-check and this
bias is a documented candidate, not a shipped number. Reproduce the gather logic per
`scripts/validate_era5_flake.py` extended over a window.

Reproduce: `pip install -e ".[era5]"`, put a CDS key in `~/.cdsapirc`, accept the ERA5
licence once, then `python scripts/validate_era5_flake.py`.
