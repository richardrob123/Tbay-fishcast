# RESEARCH_PROTOCOL.md — how knowledge enters this system

Knowledge lands as **validated data**, never as prose an LLM reads at runtime. Research subagents fill schema-shaped dossiers; a validator gates entry.

## Confidence tiers
- **T1 — Official / peer-reviewed:** Ontario regs summaries, MNRF/NOAA/ECCC/CHS/LRCA publications, journal papers.
- **T2 — Named expert:** Gord Ellis articles, DNR reports, named tackle-shop guidance (with date).
- **T3 — Community:** the seed KML (Davidson "Bunny Holes"), forums, Google reviews, local Reddit. Valuable, cite exactly, never load-bearing alone.
- **T4 — Inference:** physics/geometry reasoning. Must be labeled and testable.

## Field rules
- Every field: `source` (URL/title), `retrieved` (date), `tier`.
- **Regs fields: T1 only.** No exceptions. `verified_on` mandatory.
- **Access-legality: T1 or `field_verify: true`** (a human must stand there). Origin: Bare Point was invented; Kakabeka's community pin sat in closed water; Mission Marsh had zero fishing evidence across 232 reviews.
- Contradictions: store both entries with tiers side-by-side. Do not resolve silently.
- Absence-of-evidence is data: record "no fishing mention across N reviews" as a field.

## Subagent rules
1. Seeded with `knowledge/seed/` — extend the verified corpus, don't rediscover it.
2. Per-dossier token budget and definition-of-done set in the task spec. No open-ended crawling.
3. Model: Sonnet-class for dossier research; Haiku-class for bulk parsing (e.g., MN DNR weekly archive → structured phenology rows).
4. Output = proposed YAML matching a schema in `knowledge/schemas/` + a sources appendix. Validator (deterministic) checks shape, tiers, dates; human reviews T3/T4 promotions.
5. Banned: ToS-walled sources; constructing "facts" from model priors without a citation (that's T4 and must say so).

## Field-session debrief (human, ≤2 min, after every outing)
1. Where/when/window, and what did the frozen forecast say? (auto-attached)
2. Effort: casts/hours, lure classes used.
3. Outcome: species/sizes/count — **blanks are logged**.
4. Water observations: hand temp, spoon-touch, clarity, seam/bait/birds seen.
5. Anything that contradicts the knowledge pack? → becomes a proposed update with tier T3 (own observation).

## Dossier queue (initial)
1. Per-station dossiers for the five core stations (fill `spots.yaml`).
2. Species dossiers: walleye, pike, chinook, pink, coho, steelhead, laker, perch, whitefish (fill `species_rules.yaml`).
3. Regs pack: FMZ 6 + FMZ 9 relevant waters incl. sanctuaries, Nipigon special rules (T1 only).
4. Events calendar: runs/windows with even/odd-year modifiers.
5. MN DNR archive extraction → phenology labels (control-coast proxy).
6. Road-end candidate list (satellite + municipal verification flags) — access `field_verify: true` until walked.
