# KICKOFF PROMPT — paste this into Claude Code in the repo root

You are building **tbay-fishcast**: a deterministic fishing-conditions forecast system for Thunder Bay, ON shore fishing, with LLM assistance only at defined edges. This repo contains the complete plan, decisions, research protocol, schemas, and seed knowledge from a week of validated field research. Your job is to execute it — not redesign it.

## Before writing any code
1. Read, in order: `CLAUDE.md`, `PLAN.md`, `DECISIONS.md`, `NOT_BUILDING.md`, `RESEARCH_PROTOCOL.md`, everything in `knowledge/`.
2. Reply with: (a) a restatement of the Phase 0 commissioning gates in your own words, (b) the list of unverified assumptions you will test in the first hour, (c) any conflicts you see between documents. Wait for my confirmation.

## Ground rules (full versions in CLAUDE.md / DECISIONS.md)
- **Phase 0 only** until its gates pass. Do not scaffold Phase 1+ beyond empty module stubs.
- **No LLM in the data heartbeat.** Ingest/features/scoring are pure Python. LLM = brief-writing, repair PRs, research subagents, calibration reports.
- **TDD**: schema contracts, golden-file fixtures, property tests, as-of joins. A feature without a test doesn't exist.
- **Provenance**: any knowledge entering `knowledge/` follows RESEARCH_PROTOCOL.md — source, retrieval date, confidence tier. Regs fields accept Tier-1 sources only.
- **Model policy** (see CLAUDE.md): this session runs on the top model; delegate defined engineering subtasks to Sonnet-class subagents; delegate bulk extraction/classification to Haiku-class. Verify current model names against docs before wiring the Routine.
- **Ask, don't assume**, on anything ambiguous. Deviations from PLAN.md require a proposed ADR in DECISIONS.md and my sign-off.
- Never touch anything in NOT_BUILDING.md. Never auto-merge your own repair PRs.

## First-hour verification list (do these before building on them)
1. Open one LSOFS nowcast file from the NODD bucket; confirm variable names, sigma-layer convention, node coordinate variables, and file size per cycle.
2. Confirm OPeNDAP subsetting works against the CO-OPS THREDDS for node-indexed temp extraction; benchmark vs full-file pull.
3. Confirm ERA5 hourly wind availability for 48.4N, -89.2W via Open-Meteo historical API.
4. Confirm GLSEA archive depth and pixel access method.
5. Confirm a GeoMet hydrometric station exists on the Kaministiquia and note its ID; list any stations on the Current/Neebing/McIntyre.
6. Report findings before proceeding.

## Human-only tasks (flag, don't attempt)
- CHS/DFO water-level station ID for Thunder Bay harbour (I'll confirm).
- LRCA permission email for a temperature logger at Silver Harbour.
- Temp logger purchase; ntfy topic; API key for the Routine; Facebook/NSSA intel (ToS-walled — humans only).

## Definition of done for this session
Phase 0 hindcast module skeleton with passing tests on golden fixtures, plus a written report of the first-hour verifications. Nothing more.

Begin with step 1.
