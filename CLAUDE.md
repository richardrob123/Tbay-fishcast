# CLAUDE.md — tbay-fishcast

Deterministic fishing-forecast system for Thunder Bay shore fishing. LLMs orchestrate, interpret, research, and repair. They never sit in the data path.

## Model policy
- Lead and single judgement calls (`analyst`): `claude-opus-5-5`, effort medium.
- Code changes (`implementer`) and EVERY fan-out (`scout`, Workflow agents): `claude-sonnet-5-5`. Never the bare alias `sonnet`.
- Search: the `Explore` agent runs on `haiku`.
- Bulk extraction/classification (MN DNR archive parsing, review mining, log classification): Haiku-class (`claude-haiku-4-5-20251001`).
- Daily brief Routine: Sonnet-class. One self-contained prompt, structured input, five-line output.
- Model lineup changes; verify current names at docs.claude.com before wiring anything scheduled.
- Escalate one step only (haiku, sonnet, opus), and only after an objective failure.
- Never switch models mid-session: it throws away the prompt cache.
- Roles live in `.claude/models.json`; a hook refuses a `model` that contradicts a role (leave `model` out and the agent file's pin applies).
- Subagents: `implementer`, `scout`, `analyst`, `Explore` (a plan: `analyst`; a fan-out: `scout`).
- In a Workflow, write `agentType: "scout"` inline as a quoted literal in every `agent()` call.

## Standing behavioral rules
1. **No LLM in the heartbeat.** Ingest, features, scoring, alerts: pure Python, cron-driven, deterministic. If you find yourself putting a model call in a 4x-daily loop, stop — that's ADR-001.
2. **TDD, always.** Schema contracts on bronze; golden-file fixtures (recorded NetCDF in `tests/fixtures/`); property tests (interpolated temp bounded by bracketing layers; depth clamps at bottom; no feature reads data timestamped after its forecast time).
3. **Provenance or it doesn't exist.** Every knowledge field: `source`, `retrieved`, `tier` (T1–T4 per RESEARCH_PROTOCOL.md). Regs: T1 only. Access-legality: T1 or `field_verify: true`.
4. **The system must be incapable of recommending closed or prohibited water.** Regs gates are tested like security invariants (see Kakabeka in seed corpus — a community map pin sat inside a no-fishing provincial park).
5. **Staleness is loud.** Every brief carries data-age; stale ingest is never presented as current. (Origin: cached-weather failures during field week.)
6. **Temporal splits only.** Tune on 2022–2024, validate on 2025–2026. Never tune thresholds on data you report skill for.
7. **Pre-registration.** Forecasts freeze at session start; the frozen score is stored with every field-session record (selection-bias correction later depends on this column existing now).
8. **Demotion rule.** Any layer that can't beat climatology in quarterly review gets benched, not tweaked until it flatters.
9. **UTC in storage, local only at display.**
10. **Self-repair via PR.** The scheduled run opens a `claude/<topic>` PR and merges it itself only when `pytest -q` is green and the change touches no workflow, `data/hindcast_request.json` or `PLAN.md` path; otherwise the PR stays open for the owner. Scoped `--allowedTools` on all scheduled runs.
11. **Ask before deviating.** Changes to PLAN.md require a proposed ADR and human sign-off.
12. **Respect source ToS.** No Facebook or auth-walled scraping. Rate-limit and honor robots.txt everywhere else.
13. **Concise outputs.** Reports and briefs: verdict first, receipts attached, no filler.

## Stack
Python 3.12, xarray/netCDF4/scipy, DuckDB + parquet in-repo, GitHub Actions (heartbeat), one Claude Routine daily (brief + log check + repair PRs), ntfy push, `stations.yaml` as config. No servers.

## Domain quick-reference
Five stations (full data in `knowledge/`): Silver Harbour, MacKenzie Point, Marina Park east/McVicar, Kam mouth/Mountdale, Sturgeon Bay. Physics: west-quadrant wind → upwelling on the city/north shore; Wedderburn threshold ≈12–17 kt sustained depending on mixed-layer depth; setup ≈10 h; seiche ≈40 h. Fish ceiling: conditions are highly predictable, fish response weakly — the product is calibrated probabilities with honest intervals, not certainty.

## Live site and public repo
- This repo is PUBLIC and the site is LIVE. Never put secrets, tokens, alert-topic names or personal data in commits, PRs, issues or logs.
- Start with `docs/HANDOFF.md`. Check your work: `pip install -e ".[dev,geo]"` then `pytest -q` (hermetic, no network; CI runs the same). Say which ran.
- Land your own work: open a PR from `claude/<topic>` and merge it when the tests pass; nobody reviews PRs here. The scheduled self-repair run does the same within rule 10's limits (no workflow, `data/hindcast_request.json` or `PLAN.md` path).
- The bot writes these, so do not hand-edit them: `data/*_log.csv`, `data/calib/*.json`, `docs/ACCURACY_SCORECARD.md`, `knowledge/observations/**`, `web/data/**`. Never touch `gh-pages` (machine-written).
- Owner only: anything under `.github/workflows/` (editing, adding, re-running, triggering), and `data/hindcast_request.json` (a push to it starts a 350-minute hindcast job).

## Compact instructions
When compacting, keep: the current task, open decisions waiting on the owner, the files being changed, any failing test output (verbatim), and the line "re-read .claude/handoff.md if it exists".
