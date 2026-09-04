# Accuracy scorecard (auto-generated — do not hand-edit)

Honest, un-tuned back-test of the standing observation logs. Grows as the daily gate
accumulates. **Caveats that bound every number below:** both isotherm chains are
170–270 km from Thunder Bay (45216 Ontonagon, llo1 Duluth), and their obs are a
coarse/surface proxy for the product's ~6 m target — so this tracks *trend and sign of
skill*, not a Thunder Bay 6 m verification. Local truth (Bare Point intake) would replace
these proxies.

## Isotherm-depth (12 °C) gate

Scored day×chain rows: **11** · **n_effective (moving-obs, skill-bearing): 11**

> Skill is pooled over the **2 diagnostic chain(s)** with a varying observed isotherm; sensor-floor-pinned chains are shown but excluded from the skill number (their 'error' against a constant is not skill). With n_effective this small, read the sign and rough magnitude, not the exact percent. ⚠ NOT INDEPENDENT: diagnostic chain(s) 45216 also feed the bias correction, so this is train-on-test — the correction is scored on data it was partly fit to. Treat the skill % as an UPPER BOUND until an independent local truth (Bare Point) exists.

| chain | n | raw MAE | corrected MAE | skill vs raw | persistence MAE | note |
|---|---|---|---|---|---|---|
| 45216 | 4 | 5.83 m | 3.18 m | +46% | 3.56 m |  |
| llo1 | 7 | 7.72 m | 8.71 m | -13% | 4.42 m |  |
| **pooled (diagnostic)** | 11 | 7.03 m | 6.70 m | +5% | | moving-obs chains only |

**Read:** the correction beats raw LSOFS pooled (6.70 m vs 7.03 m). Skill is the audit's demotion test (ADR-006): a correction that can't beat raw gets benched.

## Forecast skill by lead (12 C isotherm, forecast vs obs — ADR-021)

How the forecast the map ships degrades with lead time. Same far-chain caveats. **NOT YET DIAGNOSTIC:** most forecast rows are the sensor-floor-pinned chain, so the per-lead MAE below is largely noise around a constant and is expected to be flat / non-monotonic — do not read lead-decay from it until ≥2 chains carry a moving obs across ≥2 leads.

| lead | n | corrected MAE | raw MAE |
|---|---|---|---|
| +24 h | 15 | 4.96 m | 5.92 m |
| +48 h | 15 | 4.74 m | 5.49 m |
| +72 h | 15 | 4.95 m | 5.52 m |
| +96 h | 15 | 4.70 m | 5.18 m |
| +120 h | 15 | 4.93 m | 6.04 m |

(Nowcast/lead-0 is in the isotherm-depth table above.) The lead-dependent uncertainty band (ADR-021) activates once these per-lead n are respectable — the widening is read from this measured error, never hand-picked.

## Nearshore anchor (Landsat 30 m shore − GLSEA offshore, same-day)

n=59 clear scenes · shore-warm **+1.08 °C** (range -11.77…+10.14). GLSEA (1 km) cannot resolve this nearshore warming; too few scenes to fold into the anchor yet (see OVERNIGHT_ITERATION ADR-020).

## Offshore cross-check (LSOFS vs GLERL mooring climatology, ADR-034)

n=37 days · mixed-layer model−clim mean **-0.69 °C**, iso-12 depth model−clim mean **+4.8 m** · 0 DIVERGENT day(s). Independent (observed) check that the model's central-basin thermocline isn't grossly misplaced; generous band (2-yr clim vs 1 day).

## Over-lake wind gate (GFS forecast vs NDBC buoy obs, ADR-032/034)

n=106 buoy-days · GFS speed MAE **2.56 kn**, W-quadrant forecast−obs bias **-0.66 kn** · airport(CYQT)−buoy offset **-1.30 kn** (W-quadrant -1.15). The real over-lake test the wind-model choice deferred; the airport offset is what a separate phase threshold would need to earn — currently within noise, so one Wedderburn bar is used.
