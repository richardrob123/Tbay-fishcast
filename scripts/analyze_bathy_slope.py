"""Derive the structure-edge slope bar from the REGIONAL bathymetry, not a picked number.

`build_coast_site.STRUCT_SLOPE_ABS` decides which reachable pixels count as a real bottom
break/drop-off (the "edge" that, with optimal temperature, makes a spot prime). It must be an
ABSOLUTE bar (ADR-029) — a per-scene percentile always flags a third of every stretch. But the
absolute value should still come from DATA: the pooled slope distribution over the surveyed
shore. This script fetches every surveyed stretch, computes the corrected shore-distance/depth
field exactly as the build does, and pools |grad depth| over the reachable band. A genuine
drop-off is the steep tail of that regional distribution; we pin the bar at a high percentile and
record the measurement so the constant is reproducible, not asserted (CLAUDE rule 3).

    python scripts/analyze_bathy_slope.py            # -> prints + writes data/calib/bathy_slope.json

No LLM. Read-only w.r.t. the product (only writes the calibration record).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import build_coast_site as bcs  # noqa: E402
from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.features import suitability as su  # noqa: E402
from tbay_fishcast.features.reachability import corrected_fields  # noqa: E402
from tbay_fishcast.ingest import nonna  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "data" / "calib" / "bathy_slope.json"
PCTILE = 90  # a "real break" is the steep tail — the top ~10% of reachable regional slope


def main() -> int:
    cfg = load_config()
    pooled = []
    per_stretch = {}
    for sid, name, clat, clon, exposure in bcs.STRETCHES:
        if sid in bcs.LOW_CONFIDENCE:            # sparse survey -> noisy slope, exclude from the fit
            continue
        try:
            patch = nonna.fetch_patch(clat, clon, half_m=bcs.HALF_M, scale_px=bcs.PX)
        except Exception as e:  # noqa: BLE001
            print(f"skip {sid}: {str(e)[:50]}"); continue
        if patch.coverage_frac < 0.03:
            continue
        res = nonna.ground_res_m(patch.res_mercator_m, clat)
        depth, dist, _deg = corrected_fields(patch.depth, patch.bounds_3857, res)
        within = (np.isfinite(depth) & (dist <= cfg.product.cast_m)
                  & (depth <= cfg.product.max_reach_depth_m))
        slope = su.bathymetric_structure(depth, res)
        v = slope[within & np.isfinite(slope)]
        if v.size < 500:
            continue
        pooled.append(v)
        per_stretch[sid] = {p: round(float(np.percentile(v, p)), 3) for p in (50, 75, 90, 95)}
        print(f"{sid:20s} n={v.size:6d}  p50 {np.percentile(v,50):.3f}  p75 {np.percentile(v,75):.3f}  "
              f"p90 {np.percentile(v,90):.3f}  p95 {np.percentile(v,95):.3f}")

    if not pooled:
        print("no surveyed stretches produced slope data"); return 1
    allv = np.concatenate(pooled)
    pcts = {p: round(float(np.percentile(allv, p)), 3) for p in (50, 66, 75, 85, 90, 95, 99)}
    bar = round(float(np.percentile(allv, PCTILE)), 3)
    print(f"\nPOOLED n={allv.size} reachable px across {len(per_stretch)} surveyed stretches")
    print("pooled percentiles:", pcts)
    print(f"=> STRUCT_SLOPE_ABS = p{PCTILE} of pooled regional slope = {bar} rise/run")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "struct_slope_abs": bar, "percentile": PCTILE,
        "pooled_percentiles": pcts, "n_pixels": int(allv.size),
        "per_stretch_percentiles": per_stretch,
        "stretches": list(per_stretch.keys()),
        "definition": ("Absolute bar for a real bottom break: the pXX percentile of |grad depth| "
                       "pooled over reachable water across all surveyed stretches (CHS NONNA-10). "
                       "Reproducible from the regional bathymetry, not a picked constant."),
        "retrieved_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, indent=1))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
