# Handoff: where tbay-fishcast stands

Committed on purpose, so a Claude Project thread sees it. Keep it under 60 lines, with links rather
than copies. Update it when a piece of work ends. Last updated 2026-10-07 (Project setup).

## Where we are
- **The site is live and the repo is public.** Rules: `CLAUDE.md`. Plan: `PLAN.md`. Decisions (ADRs):
  `DECISIONS.md`. The default branch is `claude/tbay-fishcast-phase-0-6kh5lx` (not `main`): start there.
- **Data commits stopped on 2026-09-04** (last bot data commit: 8c5b804). Cause: the "Commit the
  observation logs" step in `.github/workflows/coast_site.yml` runs `git add` on each log, and
  `data/wind_lead_gate_log.csv` was git-ignored, so the step failed every day. The step is
  `continue-on-error`, so the site kept building and nobody saw it.
- **The fix is one line**: `!data/wind_lead_gate_log.csv` at the END of `.gitignore` (commit 364b0ef).
  The owner pushes it from the Mac before 11:45 AM EDT on 2026-10-08, when the 15:45 UTC run commits
  data. Check it landed: `git log --oneline -3 -- .gitignore` on the default branch shows 364b0ef.
- 2026-09-21: f7b86ae fixed CHS renaming its WCS axis labels (the forecast had been dead for 7 days).

## The gap in the logs
- Days from 2026-09-04 until the first good data commit have no rows. Three logs can only be written
  live and cannot be rebuilt: `scripts/accumulate_gate.py`, `scripts/accumulate_wind_gate.py` and
  `scripts/check_offshore_climatology.py`. Say so in the commit that fills the rest.
- These can be rebuilt with the backfill scripts. Each is append-only and skips rows it already has;
  read its header and `--help` first: `backfill_thermal_gate.py --start --end`,
  `backfill_surface_gate.py --start --end`, `backfill_wind_lead_gate.py --past-days N`,
  `backfill_nearshore_anchor.py`. The nightly workflow only backfills a trailing window (thermal and
  surface 10 days, wind lead 14 days), so a longer gap needs an explicit run.
- The scripts fetch from NOAA, GLOS and Landsat. Not yet checked: whether a cloud thread's network
  reaches them. If a fetch is blocked, stop and name the host; run it with "Work locally" instead.

## Due
- **2026-10-08, after the 15:45 UTC run:** confirm a new "data: accumulate" commit on the default
  branch, newer than 8c5b804. If there is none, read that run's log for the "Commit the observation
  logs" step (or ask the owner for it) and find why. Once one lands, run the backfills for
  2026-09-04 to today, one at a time, and open a PR with the rows they add. The scripts write the
  files; you do not edit logs by hand.

## Checks
- `pip install -e ".[dev,geo]"`, then `pytest -q`: 535 tests on 2026-10-07, no network needed. CI
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
