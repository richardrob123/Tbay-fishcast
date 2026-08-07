# Accuracy scorecard (auto-generated — do not hand-edit)

Honest, un-tuned back-test of the standing observation logs. Grows as the daily gate
accumulates. **Caveats that bound every number below:** both isotherm chains are
170–270 km from Thunder Bay (45216 Ontonagon, llo1 Duluth), and their obs are a
coarse/surface proxy for the product's ~6 m target — so this tracks *trend and sign of
skill*, not a Thunder Bay 6 m verification. Local truth (Bare Point intake) would replace
these proxies.

## Isotherm-depth (12 °C) gate

Scored day×chain rows: **3** · **n_effective (moving-obs, skill-bearing): 1**

> Skill is pooled over the **1 diagnostic chain(s)** with a varying observed isotherm; sensor-floor-pinned chains are shown but excluded from the skill number (their 'error' against a constant is not skill). With n_effective this small, read the sign and rough magnitude, not the exact percent. ⚠ NOT INDEPENDENT: diagnostic chain(s) 45216 also feed the bias correction, so this is train-on-test — the correction is scored on data it was partly fit to. Treat the skill % as an UPPER BOUND until an independent local truth (Bare Point) exists.

| chain | n | raw MAE | corrected MAE | skill vs raw | persistence MAE | note |
|---|---|---|---|---|---|---|
| 45216 | 1 | 7.37 m | 3.93 m | +47% | — |  |
| llo1 | 2 | 2.33 m | 1.22 m | +48% | 0.00 m | obs sensor-pinned (constant) — persistence is trivially 0, discount it |
| **pooled (diagnostic)** | 1 | 7.37 m | 3.93 m | +47% | | moving-obs chains only |

**Read:** the correction beats raw LSOFS pooled (3.93 m vs 7.37 m). Skill is the audit's demotion test (ADR-006): a correction that can't beat raw gets benched.

## Forecast skill by lead (12 C isotherm, forecast vs obs — ADR-021)

How the forecast the map ships degrades with lead time. Same far-chain caveats. **NOT YET DIAGNOSTIC:** most forecast rows are the sensor-floor-pinned chain, so the per-lead MAE below is largely noise around a constant and is expected to be flat / non-monotonic — do not read lead-decay from it until ≥2 chains carry a moving obs across ≥2 leads.

| lead | n | corrected MAE | raw MAE |
|---|---|---|---|
| +24 h | 5 | 1.76 m | 3.23 m |
| +48 h | 5 | 1.50 m | 2.79 m |
| +72 h | 5 | 2.23 m | 3.56 m |
| +96 h | 5 | 1.72 m | 3.56 m |
| +120 h | 5 | 1.73 m | 2.99 m |

(Nowcast/lead-0 is in the isotherm-depth table above.) The lead-dependent uncertainty band (ADR-021) activates once these per-lead n are respectable — the widening is read from this measured error, never hand-picked.

## Nearshore anchor (Landsat 30 m shore − GLSEA offshore, same-day)

n=3 clear scenes · shore-warm **+2.35 °C** (range +1.73…+2.76). GLSEA (1 km) cannot resolve this nearshore warming; too few scenes to fold into the anchor yet (see OVERNIGHT_ITERATION ADR-020).

## Offshore cross-check (LSOFS vs GLERL mooring climatology, ADR-034)

n=8 days · mixed-layer model−clim mean **-0.12 °C**, iso-12 depth model−clim mean **+6.1 m** · 0 DIVERGENT day(s). Independent (observed) check that the model's central-basin thermocline isn't grossly misplaced; generous band (2-yr clim vs 1 day).

## Over-lake wind gate (GFS forecast vs NDBC buoy obs, ADR-032/034)

n=12 buoy-days · GFS speed MAE **2.29 kn**, W-quadrant forecast−obs bias **+0.69 kn** · airport(CYQT)−buoy offset **-0.48 kn** (W-quadrant +0.94). The real over-lake test the wind-model choice deferred; the airport offset is what a separate phase threshold would need to earn — currently within noise, so one Wedderburn bar is used.
