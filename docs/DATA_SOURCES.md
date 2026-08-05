# Data sources — verified live (2026-08-04/05)

Every source below was hit with a real query from this environment after the egress
allowlist was opened. Hosts, dataset IDs, coverage, and access quirks are recorded so
the next session doesn't re-discover them.

## LSOFS (Lake Superior Operational Forecast System) — the temperature layer

- **Model:** FVCOM 4.4.7; 90,964 nodes, 174,015 elements, 20 sigma layers.
- **Vars:** `temp(time,siglay,node)` °C, `zeta`, `lon`/`lat` (0–360°), `lonc`/`latc`,
  `h` (bathymetry 0.1–379 m), `siglay`/`siglev` (negative, surface→bottom).
- **Buckets (S3, allowlisted by default via `*.amazonaws.com`):**
  - `noaa-ofs-pds` — recent ≤~30 d: `lsofs.YYYYMMDD/lsofs.tHHz.YYYYMMDD.fields.{n,f}NNN.nc`
  - `noaa-nos-ofs-pds` — archive. **Nested** native fields (nowcast+forecast):
    `lsofs/netcdf/YYYY/MM/DD/…` present for **2024/11, 2024/12, 2025, 2026**.
    **Flat** months `lsofs/netcdf/YYYYMM/…` for **2024-03..2024-12** carry BOTH
    `regulargrid` (nowcast+forecast) AND `fields` (nowcast+forecast), but `fields`
    nowcast coverage is day-inconsistent; `regulargrid` nowcast is the reliable,
    complete tune-season source and gives **6 m as an exact z-level**. (Corrected
    2026-08-05: an earlier probe hit an incomplete day and wrongly concluded flat
    months were regulargrid-only.) The two 6 m instruments agree to r=0.99 / RMSE
    0.28 °C (see docs/G2_PREREGISTRATION.md).
- **Earliest data:** 2024-03-26 (regulargrid). Node-`fields` nowcast: ~2024-11 →.
- **Access:** fsspec bulk-fetch (~4–5 s/file) + in-memory netCDF (ADR-017). THREDDS
  OPeNDAP is blocked/irrelevant. Cycles t00/06/12/18z; nowcast n000–n006; fcst f000–f120.

## GLSEA SST — surface truth proxy (GLERL ERDDAP: `apps.glerl.noaa.gov/erddap`)

| Dataset | Coverage | Use |
|---|---|---|
| `GLSEA_ACSPO_GCS` | 2006-12-11 → present (daily) | **Truth** for G1/G2 (2024 tune, 2025–26 validate) |
| `GLSEA_GCS` | 1995-01-01 → 2023-12-31 (daily) | **Climatology** baselines (PLAN task 8) |

- Grid ~0.014° (~1.1 km). **Coastal pixels are land-masked (null)** → sample the
  nearest valid water pixel (we sample at each LSOFS node's coordinate; pixel distance
  recorded). Surface skin SST — carries the 6 m depth caveat (ADR-019).
- Point query: `griddap/GLSEA_ACSPO_GCS.json?sst[(DAYT12:00:00Z)][(latmin):(latmax)][(lonmin):(lonmax)]`.

## ERA5 wind (Open-Meteo archive: `archive-api.open-meteo.com`)

- Hourly `wind_speed_10m` (kn), `wind_direction_10m`, `wind_gusts_10m` at 48.4 N,−89.2 W,
  back to 2022+. Drives the Wedderburn/upwelling cross-table (PLAN task 4).

## ECCC hydrometric — GeoMet (`api.weather.gc.ca`) + HYDAT — 21 stations near Thunder Bay

Verified station IDs (bbox −89.7,48.2 / −89.0,48.7):

| Water | Station IDs |
|---|---|
| Kaministiquia | 02AB001 (near Dona), 02AB003 (Mokomon), 02AB006 (at Kaministiquia), 02AB007 (at Stanley), 02AB010 (Kakabeka Falls PH), 02AB025/026 (West Fort William) |
| McIntyre | 02AB016 (at TBay), 02AB020 (above TBay) |
| Neebing | 02AB008 (near TBay), 02AB024 (near Intola) |
| Current | 02AB014 (North Current), 02AB015/021 (at Stepstone) |
| McVicar | 02AB019 (McVicar Creek at TBay) |
| Other | 02AB018 **LAKE SUPERIOR AT THUNDER BAY** (harbour level? → relevant to the CHS/DFO human task), 02AB022 Corbett, 02AB023 Slate R., 02AB027 Whitefish R., 02AB002/009 Shebandowan |

## NDBC buoys — in-situ water temperature (`www.ndbc.noaa.gov`)

**The Phase-0 breakthrough truth source** (found 2026-08-05 after an earlier wrong
conclusion that no independent subsurface truth existed). NDBC `.ocean` files carry
water temperature at a real thermistor depth (`DEPTH`, `OTMP`). LSOFS is a whole-lake
model, so any Superior buoy is independent truth for its subsurface/upwelling skill.

| buoy | location | water depth | sensor | archive |
|---|---|---|---|---|
| **45027** | 46.860 N, 91.930 W (western Superior, McQuade) | 52 m | 1 m (2024–25), 6 m (2026) | `historical/ocean/45027o<YR>.txt.gz` 2024, 2025 full Jun–Sep; `realtime2/45027.ocean` last ~45 d |
| **45028** | 46.814 N, 91.829 W (western Superior) | 49 m | 1 m | 2024 (to Aug 9), 2025 full |
| 45136 (Slate I., EC) | ~165 km E of TBay | — | 1 m surface | secondary; too distant/surface |

These sit on the "Duluth twin" upwelling shore (seed corpus) and witness upwelling
in situ (45027 2025: 1 m range 7.4–24 °C, max 48 h drop 12 °C — a real thermistor sees
the events GLSEA's smoothed daily composite cannot). Client: `ingest/ndbc.py`.
Used by `scripts/validate_buoy.py` for the real G1/G2 (`docs/BUOY_VALIDATION.md`).

## Reachability note

Only `*.amazonaws.com` (LSOFS S3) is on the default **Trusted** allowlist. The five
hosts above (open-meteo, glerl, ndbc, weather.gc.ca, wateroffice) require a **Custom**
network policy on the cloud environment with "include default package managers" kept on.
