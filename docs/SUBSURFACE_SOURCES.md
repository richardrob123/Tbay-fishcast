# Subsurface water-temperature sources — validated (2026-08-07)

Every source below was checked against its **live endpoint** (curl of the real CKAN API /
CSV / NetCDF metadata), not search snippets. Reference: Thunder Bay ≈ 48.4 N, −89.2 W. This
corrects an earlier, more optimistic reading of the same list.

**Bottom line: there is no live, local, depth-resolved Thunder Bay subsurface temperature
feed.** That is the accuracy ceiling, now confirmed by validation, not assumed. The realistic
additions are a live *western-lake* proxy, an offline climatology, and a handful of sparse
local grab samples — all flagged as proxy/historical, never as local current-conditions truth.

## Verdicts

| Source | What it really is | Verdict |
|---|---|---|
| **UMD LLO buoy WT CSVs** (`d.umn.edu/buoys/data/LLO_0X_WT_YYYY.csv`) | Real-time, **10 depths to 38 m**, 10-min, plain CSV, no auth. Live 2026 data confirmed (LLO_03/04). Western MN shore ~200–240 km SW; **summer-only** (deploy ~April, pulled before ice). | **INGEST** — best genuinely-subsurface, live, ingestible source. Use as a lake-wide stratification / upwelling covariate, provenance = "western-lake proxy, not local". |
| **NCEI Accession 0220860** (GLERL central-basin mooring) | One public-domain NetCDF, **21 depths to 198 m**, 2018-08→2020-08, ±0.002 °C. Central basin ~230 km SE. Historical. | **INGEST OFFLINE** — thermocline/stratification climatology + offline validation prior, not the heartbeat. (Likely what the existing `mooring.py` prior draws on.) |
| **Ontario DWSP — Bare Point raw water** | REAL and the only Thunder-Bay-local temperature, but **discrete grab samples (~1–2/yr), no depth field**, and temperature reporting **stopped in 2023-24** (Bare Point had 0 temp rows in 2023-24; residual rows corrupt). Values e.g. 16.6 °C 2018-08-22, 9.4 °C 2020-02-26. Open licence, no FOI. | **HARVEST as sparse historical calibration points only.** Do NOT build anything expecting it to continue. This half-answers task #8: the FOI wouldn't yield a continuous series either — the data is inherently sparse and discontinued. |
| **USGS NWIS 04015380** ("Duluth intake") | Discrete water-quality temperature **1969–1973 only**; no real-time or daily service. Dead ~50 yr. | **SKIP** — the "live ~22 m intake" premise is false. |
| **DRUM LLO archive** (2005-15, 2015-21) | Rich historical depth data (46 deployments, 514 thermistor records) but `conservancy.umn.edu` is WAF/JS-gated (couldn't enumerate); historical + distant. | **DEFER** — only if a longer climatology is wanted; NCEI 0220860 already gives a clean sample. |
| **EPA GLENDA / ECCC Great Lakes WQ** | Offshore ship-survey CTD profiles, 1–2 cruises/yr, US-side/open-lake, gated (CDX login / JS SPA). | **SKIP for pipeline** — offshore, low-cadence, not near Thunder Bay. |
| NDBC 45219/45027/45028 standard feed | UMD-owned but NDBC exposes **surface WTMP only** (0.5–1 m). | Use the UMD CSVs (above), not the NDBC surface feed. |

## Recommendation

1. **Add UMD LLO buoy strings** as a live subsurface *validation/covariate* series (depth-resolved,
   plain CSV). Encode: western-lake proxy, summer-only, per-buoy/year file availability varies.
2. **Add NCEI 0220860** as an offline stratification climatology (one NetCDF, xarray-ready).
3. **Harvest DWSP Bare Point** grabs (2018–2020) as the only local calibration anchors — sparse.
4. The one true local 0–10 m truth remains a **DIY logger string** at real cast spots (operator's call;
   slots into the existing field-session logging).

Gotcha confirmed: "Thunder Bay Buoy" (NDBC/GLERL) = Alpena, **Michigan** — wrong lake. Not used.
