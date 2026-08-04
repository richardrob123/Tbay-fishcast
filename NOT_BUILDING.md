# NOT_BUILDING.md — the fence. Do not build, scaffold, or "prepare for" any of these.

- Web dashboards, apps, or servers of any kind (the heatmap is ONE static HTML file per day)
- Airflow / Dagster / queues / microservices / Postgres
- ML fish-prediction models before the ADR-009 data gate
- Solunar-weighted scoring (display-only field permitted, weight zero)
- Facebook / Instagram / auth-walled scraping, or any ToS-violating collection
- LLM calls inside the 4×-daily heartbeat
- Auto-merged repair PRs
- Live GIS raster pipelines (bathymetry lives as per-station config in `spots.yaml`)
- Real-time fish "detection" claims of any kind
- Multi-user features, auth, sharing — this is a single-operator instrument
- Anything not in PLAN.md without an accepted ADR
