# Offshore mooring — the one observed deep profile (GLERL 2018-2020)

**The gap.** Every offshore subsurface number is *model* (LSOFS FVCOM) or *satellite skin*
(GLSEA/Landsat). ERA5-FLake added an independent *model*. What was still missing: a
multi-year, full-depth, *in-water observed* profile for open Lake Superior to anchor all of
them against.

**Closed (as a prior, not a feed).** NOAA/GLERL moored a thermistor string at
**47.13 N, -86.87 W** (~46 mi N of Rock River, MI), 21 depths from 5.8 m to 202 m, hourly,
**2018-08-30 → 2020-08-12** (NCEI accession 0220860). `ingest/mooring.py` +
`scripts/build_mooring_climatology.py` read it once (offline) and write the compact,
committed `knowledge/mooring_superior_climatology.json`: a **half-month climatology** of the
observed offshore column.

It is offshore and historical, so it never enters the heartbeat. Its role is a **seasonal
prior**: the observed offshore 12 °C-isotherm depth and mixed-layer temperature by time of
year, from two years of real profiles.

## What it shows — a physical, observed thermocline

| period | 12 °C isotherm | surface | mixed-layer T | n (hrs) |
|---|---:|---:|---:|---:|
| early Aug | 7.2 m | 12.4 | 11.5 | 645 |
| late Aug | 12.3 m | 14.9 | 14.2 | 427 |
| early Sep | 14.0 m | 13.6 | 13.4 | 720 |
| late Sep | 18.3 m | 12.5 | 12.4 | 720 |

The offshore thermocline deepens ~7 → 18 m through the stratified season — classic Superior.
Outside it the column is isothermal and cold (no 12 °C crossing → `null`, reported honestly).

## Role
- **Observation-grounded envelope** on where LSOFS's *offshore* thermocline should sit for a
  given time of year — a reality check the FVCOM model never had from live obs.
- **Corroborates ERA5-FLake's** mixed-layer-depth prior with actual multi-year profiles: a
  second independent — and this time *observed* — opinion on offshore stratification.
- Not nearshore: the Thunder Bay embayment tilt still comes from wind physics + LSOFS +
  the Landsat shore anchor. This pins the *offshore* end of that gradient.

Reproduce: `python scripts/build_mooring_climatology.py` (downloads the 2.35 MB NetCDF,
writes the committed JSON). The raw NetCDF is gitignored; the derived climatology is committed.
