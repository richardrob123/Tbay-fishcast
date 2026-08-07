# Accuracy scorecard (auto-generated — do not hand-edit)

Honest, un-tuned back-test of the standing observation logs. Grows as the daily gate
accumulates. **Caveats that bound every number below:** both isotherm chains are
170–270 km from Thunder Bay (45216 Ontonagon, llo1 Duluth), and their obs are a
coarse/surface proxy for the product's ~6 m target — so this tracks *trend and sign of
skill*, not a Thunder Bay 6 m verification. Local truth (Bare Point intake) would replace
these proxies.

## Isotherm-depth (12 °C) gate

Scored day×chain rows: **3**

| chain | n | raw MAE | corrected MAE | skill vs raw | persistence MAE | note |
|---|---|---|---|---|---|---|
| 45216 | 1 | 7.37 m | 3.93 m | +47% | — |  |
| llo1 | 2 | 2.33 m | 1.22 m | +48% | 0.00 m | obs sensor-pinned (constant) — persistence is trivially 0, discount it |
| **pooled** | 3 | 4.01 m | 2.12 m | +47% | | n-weighted |

**Read:** the correction beats raw LSOFS pooled (2.12 m vs 4.01 m). Skill is the audit's demotion test (ADR-006): a correction that can't beat raw gets benched.

## Nearshore anchor (Landsat 30 m shore − GLSEA offshore, same-day)

n=3 clear scenes · shore-warm **+2.35 °C** (range +1.73…+2.76). GLSEA (1 km) cannot resolve this nearshore warming; too few scenes to fold into the anchor yet (see OVERNIGHT_ITERATION ADR-020).
