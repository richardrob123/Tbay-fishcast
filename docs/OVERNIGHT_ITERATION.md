# Overnight iteration log — 2026-08-07

Autonomous session under the standing rules (no model-contract change without a
proposed ADR + sign-off; no tuning on thin data; ship only verified improvements).
Each entry: what, why, evidence, and a critical evaluation. Newest first.

---

## Shipped

### 1. Frozen shoreline — deterministic coast, no live Overpass in the data path (commit `4d02625`)

**Problem (root cause of the Silver Harbour tip flickering solid↔faint between builds).**
The authoritative Lake Superior water mask — which decides land vs water and therefore
shore-distance — was re-fetched live from OSM Overpass on *every* runner build and was
gitignored, never committed. A complete fetch placed the point's near-tip shelf within a
cast (solid green); a partial/rate-limited fetch shifted the shoreline so the same water
read as >75 m offshore (faint "past a cast"). This is a flaky live dependency in the
deterministic data path — the exact thing ADR-001 / rule #1 forbids.

**Evidence (how it was proven, not guessed).** Rebuilt `mackenzie_silver` three ways with
identical bias `(3.8/0.8/5.7, n=12)`, GLSEA anchor `17.7 °C`, LSOFS cycle, and 4 m/px:

| build | tip cell | within-cast E/SE | t12 area (rel) | reach_ha |
|---|---|---|---|---|
| deployed-code, local | solid `t12` | solid `t12` | 2251.7 | 19.9 |
| working-code, local | solid `t12` | solid `t12` | 2251.7 | 19.9 |
| **live runner (02:18 build)** | **faint `t12far`** | **faint `t12far`** | 1980.8 | 17.5 |

Same code, same data — only the freshly-fetched shoreline differed. Bathymetry off the
tip is 6–9 m within a cast, colder than the 3.4 m 12 °C isotherm, so solid is correct.

**Fix.** `data/watermask_frozen/*.npz` (7 committed masks, ~60 KB total). `water_mask()`
returns the frozen raster before any network call; live fetch remains as an
auto-freezing fallback for any not-yet-frozen bbox. Test asserts the frozen path takes
zero network (`_overpass`/`_fetch_barrier_ways` patched to raise).

**Critical eval.** Correct and low-risk: the shoreline is a static geographic fact, so
freezing it removes variance without losing information. Re-run `scripts/freeze_watermask.py`
only when `STRETCHES`/`HALF_M`/`PX` change. Residual: the mask is only as good as the OSM
fetch it was frozen from — verified visually against imagery for all 7 stretches this session.

### 2. Accuracy gate finally persists (commit `d9330d3`)

**Problem.** The daily "commit observation logs back to the branch" step failed on *every*
run: the build leaves `web/data/**` (tracked) dirty, so `git rebase origin/$BR` aborted with
"cannot rebase: You have unstaged changes." The isotherm-depth gate + nearshore-anchor rows
were computed correctly ("appended 4 row(s)") but never committed — `data/gate_log.csv` has
sat empty (header only). The season-long accuracy record the depth-bias correction depends on
was silently not accumulating.

**Fix.** `git rebase --autostash`. Reproduced the failure and verified the fix in a throwaway
repo (plain rebase → "cannot rebase"; `--autostash` → commit lands, `web/data` preserved
uncommitted). This is the single biggest *accuracy* enabler tonight: AUDIT_ROUND3 says the
path to a real accuracy claim is "re-score when n is respectable" from this exact log.

**Critical eval.** Infrastructure, not a model change — safe. Verify on the next scheduled
run that `data/gate_log.csv` gains rows.

---

## Whole-coast fidelity + coverage sweep (verification, no change)

Now that the shoreline is frozen/deterministic, swept all 7 stretches:

- **Continuity:** all consecutive stretches overlap 4.7–39.6 % — the arc is continuous,
  ~26 km from the Kam mouth (48.37, −89.28) to Silver Harbour/MacKenzie (48.54, −88.92).
  No gaps between boxes.
- **Cold stations (lead 0):** Silver `t12@0 m`, MacKenzie `t12@40 m`, Marina `t12@13 m` —
  all solid within a cast. The Silver-tip fix holds.
- **"Uncovered" points:** `mountdale_kam` and `sturgeon_bay_launch` fall outside all boxes,
  but both are explicitly **warmwater** launches, correctly excluded from a cold-water
  product and absent from the manifest stations. Not a bug.

---

## Critical evaluation vs the ideal state

Target: trustworthy where-is-castable-cold-water for the whole accessible shore, +0…120 h,
with honest intervals, deterministic, loud on staleness, incapable of recommending closed water.

**Where it's strong now:** continuous 26 km cold-shore coverage at 4 m/px; deterministic
shoreline and reachability (map and pins share one pipeline); uncertainty shipped as a band,
not false precision; staleness surfaced; provenance on the product constants.

**The dominant accuracy limitation (honest):** the subsurface bias correction (central 3.8 °C,
up to 5.7 °C) is anchored on NDBC thermistors 170–270 km away (45216 Ontonagon, LLO1 Duluth)
— the audit's "uncertain leg." A large correction from far-field buoys plausibly over- or
under-cools the *Thunder Bay* subsurface, moving the 12 °C isotherm depth (and thus the
castable boundary) by more than the 75 m cast band. Two data points, both far, make this
un-validatable tonight. The just-fixed gate is what will measure it over the season. **I have
deliberately not changed the correction** — that is a model-contract change (rules 4/6/8/11)
and needs sign-off; see proposed ADRs below.

**Gaps to the ideal (ranked):**
1. **Local subsurface truth.** The whole accuracy story hinges on far buoys. Bare Point intake
   FOI (docs/BARE_POINT_DATA_REQUEST.md, task #8) would put a nearshore temperature series in
   the city — highest-leverage accuracy input, currently blocked awaiting the user to send it.
2. **Nearshore-warm surface signal is invisible to GLSEA.** Landsat 30 m shows the shore
   +1.7…+2.8 °C warmer than the offshore GLSEA anchor, but GLSEA at 1 km spans only 0.31 °C
   across a box — per-node GLSEA can't recover it (ADR-020 checked and demoted). Capturing it
   needs Landsat blended into the anchor, which is too intermittent/thin (n=3) to use yet.
3. **Lead-time uncertainty is flat.** The isotherm band width comes only from buoy-bias spread;
   it does not widen at +72…120 h as LSOFS forecast error grows. Honest intervals should. No
   clean data-driven growth rate exists yet (LSOFS isn't ensemble). Proposed ADR-021.
4. **Coverage stops at MacKenzie Point.** The cold outer Sibley/Sleeping-Giant shore to the NE
   is real cold-water shore but less accessible (park, boat). Expansion is a scope call. Proposed
   ADR-022.

---

## Proposed ADRs (awaiting human sign-off — NOT yet implemented)

> **ADR-020 (PROPOSED, then DEMOTED same session — kept for the record).** *Idea:* sample
> GLSEA per LSOFS node instead of once at box center to follow the nearshore→offshore SST
> gradient. *Empirical check (this session):* GLSEA SST across the whole `mackenzie_silver`
> box spans only **0.31 °C** (17.5–17.8) — at 1 km resolution GLSEA does not resolve the
> nearshore warming that Landsat sees at 30 m (+1.7…+2.8 °C). Per-node GLSEA would move the
> isotherm <0.3 m — not worth a core-path change. **Capturing the real nearshore-warm effect
> needs Landsat 30 m blended into the anchor**, but Landsat is intermittent (16-day repeat,
> cloud) and n=3 — too thin to anchor on. Revisit once the nearshore_anchor log has a
> respectable clear-scene n; until then the single-point GLSEA anchor is adequate at box scale.

> **ADR-021 (PROPOSED) — Lead-dependent isotherm band widening.** Widen the isotherm-depth
> band with forecast lead to reflect growing LSOFS error, so +120 h reads as less certain than
> +0 h. Rationale: current flat band understates far-lead uncertainty (rule: honest intervals).
> Blocker: no clean growth rate — needs the gate log stratified by lead to estimate it
> empirically (do not hand-pick). Recommend: hold until gate has lead-stratified n, then fit on
> a temporal holdout.

> **ADR-022 (PROPOSED) — Extend coverage NE to the outer Sibley/Sleeping-Giant shore.** Add
> stretches beyond MacKenzie Point. Rationale: genuine cold-water shore. Cost: less accessible
> (Sleeping Giant PP — regs gate must be verified T1 first, rule/ADR-007), more build time.
> Recommend: user decides scope; if yes, verify access-legality before adding.

---

## Open / next

- Verify on the next scheduled coast-site run: (a) deployed build is byte-deterministic across
  runs (frozen mask), (b) `data/gate_log.csv` gains rows (autostash fix).
- Decide ADR-020/021/022.
- Unblock local subsurface truth (Bare Point FOI) — the accuracy ceiling until then.
