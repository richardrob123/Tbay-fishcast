# tbay-fishcast

Deterministic fishing-conditions hindcast/forecast for **Thunder Bay, ON** shore fishing.
LLMs orchestrate, interpret, research, and repair — they never sit in the data path
(ADR-001). Ingest, features, and scoring are pure, deterministic Python.

**Status: Phase 0 (hindcast validation).** No live alerts exist yet. Phase 0 finds out
where the predictability ceiling actually sits, on archives, before anything is built on
top of it. See `PLAN.md` for phases/gates and `docs/FIRST_HOUR_VERIFICATION.md` for the
data-source reality check.

## What works today

- **LSOFS temperature layer (live, tested).** Grid bootstrap (KDTree nearest node per
  Superior-shore station), FVCOM sigma-layer → fixed-depth (2/6/10 m) interpolation, and
  station-node extraction from NOAA NODD S3 via fsspec bulk-fetch (~5 s/file). Proven
  end-to-end against live S3 and pinned to a real golden fixture.
- **Safety-critical regs gate (tested as an invariant).** The system is incapable of
  recommending closed/prohibited water (Kakabeka, McIntyre spring sanctuary) — ADR-007.
- **Verification scorecard** (MAE/bias/RMSE, event POD/FAR contingency) — the math the
  commissioning gates G1/G2 are read from.
- **Upwelling-event detector** and **Wedderburn** susceptibility — real, deterministic
  physics with provisional (to-be-calibrated) thresholds.

- **First real gate read (G1).** LSOFS surface SST vs GLSEA over a validation month
  (`scripts/g1_scorecard.py`): daily-mean model (diurnal aliasing removed, QC'd) vs GLSEA
  nearest-valid-pixel, scored MAE/bias/RMSE/median/p90/r per station + pooled. July 2025:
  ~0 bias but MAE 2.30 °C (fails ≤2.0) with low day-to-day correlation — real upwelling
  variability the satellite smooths. See `docs/G1_SCORECARD.md`.

## Data sources

All verified live (`docs/DATA_SOURCES.md`): LSOFS (S3), GLSEA SST (GLERL ERDDAP —
`GLSEA_ACSPO_GCS` 2006→present for truth, `GLSEA_GCS` 1995→2023 for climatology), ERA5
wind (Open-Meteo), ECCC GeoMet/HYDAT (21 local stations, IDs recorded), NDBC Slate buoy.
Reference-ingest hosts require a **Custom** egress allowlist on the cloud environment
(keep "include default package managers" on, so the LSOFS S3 buckets stay reachable);
the clients raise `SourceUnavailable` if a host is blocked, so the system degrades loudly.

## Layout

```
src/tbay_fishcast/
  config.py            stations.yaml loader; knowledge-pack version pin (ADR-013)
  ingest/
    lsofs_paths.py     NODD S3 key/URL construction (both verified layouts)
    lsofs_grid.py      KDTree nearest-node bootstrap (longitude-wrap aware)
    lsofs_extract.py   station-node temp extraction -> tidy bronze rows (UTC)
    backfill.py        the hindcast backfill loop (live S3, ~5 s/file)
    era5_wind.py glsea.py hydat.py ndbc_slate.py   reference clients (egress-blocked)
  features/
    sigma.py           sigma-layer -> depth interpolation (property-tested)
    events.py          upwelling-event detector
    wedderburn.py      wind-driven upwelling susceptibility (physics)
  scoring/regs_gate.py safety-critical closed-water gate (invariant)
  verification/scorecard.py   MAE / POD / FAR (gate math)
  storage/parquet_store.py    bronze parquet + DuckDB as-of join (ADR-003/014)
tests/                 property, golden-file, as-of, and safety-invariant tests
scripts/bootstrap_grid.py     one-shot: pin station nodes + cut the golden fixture
```

## Develop

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest                       # hermetic: golden fixture + synthetic data, no network
```

Re-pin station nodes / rebuild the fixture from a freshly downloaded LSOFS file:

```bash
python scripts/bootstrap_grid.py /path/to/lsofs.tHHz.YYYYMMDD.fields.nNNN.nc
```

## Ground rules (full text in `CLAUDE.md` / `DECISIONS.md`)

No LLM in the heartbeat · TDD with golden fixtures · provenance tiers on all knowledge
(regs T1-only) · closed water is unrecommendable · staleness is loud · temporal splits
only · UTC in storage · self-repair via PR, never auto-merge. Nothing outside the current
phase (`NOT_BUILDING.md`); deviations need a proposed ADR + sign-off.
