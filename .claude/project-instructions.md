# tbay-fishcast Project brief
Read CLAUDE.md first (it has the rules), then docs/HANDOFF.md.

Purpose: deterministic fishing forecast for Thunder Bay shore fishing. The site is LIVE and the repo is PUBLIC; bots commit data to the default branch every day.

Branch: the default branch is claude/tbay-fishcast-phase-0-6kh5lx (not main). Start from it and land work by PR (the bots commit there too, so fetch first). Never touch gh-pages.

Checks: pip install -e ".[dev,geo]" once, then pytest -q (no network needed; 535 tests on 2026-10-07).

Ask first (the owner decides): anything under .github/workflows/ (edit, add, re-run, trigger); data/hindcast_request.json (a push starts a 350-minute job); secrets, tokens or alert-topic names in a public repo; PLAN.md changes (need an ADR and sign-off). The daily Routine's self-repair PR merges itself only when pytest -q is green and it touches none of those paths; otherwise it stays open for the owner.

<!-- harness:core -->
How to work: nobody reviews your code or PRs, so the checks decide.
- Work on claude/<topic>; commit and push it by name often (the sandbox can reset between turns).
- Run the checks above before calling anything done, and say which ran. When they pass, merge your own PR into the default branch. If a check fails twice, stop, write why in the handoff file, and move on.
- Start what the owner asks, at most 2 threads at once. Update the handoff file when a piece of work ends. Record decisions in project memory.
- Only the "Ask first" items need the owner. Everything else: do it, then report.
- Unattended runs (Routines, scheduled checks) never stop to ask: take the safe default and say so in the first line of the output.

Models: the thread model does the work; planning and review use the analyst agent, fan-outs use scout, searches use Explore.
Limits: one repo per Project (a second repo silently drops the repo's hooks and permission rules in cloud threads). Mac-only work: "Work locally" with the Worktree option.
