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
    pooled_s, pooled_r = [], []
    per_stretch = {}
    for sid, name, clat, clon, exposure in bcs.STRETCHES:
        if sid in bcs.LOW_CONFIDENCE:            # sparse survey -> noisy -> exclude from the fit
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
        relief = su.bathymetric_relief(depth, res)
        # aligned pairs under a COMMON mask so the combined structure STRENGTH (max of the two
        # normalized kinds) can be computed per-pixel for the continuous-glow band edges.
        common = within & np.isfinite(slope) & np.isfinite(relief)
        vs = slope[common]
        vr = relief[common]
        if vs.size < 500:
            continue
        pooled_s.append(vs)
        pooled_r.append(vr)
        per_stretch[sid] = {"slope_p90": round(float(np.percentile(vs, 90)), 3),
                            "relief_p90_m": round(float(np.percentile(vr, 90)), 2)}
        print(f"{sid:20s} n={vs.size:6d}  slope p90 {np.percentile(vs,90):.3f}  "
              f"relief p90 {np.percentile(vr,90):.2f} m")

    if not pooled_s:
        print("no surveyed stretches produced data"); return 1
    alls = np.concatenate(pooled_s)
    allr = np.concatenate(pooled_r)
    s_pcts = {p: round(float(np.percentile(alls, p)), 3) for p in (50, 66, 75, 85, 90, 95, 99)}
    r_pcts = {p: round(float(np.percentile(allr, p)), 2) for p in (50, 66, 75, 85, 90, 95, 99)}
    s_bar = round(float(np.percentile(alls, PCTILE)), 3)
    r_bar = round(float(np.percentile(allr, PCTILE)), 2)
    # Continuous structure STRENGTH = max of the two kinds each NORMALIZED by its own p90 bar, so
    # strength ~1.0 at a regionally-typical "real break" and higher on the strongest structure.
    # The glow-band edges are percentiles of THIS pooled strength distribution — data-derived, not
    # picked multipliers. (break = the p90 reference ~1.0; strong/exceptional = p95/p99.)
    strength = np.maximum(alls / s_bar, allr / r_bar)
    strength_bands = {
        "break": round(float(np.percentile(strength, 90)), 2),
        "strong": round(float(np.percentile(strength, 95)), 2),
        "exceptional": round(float(np.percentile(strength, 99)), 2),
    }
    print(f"\nPOOLED n={alls.size} reachable px across {len(per_stretch)} surveyed stretches")
    print("slope percentiles:", s_pcts)
    print("relief percentiles (m):", r_pcts)
    print("structure-strength glow bands (xp90-normalized):", strength_bands)
    print(f"=> STRUCT_SLOPE_ABS  = p{PCTILE} pooled slope  = {s_bar} rise/run")
    print(f"=> STRUCT_RELIEF_ABS = p{PCTILE} pooled relief = {r_bar} m (shoal/point local rise)")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "struct_slope_abs": s_bar, "struct_relief_abs_m": r_bar, "percentile": PCTILE,
        "slope_percentiles": s_pcts, "relief_percentiles_m": r_pcts, "n_pixels": int(alls.size),
        "strength_bands": strength_bands,  # continuous-glow edges (p90/p95/p99 of pooled strength)
        "per_stretch": per_stretch, "stretches": list(per_stretch.keys()),
        "definition": ("Absolute structure bars, both the pXX percentile pooled over reachable "
                       "water across all surveyed stretches (CHS NONNA-10): slope = |grad depth| "
                       "(drop-off flanks); relief = neighbourhood-mean depth − depth (shoal tops / "
                       "point tips / crests the slope misses). Reproducible, not picked constants."),
        "retrieved_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, indent=1))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
