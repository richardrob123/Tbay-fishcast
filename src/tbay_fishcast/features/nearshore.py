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
    try:
        rows = list(csv.DictReader(open(path)))
        deltas = [float(r["delta_c"]) for r in rows if r.get("delta_c") not in (None, "")]
    except (OSError, ValueError, KeyError):
        return 0.0, 0
    if not deltas:
        return 0.0, 0
    return sum(deltas) / len(deltas), len(deltas)
