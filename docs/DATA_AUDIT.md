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

---

# Audit round 2 — squeezing the remaining accuracy, and closing the "is there more data" question

Second pass, after the forecast/GLOS/heartbeat work shipped. Everything below was
**tested against live data in this repo's env**, and the point of several was to
*confirm a negative* so we stop wondering about it. Two wins shipped; the rest are
documented dead-ends so nobody re-litigates them.

## Shipped (measured improvements)

### D. Isotherm from native sigma layers — the free win ✅ shipped
We interpolated LSOFS's **20 native sigma layers** down to 7 fixed depths
`[1,2,4,6,8,10,15]` and *then* found the isotherm — but the isotherm sits at 3–4 m,
right in the 2→4 m sampling gap. Where the thermocline is sharp that misplaces the
line: MacKenzie **3.50 m (7-depth) → 2.82 m (native)**, a 0.68 m local error, zero
cost to remove (same file read). On the GLOS gate the native profile beat the fixed
grid **2.42 m → 2.28 m** (A/B on identical data). Implemented as
`lsofs_extract.extract_native_columns`, wired into the forecast, the dynamic map, and
the isotherm-depth validator. Bronze extraction still uses fixed depths (schema +
buoy-depth matching); the native path is isotherm-only.

### E. NONNA-10 bathymetry validated against a field measurement ✅ confirmed
The whole depth axis rests on CHS NONNA-10, previously unchecked against ground truth.
The seed corpus holds the operator's own count-down-sonar depth at Silver Harbour
("outer rocks ~5–7 m at cast range"). NONNA at 60–80 m from shore there reads **median
7.6 m (p25 6.2)** — a match. MacKenzie's "4.8" reference lines up with NONNA's ~4.6–4.9 m
at cast range too. The bathymetry is trustworthy nearshore where we use it.

## Tested and rejected (so we don't chase them again)

| lever | test | verdict |
|---|---|---|
| **Wind-state-conditioned bias** (fix the upwelling under-shoal) | n=793 cross-year, leave-one-buoy-out: pooled MAE **1.595** vs 2-regime **1.61–1.66** and continuous-regression **1.78**; on the isotherm gate, conditioning was flat-to-worse (2.41–2.60 vs 2.42). In-sample gains vanish under LOBO — classic overfit. | **SKIP.** Pooled +3.3 °C band stays. Residual error is spatial non-locality, not something a wind proxy fixes. Also overturns a hypothesis in BACKTEST_UPWELLING.md: bias is *smallest* during sustained favorable wind, *largest at the transition*. |
| **u,v currents → advection feature** | surface displacement looked huge (≈50 km/5 d at Marina) but that's wind-drift; at the **cold-pool depth** coherence is Silver 0.36 (inertial rotation), Marina 0.56, MacKenzie 0.90. Coherent at 1 of 3 spots. | **SKIP** as a scored feature (fails the generalization/demotion bar — would help at MacKenzie, mislead at Silver). Keep only as an optional diagnostic where subsurface flow is coherent. |
| **net_heat_flux → relaxation timing** | correlates 0.83–0.92 with the temp trend, but it's a *forcing input inside the same FVCOM run* that makes `temp` — no orthogonal skill. | **SKIP** as a feature; fine as a QC diagnostic. |
| **zeta (seiche water level)** | 8.6–9.3 cm peak-to-peak over 120 h — ~10× below the 1 m depth-axis bar; and already fed into `interp_column`. | **SKIP** (no change; already handled). |
| **ww (vertical velocity)** | spatial std across neighbouring elements ≈ the temporal signal at a point. | **SKIP**, confirms the earlier "too noisy at a point" call. |

## The comprehensiveness question: is there any *independent* subsurface source? — tested NO

The subsurface field rests entirely on one model (LSOFS FVCOM). We looked hard for a
second, independent one to cross-check it. **None exists that is live, no-auth, and
independently pullable** — a tested negative, not an assumption:
- **NOAA RTOFS** — Great Lakes are land-masked out (pulled the file: 100 % NaN over the bay).
- **GLERL GLCFS** — LSOFS's own development testbed, same FVCOM lineage (not independent).
- **CoastWatch `LS_fvcom_temp` reanalysis** — real, 20-level, subsurface, CC0 — but ends **2022-12-31**, before our tune window even starts, and is also FVCOM.
- **Copernicus / ECCC-GIOPS / Open-Meteo Marine** — all exclude the freshwater lakes (surface-only at best).
- **ERA5 FLake lake-column** (`lake_bottom_temperature`) — the *one* genuinely independent physics (1-D lake model, ECMWF-forced, not FVCOM). Not exposed by Open-Meteo; needs a raw ECMWF CDS key (not configured here). **The single unclosed lead** — worth a short spike if a CDS key appears, though ERA5's lake mask resolution near Thunder Bay is uncertain.

Verdict: keep validating the *output* against GLOS thermistor **observations** (the
isotherm-depth gate) — that remains the honest cross-check; there is no second model to
consume.

Side finding: `ingest/era5_wind.py` claimed `archive-api.open-meteo.com` was
egress-blocked (403 on 2026-08-04). Retested 2026-08-05: **HTTP 200, real records** —
historical ERA5 wind is reachable again; the stale status note was corrected.

## Bottom line, round 2
The accuracy that was recoverable, we recovered (native-sigma isotherm) and validated
the depth axis against field truth. Everything else worth trying was tested and
honestly rejected — the correction is already near its pooled ceiling, the unused
model variables don't add orthogonal skill, and there is no independent subsurface
model to cross-check against. The remaining error is dominated by one thing a second
dataset can't fix but a **local sensor** would: spatial non-locality. The Bare Point
intake ask stands as the highest-leverage move left.
