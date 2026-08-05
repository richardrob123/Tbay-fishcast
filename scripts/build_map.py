"""Regenerate the cross-shore laker map (viz/laker_map.html) from live data.

Pulls today's LSOFS temperature profile (bias-corrected) + the real cached
bathymetry profiles (knowledge/bathymetry/, from build_bathymetry.py), computes
the cross-shore transect per station, and splices a fresh DATA object into the map
template. The map's render code (SVG cross-sections, isotherm, cast-range, chips)
is untouched — only DATA changes.

    python scripts/build_map.py [YYYY-MM-DD]

Provenance: the map footer carries the CHS NONNA attribution (licence requirement
for derivative products).
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.features import bathy as bathy_cache  # noqa: E402
from tbay_fishcast.features.cross_shore import evaluate  # noqa: E402
from tbay_fishcast.ingest.backfill import station_node_map  # noqa: E402

MAP_HTML = Path(__file__).resolve().parents[1] / "viz" / "laker_map.html"
LAKER_CEILING_C = 12.0
CAST_RANGE_M = 75.0
OFFSET_C = -3.0
PROFILE_DEPTHS = [1, 2, 4, 6, 8, 10, 15]


def latest_day(arg) -> date:
    return datetime.fromisoformat(arg).date() if arg else date(2026, 8, 4)


def build_data(day: date) -> dict:
    from tbay_fishcast.ingest.backfill import _open_first
    from tbay_fishcast.ingest.lsofs_extract import extract_nodes
    from tbay_fishcast.ingest.lsofs_paths import LsofsFile, candidate_urls

    cfg = load_config()
    nodes = station_node_map(cfg)
    f = LsofsFile(day, "t12z", "n", 6)
    ds = _open_first(candidate_urls(f, cfg.lsofs.recent_bucket, cfg.lsofs.archive_bucket,
                                    byterange=False))
    try:
        allrows = extract_nodes(ds, nodes, PROFILE_DEPTHS)
    finally:
        ds.close()

    stations = []
    attributions = set()
    for s in cfg.shore_stations:
        pr = sorted([r for r in allrows if r.station_id == s.id], key=lambda r: r.depth_m)
        prof = [[float(r.depth_m), round(r.temp_c + OFFSET_C, 2)] for r in pr]
        if prof:  # extend to 20 m (flat) so the render fills the column
            prof.append([20.0, prof[-1][1]])
        depths = [p[0] for p in prof]
        temps = [p[1] for p in prof]

        bp = bathy_cache.load(s.id)
        if bp is not None:
            bathy = [[round(d, 1), round(h, 2)] for d, h in zip(bp.dist_m, bp.depth_m)]
            src = f"{bp.source} · {bp.resolution_ground_m:.0f} m · {bp.tier}"
            if bp.attribution:
                attributions.add(bp.attribution)
        else:
            bathy = [[0, 0], [s.node_dist_m or 500, s.node_depth_m or 12]]
            src = "coarse seed (no cached profile)"

        bd = [b[0] for b in bathy]
        bh = [b[1] for b in bathy]
        t = evaluate(depths, temps, bd, bh, target_c=LAKER_CEILING_C, cast_range_m=CAST_RANGE_M)
        stations.append({
            "id": s.id, "name": s.name, "profile": prof, "bathy": bathy,
            "bathy_source": src,
            "coarse": bool(bp.is_coarse) if bp else True,
            "isotherm_depth": round(t.isotherm_depth_m, 1) if t.isotherm_depth_m is not None else None,
            "in_range_dist": round(t.distance_from_shore_m, 1) if t.distance_from_shore_m is not None else None,
            "in_range": t.in_cast_range,
            "note": t.note,
        })
    return {
        "day": day.isoformat(), "cast_range_m": CAST_RANGE_M, "target_c": LAKER_CEILING_C,
        "offset_c": OFFSET_C, "stations": stations,
        "attribution": " ".join(sorted(attributions)),
    }


def main(argv) -> int:
    day = latest_day(argv[1] if len(argv) > 1 else None)
    data = build_data(day)
    block = "const DATA = " + json.dumps(data, indent=1) + ";"
    html = MAP_HTML.read_text()
    new_html, n = re.subn(r"const DATA = \{.*?\n\};", lambda _: block, html,
                          count=1, flags=re.DOTALL)
    if n != 1:
        print("ERROR: could not locate the DATA block to replace")
        return 1
    MAP_HTML.write_text(new_html)
    go = [s["name"] for s in data["stations"] if s["in_range"]]
    print(f"map rebuilt for {day}: {len(data['stations'])} stations, "
          f"{len(go)} in cast range ({', '.join(go) or 'none'})")
    for s in data["stations"]:
        print(f"  {'GO ' if s['in_range'] else '-- '} {s['name']:34s} {s['bathy_source']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
