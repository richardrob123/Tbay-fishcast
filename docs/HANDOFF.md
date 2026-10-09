# Handoff: where tbay-fishcast stands

Committed on purpose, so a Claude Project thread sees it. Keep it under 60 lines, with links rather
than copies. Update it when a piece of work ends. Last updated 2026-10-09 (backfills).

## Where we are
- **The site is live and the repo is public.** Rules: `CLAUDE.md`. Plan: `PLAN.md`. Decisions (ADRs):
  `DECISIONS.md`. The default branch is `claude/tbay-fishcast-phase-0-6kh5lx` (not `main`): start there.
- **Data commits resumed 2026-10-08** (a9345b6): the `.gitignore` fix (364b0ef) works. They had stopped
  2026-09-04 (last bot commit 8c5b804) because `data/wind_lead_gate_log.csv` was git-ignored and the
  `git add` in the "Commit the observation logs" step of `coast_site.yml` failed silently.
- 2026-09-21: f7b86ae fixed CHS renaming its WCS axis labels (the forecast had been dead for 7 days).

## The gap in the logs
- 2026-09-04 until a9345b6 has no rows in three logs that can only be written live:
  `scripts/accumulate_gate.py`, `scripts/accumulate_wind_gate.py`, `scripts/check_offshore_climatology.py`.
- Backfilled 2026-10-09: wind lead (`--past-days 120 --clim-years 8`) and nearshore anchor.
- Cloud threads now reach the data hosts (the `fishcast` environment allows the 10 data hosts).
  Backfill scripts are append-only; read the header and `--help` first (nearshore has no `--help`).

## Due
- `backfill_thermal_gate.py --start 2026-09-04 --end <yesterday>`: GLOS `obs_42` now answers 400
  "Unrecognized variable sea_water_temperature_1_depth". Source schema change; needs a code look.
- `backfill_surface_gate.py --start 2026-09-04 --end <yesterday>`: Open-Meteo archive rate-limited
  (empty reply) right after the wind-lead run. Retry later, once.

## Checks
- `pip install -e ".[dev,geo]"`, then `python -m pytest` (not bare pytest): 535 tests on 2026-10-07, no network needed. CI
  (`ci.yml`) runs the same on every push. Say which ran.

## Workflows (reference; do not edit)
- `coast_site.yml` builds and deploys the site 4 times a day (03:45, 09:45, 15:45, 21:45 UTC). The
  15:45 run also does the once-a-day gates, the knowledge refresh and the data commit.
- `heartbeat.yml` sends the phone alerts (04:30, 10:30, 16:30, 22:30 UTC).
- `hindcast.yml` runs only when `data/hindcast_request.json` is pushed, or by hand.

## Owner-only (a thread never does these)
Anything under `.github/workflows/` (edit, add, re-run, trigger). `data/hindcast_request.json` (a push
starts a 350-minute job). Secrets, tokens or alert-topic names in commits, PRs or logs. PLAN.md
changes without an ADR and the owner's sign-off (rule 11). The `gh-pages` branch. The daily Routine's self-repair PR merges itself when `pytest -q` is green and it touches none of the above (rule 10); otherwise it stays open for the owner.

## Mac-only work (use "Work locally" with the Worktree option)
Backfills the cloud network cannot reach, and anything that needs the owner's phone.
