# First-hour verification report

**Date:** 2026-08-04 · **Environment:** Claude Code remote (egress-policy proxy) ·
**Scope:** the six kickoff verifications, run against live sources before building on them.

Verdict first: **the LSOFS temperature layer — the core of Phase 0 (G1/G3) — is real,
reachable, and fast enough to backfill.** The four reference sources (wind, GLSEA,
hydrometric, buoy) are **blocked by this environment's egress policy** and need an
allowlist expansion (admin action) before the full Phase-0 scorecard can run. Two
material findings beyond the checklist: the LSOFS archive starts **2024-03**, not 2022;
and the fast subsetting path is **fsspec bulk-fetch**, not THREDDS OPeNDAP.

---

## 1. LSOFS nowcast file — variable names, sigma convention, node coords, size ✅ CONFIRMED

Inspected a real file (`lsofs.t00z.20260804.fields.n000.nc`, 189 MB) with netCDF4.

| Property | Finding |
|---|---|
| Grid | FVCOM 4.4.7; **90,964 nodes**, 174,015 elements, **20 sigma layers** (21 levels) |
| Temperature | `temp(time, siglay, node)`, units °C; range 3.97–25.96 °C (cold deep / warm shallow — correct August Superior) |
| Sigma convention | `siglay` **negative**, uniform per column: −0.025 (surface) … −0.975 (bottom). Depth of a layer = −siglay × (h + zeta) |
| Node coords | `lon`,`lat` on `node`; element coords `lonc`,`latc`; bathymetry `h` (0.1–379 m). **Longitude is 0–360** (e.g. 270.8 = −89.2°) — must wrap the delta to [−180,180] |
| Time | `time` = "seconds since 2018-01-01"; `Times` char string. n000 of t00z is valid at **cycle − 6 h** |
| Layout (recent bucket `noaa-ofs-pds`) | `lsofs.YYYYMMDD/lsofs.tHHz.YYYYMMDD.fields.{n,f}NNN.nc`; cycles t00/06/12/18z; nowcast n000–n006; forecast f000–f120 |
| Size | **~189 MB/file** → full-file backfill of all cycles is multi-TB (drove the subsetting decision) |

Station nodes resolved by KDTree (nearest node ≥10 m so the 2/6/10 m band resolves, not clamps):

| Station | node | depth | dist |
|---|---|---|---|
| silver_harbour_outer | 29434 | 11.9 m | 1.18 km |
| mackenzie_point | 29446 | 13.8 m | 2.49 km (at LSOFS cell scale — provisional) |
| marina_east_mcvicar | 29138 | 16.3 m | 0.44 km |

## 2. OPeNDAP node subsetting + benchmark ✅ ANSWERED (better path found)

- **CO-OPS THREDDS OPeNDAP is BLOCKED** by egress policy (`opendap.co-ops.nos.noaa.gov` → proxy 403).
- Benchmarked three ways to read station-node temps from S3:

| Method | Cost/file | Notes |
|---|---|---|
| netCDF `#mode=bytes` (HDF5 range) | ~37 s | latency-bound; many tiny GETs |
| **fsspec bulk-cat + in-memory netCDF** | **~5 s** | one bulk GET (~4 s @ ~40 MB/s) + 0.02 s open; **chosen** |
| fsspec + h5py 16 MB-block partial | ~3 s | least bandwidth (~40 MB); optimization tier |

→ Backfill is **feasible**: ~5 s/file × ~28 nowcast files/day × ~600 days ≈ 13 h single-thread, ~1 h at 16× concurrency. Proposed as **ADR-017**. Proven end-to-end: a live mini-backfill (Aug 4 t00z, 7 hours) wrote 63 bronze rows in 31 s with a correct diurnal signal.

## 3. ERA5 hourly wind via Open-Meteo (48.4 N, −89.2 W) ❌ BLOCKED

`archive-api.open-meteo.com` → proxy **403 (egress policy)**. Client written to the real
archive-API shape (`ingest/era5_wind.py`); raises `SourceUnavailable` until allowlisted.
Wind drives the Wedderburn/upwelling cross-table (G2/G4) — **needed for the full scorecard.**

## 4. GLSEA archive depth + pixel access ❌ BLOCKED

GLERL hosts (`coastwatch.glerl.noaa.gov`, `apps.glerl.noaa.gov`) unreachable via proxy.
Could not confirm archive depth (expected 1995→) or pixel method. GLSEA is the **primary
truth proxy** for G1/G2 — but it is **surface SST**, not 6 m (see ADR-019 depth caveat).

## 5. GeoMet hydrometric station on the Kaministiquia (+ Current/Neebing/McIntyre) ❌ BLOCKED

`api.weather.gc.ca` (GeoMet OGC-API) → proxy **403**. Could not enumerate stations or
capture the Kam station ID. This is Phase-2 (river phase), not Phase-0-blocking, but the
verification itself could not run. Client stub in `ingest/hydat.py`.

## 6. Reachability map (the binding constraint)

Tested distinct hosts (discovery, not retrying a denial):

| Host | Result |
|---|---|
| `noaa-ofs-pds.s3.amazonaws.com` | **200 — allowed** (LSOFS recent) |
| `noaa-nos-ofs-pds.s3.amazonaws.com` | **200 — allowed** (LSOFS archive) |
| open-meteo, GLERL, NDBC, weather.gc.ca, wateroffice, THREDDS | **blocked** |

**Only the AWS S3 LSOFS buckets are allowlisted.** Per the proxy README, policy denials
must be reported, not routed around.

---

## Bonus finding — LSOFS archive starts 2024-03, not Oct 2022

Listing both buckets: **no LSOFS before 2024-03-26.** Flat `202403–202412` = `regulargrid`
product; nested `YYYY/MM/DD` (late-2024→) + recent bucket = native `fields`. This breaks
PLAN task 2's "Oct 2022 → present" and ADR-004's "tune 2022–2024" — proposed revision in
**ADR-018** (tune on the 2024 ice-free season, validate 2025–2026 held out).

## What this means for the commissioning gates

| Gate | Status after hour 1 |
|---|---|
| G1 (temp MAE ≤ 2 °C @ 6 m) | Model side ready; **truth side blocked** (GLSEA/buoy). Runs on surface proxy + depth caveat once allowlisted. |
| G2 (event POD/FAR) | Detector built + tested; **needs ERA5 wind + GLSEA** to verify. |
| G3 (field-week replay) | **Feasible now** — Aug 2–5 2026 is in the recent bucket; fixture captures it. |
| G4 (alert cadence) | Needs the tuned thresholds on the (revised) held-out years. |

## Action items

- **Human/admin (blocking the full scorecard):** allowlist `archive-api.open-meteo.com`,
  `coastwatch.glerl.noaa.gov` (+`apps.glerl.noaa.gov`), `www.ndbc.noaa.gov`,
  `api.weather.gc.ca`, `wateroffice.ec.gc.ca` on the environment's network policy.
- **Human (from kickoff):** CHS/DFO harbour water-level ID; LRCA logger permission; ntfy
  topic + Routine API key; verify current model lineup before wiring the Routine.
- **Sign-off:** ADR-017 / ADR-018 / ADR-019.

---

## POSTSCRIPT (2026-08-05) — allowlist opened; all six verifications now pass

The five hosts above were allowlisted. Re-ran the blocked checks against live data:

- **#3 ERA5 wind** ✅ Open-Meteo returns hourly speed(kn)/dir/gust at 48.4 N,−89.2 W.
- **#4 GLSEA** ✅ via GLERL ERDDAP (`apps.glerl.noaa.gov/erddap`): `GLSEA_ACSPO_GCS`
  daily SST **2006→present** (truth for 2024–26), `GLSEA_GCS` **1995→2023** (climatology).
  Coastal pixels are land-masked → sample nearest valid water pixel. Full details and the
  21 GeoMet station IDs (#5) are in `docs/DATA_SOURCES.md`.
- **#5 GeoMet** ✅ 21 hydrometric stations incl. Kam 02AB006/007/025/026, McIntyre
  02AB016/020, Neebing 02AB008/024, Current 02AB014/015/021, McVicar 02AB019, and
  02AB018 "Lake Superior at Thunder Bay" (candidate harbour level → CHS/DFO human task).
- **NDBC 45136** ✅ WTMP 8.8 °C — far colder than the embayment (secondary proxy only).

**First real gate read:** G1 was run for real (LSOFS surface vs GLSEA, July + August
2025) — see `docs/G1_SCORECARD.md`. Headline: G1 **fails** both months (pooled MAE
2.03–2.30 °C), error concentrated at the exposed upwelling nodes; sheltered Marina passes
in August. Cause is real upwelling variability GLSEA smooths (the logger adjudicates,
ADR-019). The 2024 ice-free tune season is `regulargrid`-only (ADR-018) — a separate
reader is the next build for G2/G4 threshold tuning.
