"""Measured nearshore surface warm-delta — shared by the coast MAP and the station-PIN paths so
they anchor the surface identically (validation 2026-08 finding #1: they had diverged, the map
warming the anchor and the pins using raw GLSEA, so the two published views disagreed on the same
water by ~2.35 C).

GLSEA's ~1 km satellite pixel cannot resolve the nearshore warming; Landsat 30 m over the shore
reads warmer (data/nearshore_anchor.csv logs shore-minus-GLSEA). We add this measured offset to
the GLSEA surface anchor so the shallow nearshore profile isn't dragged cold by the offshore
value — a MEASURED, directionally-certain correction, applied once and in exactly one place.

Pure, offline, no LLM. Returns (delta_c, n): the mean logged delta and its sample count, or
(0.0, 0) when the log is absent/empty (then no correction is applied).
"""
from __future__ import annotations

import csv

from ..config import REPO_ROOT

NEARSHORE_ANCHOR = REPO_ROOT / "data" / "nearshore_anchor.csv"


def nearshore_surface_delta(path=NEARSHORE_ANCHOR) -> tuple[float, int]:
    """Region-wide shore-minus-GLSEA warm delta (°C) and its sample count, QC'd.

    Built from the multi-year Landsat anchor log (scripts/backfill_nearshore_anchor.py + the daily
    accumulator). The RAW rows are noisy — cloud-contaminated pixels read cold, the spring (June)
    regime runs very warm, and pixels sampled far from the station are unrepresentative — so we
    filter to the SUMMER-STRATIFIED regime the map models, clear pixels near the station, and a
    physical delta range, then take the MEDIAN (robust to the remaining skew).

    IMPORTANT (validated 2026-08): the delta is SPATIALLY VARIABLE — ~0/slightly-negative at the
    exposed points (Silver Harbour, MacKenzie) and ~+1.6 only at the sheltered marina. The old
    uniform +2.35 was a single warm-scene artifact that over-warmed the whole exposed shore. The
    region-wide median is ~+0.2 (near zero); a future refinement is an exposure-aware delta. Returns
    (0.0, 0) when the log is absent/empty (then no correction is applied)."""
    try:
        rows = list(csv.DictReader(open(path)))
    except (OSError, ValueError, KeyError):
        return 0.0, 0
    kept = []
    for r in rows:
        try:
            d = float(r["delta_c"]); cloud = float(r.get("cloud_pct", 0) or 0)
            dist = float(r.get("dist_m", 0) or 0); ls = float(r.get("landsat_st_c", 0) or 0)
            mo = (r.get("scene_date", "") or "")[5:7]
        except (ValueError, KeyError):
            continue
        if mo not in ("07", "08", "09"):      # summer stratified regime only (exclude spring)
            continue
        if cloud > 10 or dist > 150:          # clear pixel, near the station
            continue
        if not (-3.0 <= d <= 6.0 and 2.0 <= ls <= 26.0):   # physically plausible
            continue
        kept.append(d)
    if not kept:
        return 0.0, 0
    kept.sort()
    median = kept[len(kept) // 2] if len(kept) % 2 else (kept[len(kept) // 2 - 1] + kept[len(kept) // 2]) / 2
    return round(median, 2), len(kept)
