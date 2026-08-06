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

## Next step (the potential accuracy *gain*, not just QA)

Compare LSOFS-derived mixed-layer/thermocline depth to FLake MLD across the 2024–26
record, buoy-adjudicated: if LSOFS is *systematically* deeper/shallower than two
independent references (FLake + buoys), that is a correctable **depth** bias on top of
the existing +3.3 °C temperature correction — the first thing that could move the
isotherm-depth error below its current ~2.4 m. Until that study is done, FLake is wired
as the independent cross-check; it does not yet change any published number.

Reproduce: `pip install -e ".[era5]"`, put a CDS key in `~/.cdsapirc`, accept the ERA5
licence once, then `python scripts/validate_era5_flake.py`.
