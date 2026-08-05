# Whole-coast forecast map (hosted, phone-friendly)

A zoomable map of the Thunder Bay shore showing **reachable cold water** (green) and
the **12 °C line** (red), scrubbable across the nowcast + 5-day forecast — opened as a
URL on a phone, no local run. Lives in `web/`, deploys to GitHub Pages.

## What it shows
- **Live satellite basemap** (Esri World Imagery) with pan/zoom/pinch.
- **Cold-water + isotherm overlays** for four overlapping stretches spanning the arc
  from the Kaministiquia mouth to MacKenzie Point / Silver Harbour, precomputed from
  CHS NONNA-10 bathymetry × the LSOFS isotherm field (same engine + native-sigma
  correction as the per-spot product).
- **Forecast slider** (+ Play) stepping the whole coast through the 5-day forecast.
- **Station pins** with today's verdict, the isotherm-depth trajectory, and surface SST.
- **Upwelling-wind probability** per day (ensemble) and a **data-age / STALE** banner.
- Total reachable hectares per forecast day.

## Architecture
`web/index.html` is a static MapLibre app (library vendored in `web/vendor/`, no CDN
dependency). It reads `web/data/manifest.json` — stretch corner coordinates, per-day
transparent overlay PNGs, station verdicts, wind probabilities, data age — and stacks
the overlays on the basemap. The overlays are transparent georeferenced PNGs
(~0.25 MB total); no imagery is embedded (the browser fetches Esri tiles live).

`scripts/build_coast_site.py` regenerates `web/data/` for a given issue day
(defaults to today UTC). No LLM in the build (ADR-001).

## Deploy / refresh
`.github/workflows/coast_site.yml` rebuilds the data and deploys to Pages **daily**
(after the LSOFS t12z cycle) and on demand (`workflow_dispatch`).

**One-time setup:** repo **Settings → Pages → Source: "GitHub Actions"**. Then run the
`coast-site` workflow once (Actions tab → Run workflow) or wait for the daily run. The
map appears at `https://<owner>.github.io/<repo>/` — for this repo,
`https://richardrob123.github.io/tbay-fishcast/`.

Note: on a private repo with a free plan the published Pages site is public (the URL is
unlisted). The map shows only cold-water reachability along the public shore — nothing
sensitive — but be aware the link is reachable by anyone who has it.

The committed `web/data/` is a bootstrap snapshot so the site works the moment Pages is
enabled; the daily build overwrites it with the current forecast.
