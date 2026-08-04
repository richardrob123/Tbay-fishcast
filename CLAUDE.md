# CLAUDE.md — tbay-fishcast

Deterministic fishing-forecast system for Thunder Bay shore fishing. LLMs orchestrate, interpret, research, and repair. They never sit in the data path.

## Model policy
- **Interactive architecture/orchestration sessions (this one):** top-tier model — Claude Fable 5 (or Opus 4.8). Reasoning-heavy, low volume.
- **Defined engineering subtasks via subagents** (write a tested module to spec, refactor, fixture generation): Sonnet-class (`claude-sonnet-4-6`).
- **Bulk extraction/classification** (MN DNR archive parsing, review mining, log classification): Haiku-class (`claude-haiku-4-5-20251001`).
- **Daily brief Routine:** Sonnet-class. One self-contained prompt, structured input, five-line output.
- Model lineup changes; verify current names at docs.claude.com before wiring anything scheduled.

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
10. **Self-repair via PR, never auto-merge.** Scoped `--allowedTools` on all scheduled runs.
11. **Ask before deviating.** Changes to PLAN.md require a proposed ADR and human sign-off.
12. **Respect source ToS.** No Facebook or auth-walled scraping. Rate-limit and honor robots.txt everywhere else.
13. **Concise outputs.** Reports and briefs: verdict first, receipts attached, no filler.

## Stack
Python 3.12, xarray/netCDF4/scipy, DuckDB + parquet in-repo, GitHub Actions (heartbeat), one Claude Routine daily (brief + log check + repair PRs), ntfy push, `stations.yaml` as config. No servers.

## Domain quick-reference
Five stations (full data in `knowledge/`): Silver Harbour, MacKenzie Point, Marina Park east/McVicar, Kam mouth/Mountdale, Sturgeon Bay. Physics: west-quadrant wind → upwelling on the city/north shore; Wedderburn threshold ≈12–17 kt sustained depending on mixed-layer depth; setup ≈10 h; seiche ≈40 h. Fish ceiling: conditions are highly predictable, fish response weakly — the product is calibrated probabilities with honest intervals, not certainty.
