"""Measured nearshore surface warm-delta — shared by the coast MAP and the station-PIN paths so
they anchor the surface identically (validation 2026-08 finding #1: they had diverged, the map
warming the anchor and the pins using raw GLSEA, so the two published views disagreed on the same
water by ~2.35 C).

GLSEA's ~1 km satellite pixel cannot resolve the nearshore; Landsat 30 m over the shore measures
the difference (data/nearshore_anchor.csv logs shore-minus-GLSEA per clear pass). The multi-year
backfill (2019-2025, ADR-036) showed the delta is SPATIALLY STRUCTURED, not one number:

  * EXPOSED open-coast points (Silver Harbour, MacKenzie Pt): median ≈ -0.4 C — the exposed
    nearshore reads essentially like GLSEA (mixing; no trapped warm lens).
  * SHELTERED water (the breakwall marina): median ≈ +1.6 C — enclosed nearshore genuinely warm.

So the correction is applied PER EXPOSURE CLASS (the stretch/station's exposure maps to the class
measured at stations of that kind), signed, from the same QC'd rows. One module, used by both
publish paths, so map and pins cannot re-diverge. Returns (0.0, 0) styles when the log is
absent/empty — then no correction is applied. Pure, offline, no LLM.
"""
from __future__ import annotations

import csv

from ..config import REPO_ROOT

NEARSHORE_ANCHOR = REPO_ROOT / "data" / "nearshore_anchor.csv"

# Which anchor stations measure which class. The two conservation-area points are open coast;
# the marina sits behind the breakwall. (Station registry: stations.yaml.)
_EXPOSED_STATIONS = {"silver_harbour_outer", "mackenzie_point"}

# Publish-path exposure of each SHORE STATION pin (same classes the map's stretches use):
# the pins path passes this through forecast_spot(exposure=...) so both views share one rule.
STATION_EXPOSURE = {
    "silver_harbour_outer": "exposed",
    "mackenzie_point": "exposed",
    "marina_east_mcvicar": "sheltered",
}


def _qc_rows(path=NEARSHORE_ANCHOR) -> list[tuple[str, float]]:
    """QC'd (station, delta_c) rows: summer-stratified months, clear pixel near the station,
    physically plausible values. Same filter the region-wide median has always used."""
    try:
        rows = list(csv.DictReader(open(path)))
    except (OSError, ValueError, KeyError):
        return []
    kept = []
    for r in rows:
        try:
            d = float(r["delta_c"]); cloud = float(r.get("cloud_pct", 0) or 0)
            dist = float(r.get("dist_m", 0) or 0); ls = float(r.get("landsat_st_c", 0) or 0)
            mo = (r.get("scene_date", "") or "")[5:7]
        except (ValueError, KeyError, TypeError):
            continue
        if mo not in ("07", "08", "09"):
            continue
        if cloud > 10 or dist > 150:
            continue
        if not (-3.0 <= d <= 6.0 and 2.0 <= ls <= 26.0):
            continue
        kept.append((r.get("station", ""), d))
    return kept


def _median(vals: list[float]) -> float:
    vals = sorted(vals)
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2


def nearshore_surface_delta(path=NEARSHORE_ANCHOR) -> tuple[float, int]:
    """Region-wide robust median delta (°C) and n — the legacy single number. Prefer
    `delta_for_exposure` for anything location-specific."""
    deltas = [d for _s, d in _qc_rows(path)]
    if not deltas:
        return 0.0, 0
    return round(_median(deltas), 2), len(deltas)


def nearshore_delta_by_class(path=NEARSHORE_ANCHOR) -> dict:
    """{'exposed': (median_c, n), 'sheltered': (median_c, n)} from the QC'd rows, classified by
    which anchor station measured them. A class with no rows reports (0.0, 0) → no correction."""
    rows = _qc_rows(path)
    out = {}
    for cls in ("exposed", "sheltered"):
        vals = [d for s, d in rows
                if (s in _EXPOSED_STATIONS) == (cls == "exposed")]
        out[cls] = (round(_median(vals), 2), len(vals)) if vals else (0.0, 0)
    return out


def delta_for_exposure(exposure: str | None, path=NEARSHORE_ANCHOR) -> tuple[float, int]:
    """Signed delta for a publish location. `exposure` in {'exposed','sheltered'}; None → the
    region-wide median (legacy behavior, for callers that don't know their class)."""
    if exposure in ("exposed", "sheltered"):
        return nearshore_delta_by_class(path)[exposure]
    return nearshore_surface_delta(path)
