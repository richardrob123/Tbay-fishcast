# Near-shore SST — Landsat surface temperature closes the shore blind spot

**The gap.** GLSEA (~1 km), MUR and ERA5 all **land-mask the immediate shore**, so a
station's surface anchor is pulled from a pixel 0.6–1.3 km offshore — the prime suspect
for the shallow-isotherm, low-confidence nearshore cases. We searched exhaustively for an
in-water nearshore feed near Thunder Bay; none exists (no Canadian harbour buoy, and WSC
gauges — including "Lake Superior at Thunder Bay" 02AB018 — report level/discharge only,
no temperature). The one thing that reaches the shore is **satellite thermal**.

## What works: Landsat C2 L2 Surface Temperature (~100 m), no auth

`ingest/landsat_st.py` pulls Landsat 8/9 Collection-2 Level-2 ST (`ST_B10`) via the
Microsoft Planetary Computer STAC + anonymous SAS signing, reads a window around a
station, and returns the nearest **water**-flagged pixel's skin temperature. Thunder Bay
sits in a WRS-2 path overlap, so L8+L9 give a clear-sky attempt every ~3–8 days.

**It retains water pixels to the shoreline** — the thing GLSEA can't do. From the clear
2026-07-28 scene (15.9% cloud), nearest-water skin temp vs the GLSEA offshore anchor:

| station | Landsat ST (nearshore) | GLSEA (offshore) | Δ |
|---|---|---|---|
| Marina | 18.1 °C @ 150 m | 16.6 °C @ 1.3 km | **+1.5** |
| Silver | 19.6 °C @ **0 m** | 17.6 °C @ 0.6 km | **+2.0** |
| MacKenzie | 19.4 °C @ 85 m | 17.8 °C @ 0.6 km | **+1.6** |

The real nearshore water runs **~1.5–2 °C warmer** than the pixel GLSEA is forced to
sample — a measured anchor error at exactly the shallow-isotherm spots. (Scene and GLSEA
day differ ~8 d, so this is direction + rough magnitude, not an exact bias.)

## Limits (honest) and role
- **Skin temp, land-tuned atmospheric correction** — a reasonable nearshore skin value,
  not a water-calibrated bulk temp. Validate against the Bare Point intake when available.
- **~10-day latency, clear-sky only** — a *periodic nearshore anchor*, not a daily feed;
  it in-fills between passes via LSOFS/GLSEA.
- **Role:** (1) calibrate/correct the nearshore surface anchor where GLSEA is land-masked;
  (2) an independent nearshore SST truth to validate the product; (3) sharpen upwelling
  front placement (100 m resolves it).
- **Secondary source:** ECOSTRESS L2T LSTE (~70 m, ISS, includes night passes) via NASA
  Earthdata (free login) — noted for later; not yet wired.

Reproduce: `pip install -e ".[geo]"`, then `python scripts/validate_nearshore_sst.py`.
No key needed (Planetary Computer is anonymous).
