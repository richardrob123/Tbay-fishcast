# Data & method audit — what we use, what we're leaving on the table

Goal: make this real, not a toy. Below is every lever I could find to make the
forecast more accurate/useful, each **tested against a live endpoint**, ranked by
value. "Tested" means I actually pulled data or ran the check in this repo's env,
not that I read a doc.

## What we use today
LSOFS `temp` (nowcast t12z n006 only) · NDBC buoys (surface/single-depth) · GLSEA
1.8 km SST · ERA5 wind (past) · CHS NONNA-10 bathymetry · Esri imagery.

## The three findings that change the product

### A. We are nowcast-only — but LSOFS gives a 5-day forecast. ✅ tested
`lsofs.tHHz.YYYYMMDD.fields.fNNN.nc`, **f000–f120, hourly, 4 cycles/day** (verified by
opening f024/f048/f072/f120). Projecting the isotherm forward at Silver Harbour from
Aug 4 gave an actionable trajectory: cold water at the surface through ~Aug 7, then
**restratifying** (isotherm → 12 m offshore) by Aug 8–9 — i.e. "the window closes ~Aug 7."
Forecast skill vs the 45216/45027 buoys on the Aug 4 upwelling: err **+2.2 °C @48 h,
+4.8 °C @72 h** — real skill on onset, decaying with lead. **This is the single biggest
upgrade: the product goes from "is today good" to "here's the window this week."**

### B. We only read single-depth buoys — full thermistor PROFILES exist. ✅ tested
GLOS ERDDAP (`seagull-erddap.glos.org`) serves complete chains we weren't using:
- **45216 (Ontonagon):** 0,2,3,4,6,8,10,12 m — `obs_577_thermistor_latest` (verified).
- **Duluth LLO1:** 0–43 m, 19 sensors — `obs_42_thermistor_latest`.

This unlocks validating the **isotherm DEPTH** — the actual product output — against a
real observed profile, which we could never do with single-depth data. First result
(45216, Aug 2–4): model isotherm runs **~2.9 m too deep** and under-shoals during
upwelling (observed 10.8→8.6→7.3 m as it built; model held ~11–12 m). That is the
honest decision-variable accuracy — larger than the ±1.4 °C temperature MAE implied,
and it should become a standing validation gate.

### C. Finer + higher-res surface temperature is available. ✅ tested
- **GLERL ACSPO VIIRS 0.8 km** and `GLSEA_ACSPO_GCS` griddap (returned 18.08 °C at
  Silver Harbour) — a drop-in upgrade from our 1.8 km GLSEA.
- **Landsat 8/9 30 m thermal** (`landsat-c2-l2`, asset `lwir11`, Planetary Computer,
  no-auth): 4 clear scenes over Thunder Bay this summer; pulled one and decoded 30 m
  nearshore surface temperature. **Caveat (tested):** its absolute values read ~9 °C
  warmer than GLSEA over water — the ST algorithm is a *land* product, unreliable in
  absolute terms over lakes. Use it for **front geometry** (where the upwelling edge
  is, at 30 m), anchored in absolute terms by GLSEA — not as a standalone thermometer.

## Also worth doing (ranked)

| lever | value | status |
|---|---|---|
| **Ensemble / ECCC-HRDPS wind** for the Wedderburn threshold | high | HRDPS 2.5 km (days 1–2) + `gem_seamless`/ensemble (days 3–5) via Open-Meteo `models=`; report **probability of upwelling**, not a point wind — matches the "calibrated probabilities" mandate |
| **Bare Point WTP intake temperature** — the only true Thunder Bay near-shore subsurface signal | very high **if obtained** | not programmatic; needs a data request to City of Thunder Bay Water. One email could collapse the band. |
| **Unused LSOFS variables** (`u,v` currents, `net_heat_flux`, model wind) | medium | available in-file; use currents for advection/upwelling. `ww` vertical velocity tested — too noisy at a point to use directly. |
| **DRUM / Austin subsurface mooring archive (2005–2020)** | medium | archived (not API); western-arm profiles for subsurface climatology + temporal-split priors |
| **GLERL Lake Superior SST Front-Position product** (`LS_SST_FP_s1`) | medium | independent upwelling-front layer to cross-check the model's isotherm line |
| **ECCC Slate Island buoy (45136)** | low | Canadian real-time but surface-only, 168 km E |

## Methods, not just data
- **Forecast-lead product** (A) with **ensemble-probability** wind (calibrated "chance
  of a reachable window Thu–Sat").
- **Isotherm-depth validation gate** (B) — validate the output, not a proxy; the ~2.9 m
  error is the number to drive down.
- **Landsat front geometry** (C) — TESTED and it does NOT pan out as a quantitative
  gate: on the best clear scene (2026-07-28, path 025/026, 92% bay coverage, QA
  cloud-masked), the LSOFS surface pattern vs Landsat 30 m anomaly correlates at only
  r≈0.14 across 367 nodes. That's a scale mismatch, not a fixable bug — LSOFS (200 m–
  2.5 km) can't resolve the 30 m thermal texture Landsat sees, and Landsat's over-water
  values are uncertain. Verdict: Landsat is a QUALITATIVE front-spotting layer for the
  rare clear scene, not a validation gate. The real product-output validation stays the
  isotherm-depth gate against GLOS profiles (~2.4 m). `scripts/validate_landsat_fronts.py`.
- **No off-the-shelf upwelling index exists** — we compute it (wind stress → Wedderburn,
  or the LSOFS forecast temperature field). Confirmed no product to consume.

## Honest bottom line
The engine is validated; the two things that most limit accuracy are both now
addressable: (1) it's nowcast-only when a 5-day forecast is sitting right there, and
(2) the decision variable (isotherm depth) carries ~3 m error that we can now *measure*
against real profiles and work to reduce. The single highest-leverage external ask is
the Bare Point intake feed — a real thermometer in the water we're forecasting.
