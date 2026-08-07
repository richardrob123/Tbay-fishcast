# Fish-behavior review — is the cold-water model on the right track? (2026-08-07)

Literature/agency review of shore-accessible coldwater fish behavior around Thunder Bay,
assessing the model's biological + physical assumptions. Sources at bottom. Several are
model-contract changes → proposed as ADRs, NOT applied unilaterally (rule 11 / rule 8).

## Verdict

**Core approach is sound.** The lake-trout thermal numbers are the right family, and the
west-wind → north-shore upwelling physics is well-documented and correctly framed (Wedderburn
control, ~10 °C cooling, 0–120 h ≈ event lifetime). Two refinements and one conceptual fix.

## Validated / to refine — thermal numbers

| our value | literature | action |
|---|---|---|
| sweet spot 10–12 °C | adult lean lake trout occupy **6–9.5 °C** in summer; 10.1 °C is the age-0 preferendum | **shift "sweet spot" label to ~6–10 °C**; keep 12 °C as the OUTER margin, not the sweet spot |
| growth optimum 12.5 °C | ~12.5 °C (McCauley & Tait) | keep — but it's a *growth* optimum for young fish, not where adults concentrate |
| chronic ceiling 16 °C | behavioral **avoidance ~15 °C** | **lower ceiling to ~15 °C** |
| ≤8 °C strongest signal | matches fall 8–9 °C shallow window | keep |
| seiche ~40 h | Superior **surface** seiche ~8–14 h; 40 h is likely internal/near-inertial relaxation | reconcile the label (not load-bearing) |

Only the **lean** morph is nearshore (<80 m); siscowet's colder preference is irrelevant to a
shore isotherm — the model already builds around the lean form, which is correct.

## The biggest thing likely wrong — cold *trough* vs cold *edge/relaxation*

"Coldest reachable water = best" conflates *where cold-adapted fish can live* with *where/when
they're catchable*. A fresh, sharp upwelling **suppresses** the shore bite short-term (cold
shock → sluggish fish); the productive window is the **relaxation phase** and the **thermal
front/edge** (cold meets warm, baitfish stack), often 1–5 days later or where the edge is
moving — not the coldest cell. **Recommendation: reward the cold gradient/edge and weight the
relaxation phase, not the peak-upwelling trough.**

## Over-indexing on temperature

River mouths (Kaministiquia, Current, Neebing–McIntyre), points/breakwalls, low-light
(dawn/dusk), and forage rival or exceed temperature for actual shore catch. Temperature-
reachability should be ONE layer (consistent with ADR-008/ADR-010 discipline), not the whole
model. A river-plume-proximity term may predict shore catch better than the isotherm for several
species.

## Species — coldwater focus is too narrow, and the sign is wrong for some

| species | cold upwelling effect | note |
|---|---|---|
| lake trout (lean) | helps in summer | model's core case |
| **coaster brook trout** | **helps most** | cold keeps them shallow (<7 m, <400 m) — add; our logic works best here |
| steelhead / Kamloops | neutral/mixed | river-plume + season driven |
| chinook / coho | weak | tributary/migration driven (fall river-mouth staging) |
| **brown trout** | **HURTS** | cold pushes them off — "colder=better" INVERTS the signal |
| **walleye / pike / smallmouth** | **HURTS** | warm inner-bay fishery — cold reduces catch |
| whitefish | helps | cold draws them inshore in spring/fall windows |

## Seasonal regimes (target shifts by season)

- **Spring:** whole shore already cold — upwelling matters *least*; target river plumes/structure.
- **Summer (stratified):** the model's core regime — cold-reachability predicts opportunity, but
  reward the edge/relaxation.
- **Fall:** lake trout stage on rocky shoals to spawn at 8–11 °C surface, cooling/photoperiod-
  driven; upwelling secondary. Best shore window of the year — deserves its own regime.

## Status (ADR-024 → ADR-026)

**Shipped (ADR-024):** species-aware **preferred-range** map with the warm-edge **front** as the
prime mark; species chips; salmon/steelhead flagged weak-cue. Replaced "colder = better".
**Shipped (ADR-025):** upwelling-**phase** banner (setup/peak/relaxation/neutral) — day 0 from
**observed** Thunder Bay airport wind (METAR), forecast leads from the ensemble; **river-mouth**
structure markers (Kaministiquia, Current, Neebing–McIntyre).
**Shipped (ADR-026):** the flat range fill is now a **graded** thermal-suitability field (bottom-
temp inverted from the isotherm stack, graded through each species' preference curve → nested
zones, optimal core inside the range); the binary upwelling threshold is replaced by a
**continuous** favorability (logistic across the Wedderburn range).

**Deliberately NOT done — the honest boundary.** We do **not** fuse temperature × phase × front ×
structure into one "probability" with hand-picked weights: with no catch/field-session outcomes yet
there is nothing to fit them against, and a guessed composite is exactly what CLAUDE rules 6–8
forbid. We even tried to calibrate just the wind→cooling response from NDBC buoy history — it did
not discriminate (offshore buoys miss the coastal-upwelling signal; `data/calib/`), so that curve
stays a labelled physics prior. The multi-signal weighting waits on the pre-registered field logs,
where it can be fit and temporal-split validated.

## Original proposed changes

1. **Relabel the thermal bands** (small, ships in the map): sweet spot → ~6–10 °C, 12 °C = outer
   margin, ceiling 15 °C. Update `stations.yaml product:` provenance + the legend copy.
2. **Cold-edge / relaxation weighting** (design change): score the front/gradient and the
   relaxation phase, not the coldest cell. Needs the wind/upwelling-phase signal we already
   compute (setup vs relaxation) wired into the map score.
3. **Species/regime layers** (larger): coaster-brook-trout regime, a warmwater/brown penalty,
   a river-plume-proximity term, and a fall-spawn regime. Sequence behind #1–2.
4. **Pull GLFC Sp. Pub. 87-3 + McCauley & Tait** into `knowledge/` as T1/T2 citations (egress-
   blocked here; fetch on an open machine).

## Sources
Movement Ecology 2023 (adult nearshore telemetry); USGS McCauley & Tait (age-0 preferendum/growth);
GLFC Sp. Pub. 87-3 (temperature relationships); Li et al. 2021 JGR-Oceans (upwelling event physics,
r=−0.87 wind-vs-cooling); Lakehead Nipigon Bay coaster thesis; MN TU / JS-Outdoors / Northern
Ontario Travel / Great Lakes Angler (shore practice). Full URLs in the session research record.
