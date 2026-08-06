# Whole-coast forecast map (hosted, phone-friendly)

A zoomable map of the Thunder Bay shore showing **reachable cold water** (green) and
the **12 °C line** (red), scrubbable across the nowcast + 5-day forecast — opened as a
URL on a phone, no local run. Lives in `web/`, deploys to GitHub Pages.

## What it shows
- **Live satellite basemap** (Esri World Imagery) with pan/zoom/pinch.
- **Cold-water + isotherm overlays** for four overlapping stretches spanning the arc
  from the Kaministiquia mouth to MacKenzie Point / Silver Harbour, precomputed from
  CHS NONNA-10 bathymetry × the LSOFS isotherm field (same engine + native-sigma
  correction as the per-spot product). NONNA leaves unsurveyed water as no-data exactly
  like land, so the shore is defined against the **Esri basemap as land/water ground
  truth** (dark, lake-connected no-data = water; bright = land) — otherwise the
  unsurveyed nearshore apron reads as coast and casts "reachable" water far offshore.
- **Forecast slider** (+ Play) stepping the whole coast through the 5-day forecast.
- **Station pins** with today's verdict, the isotherm-depth trajectory, and surface SST.
- **Upwelling-wind probability** per day (ensemble) and a **data-age / STALE** banner.
- Total reachable hectares per forecast day.

## Architecture
`web/index.html` is a static MapLibre app (library vendored in `web/vendor/`, no CDN
dependency). It reads `web/data/manifest.json` — stretch metadata, station verdicts,
wind probabilities, data age — plus one GeoJSON per stretch for the reachable-cold
**area** (`data/areas/`) and one for the 12 °C **front** (`data/lines/`). BOTH overlays
are vector (fill+outline polygons and polylines, tagged by forecast lead), so they
stay crisp at any zoom instead of pixelating like a raster overlay. The area polygons
are `rasterio`-polygonized then `shapely`-simplified and Chaikin-smoothed. The 12 °C
**front is the isotherm outcrop** (`depth == iso`) within a nearshore band, contoured so
that real land is forced to the warm side — the line therefore **traces the shoreline**
wherever cold water reaches the edge (there the front simply *is* the shore) and pulls
offshore only where a warm shallow apron sits between the shore and the cold water. It
still marks the front out past casting range (a shallow flat shows the line offshore with
no green between it and shore). Narrow gaps in the CHS soundings are bridged by nearest-
sounding interpolation so the band and front connect across them; only larger unsurveyed
stretches stay blank. No imagery is
embedded in the deployed overlays (the browser fetches Esri basemap tiles live), though
the build reads that same imagery to tell land from unsurveyed water.

`scripts/build_coast_site.py` regenerates `web/data/` for a given issue day
(defaults to today UTC). No LLM in the build (ADR-001).

## Deploy / refresh
`.github/workflows/coast_site.yml` rebuilds the data and publishes `web/` to the
**`gh-pages` branch** (force-orphan single commit) **daily** (after the LSOFS t12z
cycle) and on demand (`workflow_dispatch`). We publish to a branch rather than via
`actions/deploy-pages`, whose Pages-deployment API repeatedly got stuck in
`deployment_queued` and timed out at 10 min; a `git push` has no such queue.

**One-time setup:** repo **Settings → Pages → Source: "Deploy from a branch" →
Branch: `gh-pages`, folder: `/ (root)`**. Then run the `coast-site` workflow once
(Actions tab → Run workflow) or wait for the daily run. The map appears at
`https://<owner>.github.io/<repo>/` — for this repo,
`https://richardrob123.github.io/tbay-fishcast/`.

Note: on a private repo with a free plan the published Pages site is public (the URL is
unlisted). The map shows only cold-water reachability along the public shore — nothing
sensitive — but be aware the link is reachable by anyone who has it.

The committed `web/data/` is a bootstrap snapshot so the site works the moment Pages is
enabled; the daily build overwrites it with the current forecast.
