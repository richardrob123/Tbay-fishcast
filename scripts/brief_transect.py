"""Cross-shore laker brief — the delivery prototype (operator's framing).

For each Superior-shore station, pull today's LSOFS temperature PROFILE, bias-correct,
and compute the cross-shore transect: where does the laker ceiling (<=12 C cold water)
meet the fishable slope, and is that within cast range? Flags the "go" spots.

    python scripts/brief_transect.py [YYYY-MM-DD]

Bathymetry here is COARSE (seed soundings + LSOFS node anchor) and flagged — drop in a
real contour map / CHS soundings per station to make the distances metre-accurate.
The bias offset is PROVISIONAL (lake-wide ~-3 C, pending a Thunder Bay logger).
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.features import bathy as bathy_cache  # noqa: E402
from tbay_fishcast.features.cross_shore import evaluate  # noqa: E402
from tbay_fishcast.ingest.backfill import station_node_map  # noqa: E402

LAKER_CEILING_C = 12.0     # upwelling laker trigger (events_calendar / species_rules)
CAST_RANGE_M = 75.0        # operator calibration (3/4 Cleo ~75 m)
PROVISIONAL_OFFSET_C = -3.0  # lake-wide mid-depth bias correction; PENDING TB logger
PROFILE_DEPTHS = [1, 2, 4, 6, 8, 10, 15]

# Coarse fallback only — used if a station has no cached NONNA/NCEI profile.
# The real bathymetry now comes from knowledge/bathymetry/<id>.json (build_bathymetry.py).
COARSE_FALLBACK = {
    "silver_harbour_outer": [[0, 0], [75, 6], [300, 9], [1183, 11.9]],
    "mackenzie_point":      [[0, 0], [2494, 13.8]],
    "marina_east_mcvicar":  [[0, 0], [439, 16.3]],
}


def latest_day(arg):
    if arg:
        return datetime.fromisoformat(arg).date()
    return date(2026, 8, 4)  # most recent full LSOFS day in this environment


def main(argv) -> int:
    from tbay_fishcast.ingest.backfill import _open_first
    from tbay_fishcast.ingest.lsofs_extract import extract_nodes
    from tbay_fishcast.ingest.lsofs_paths import LsofsFile, candidate_urls

    cfg = load_config()
    nodes = station_node_map(cfg)
    day = latest_day(argv[1] if len(argv) > 1 else None)

    print(f"=== CROSS-SHORE LAKER BRIEF — {day} (LSOFS t12z n006, valid 12:00Z) ===")
    print(f"    laker ceiling {LAKER_CEILING_C:.0f} C | cast range {CAST_RANGE_M:.0f} m | "
          f"offset {PROVISIONAL_OFFSET_C:+.0f} C (PROVISIONAL, pending TB logger)\n")

    # full temperature profile at each station node
    f = LsofsFile(day, "t12z", "n", 6)
    ds = _open_first(candidate_urls(f, cfg.lsofs.recent_bucket, cfg.lsofs.archive_bucket,
                                    byterange=False))
    try:
        allrows = extract_nodes(ds, nodes, PROFILE_DEPTHS)
    finally:
        ds.close()

    any_go = False
    for s in cfg.shore_stations:
        pr = sorted([r for r in allrows if r.station_id == s.id], key=lambda r: r.depth_m)
        depths = [r.depth_m for r in pr]
        temps = [r.temp_c + PROVISIONAL_OFFSET_C for r in pr]  # bias-corrected profile
        prof = bathy_cache.load(s.id)
        if prof is not None:
            bd, bh = prof.dist_m, prof.depth_m
            src = f"{prof.source} ({prof.resolution_ground_m:.0f} m, {prof.tier})"
        else:
            bathy = COARSE_FALLBACK.get(s.id, [[0, 0], [s.node_dist_m or 500, s.node_depth_m or 12]])
            bd = [p[0] for p in bathy]; bh = [p[1] for p in bathy]
            src = "coarse seed fallback (no cached profile)"
        t = evaluate(depths, temps, bd, bh, target_c=LAKER_CEILING_C, cast_range_m=CAST_RANGE_M)
        flag = "🎣 GO" if t.in_cast_range else "  --"
        any_go = any_go or t.in_cast_range
        surf, deep = temps[0], temps[-1]
        print(f"{flag}  {s.name}")
        print(f"      profile(corr): {surf:.1f}C surface -> {deep:.1f}C @ {depths[-1]:.0f} m")
        print(f"      bathy: {src}")
        print(f"      {t.note}")
        if prof is not None and prof.is_coarse:
            print("      (coarse ~92 m NCEI fallback — NONNA-10 has no nearshore coverage here)")
        print()
    print(f"VERDICT: {'at least one shore station has laker water in cast range today.' if any_go else 'no station has <=12 C water within cast range today (lakers offshore/deep).'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
