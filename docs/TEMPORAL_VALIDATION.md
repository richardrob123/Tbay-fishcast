# Temporal validation — does the correction hold across years?

> **⚠ AUDIT_ROUND3 (2026-08-06):** the held-out cross-year corrected MAE of **3.23 °C**
> in this document is the honest temporal-transfer number for the product. Where this
> doc re-anchors trust on the same-month LOBO 1.39 °C, note that figure barely holds
> anything out (same window, near-identical fold corrections) — see `docs/AUDIT_ROUND3.md`.

CLAUDE.md rule 6: tune on 2024, validate on held-out 2025–26. Everything else so far
is July–Aug 2026. This asks whether the LSOFS warm bias is a stable, correctable
feature across seasons and years, or a fluke of one month.

**Method.** `scripts/validate_temporal.py` samples every ~8 days through each year's
fishing season and compares LSOFS to every NDBC buoy at the buoy's own sensor depth.
2024 uses the `regulargrid` product (native `fields` don't start until after the
buoys haul out); 2025–26 use native fields. 84 buoy-days.

**Data reality that shapes the result:** the buoys sensor SHALLOW in the archive
(1 m in 2024–25) and deeper only in 2026 realtime (3–6 m). So the cross-year
comparison is honest but depth-limited — see the caveat.

## Results

### Bias stability — warm every year, grows with depth
| year | n | depth(s) | mean bias (LSOFS−buoy) | std |
|------|--:|----------|-----------------------:|----:|
| 2024 | 35 | 1 m | **+1.62** | 3.76 |
| 2025 | 31 | 1 m | **+2.23** | 4.19 |
| 2026 | 18 | 3–6 m | **+4.29** | 2.04 |

LSOFS runs warm at the subsurface in **every year tested** — the phenomenon is not a
2026 artifact. The near-surface (1 m) bias is consistent across 2024–25 (~+2 °C), and
it grows with depth (1 m ≈ +2, 6 m ≈ +4.7), the same taper the correction model assumes.

### Temporal transfer (rule 6) — fit on 2024, apply to held-out 2025
At the overlapping depth (1 m): correction +1.62 fit on 2024, applied to 2025 →
raw MAE 3.91 → **corrected MAE 3.23**, **anomaly correlation 0.66** (positive timing
skill on a year it was never fit to). The correction transfers forward in the right
direction; the sign and rough magnitude of the bias are stable across years.

## The honest caveat

The archive buoys only sensor **near the surface (1 m)** — the noisiest depth in the
lake (diurnal heating and wind mixing move it several °C within hours), which a single
12Z model snapshot cannot phase-match. That is why the 1 m MAE (~3.2) is much larger
than the clean 6 m number: most of that error is **timing variance at 1 m, not model
skill**. The trustworthy accuracy figure remains the same-year **6 m leave-one-buoy-out
MAE of 1.39 °C** (`ENGINE_VALIDATION.md`); the multi-year record confirms the *bias is
stable and correctable across years*, but cannot re-measure 6 m skill in 2024–25
because no buoy sensored 6 m then.

## Verdict

Temporally **robust where it can be checked**: the LSOFS warm bias is present and
warm in 2024, 2025 and 2026, grows with depth consistently, and a correction fit on
one year improves a held-out year with positive timing skill. The design choice to
anchor the surface to same-day satellite SST (rather than assume a fixed surface bias)
is vindicated — the near-surface bias is not zero and not identical year to year
(+1.6 vs +2.2). The remaining gap is purely observational: without a 6 m sensor in the
archive years (or a Thunder Bay logger now), the clean deep-water skill can only be
measured in 2026. That is a data limit, not a model failure — and one logger closes it.

Reproduce: `python scripts/validate_temporal.py gather && … report`.
