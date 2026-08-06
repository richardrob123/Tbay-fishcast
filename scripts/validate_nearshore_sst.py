"""Near-shore SST gate — Landsat water-pixel skin temp vs GLSEA's offshore anchor.

Quantifies how far GLSEA's land-masked, offshore-pulled surface anchor sits from the
actual nearshore water (Landsat C2 L2 ST, ~100 m, keeps shore water pixels). No auth
(Planetary Computer is anonymous); needs the geo extra (rasterio). No LLM.

    python scripts/validate_nearshore_sst.py [start YYYY-MM-DD] [end YYYY-MM-DD]
"""
from __future__ import annotations

import os
import sys
from datetime import date

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("GDAL_HTTP_MULTIRANGE", "YES")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tbay_fishcast.ingest import glsea, landsat_st  # noqa: E402

STATIONS = [("Marina", 48.4351, -89.2136), ("Silver", 48.5085, -88.9746),
            ("MacKenzie", 48.52294, -88.94336)]
BBOX = [-89.40, 48.33, -88.88, 48.58]


def main(argv) -> int:
    start = date.fromisoformat(argv[1]) if len(argv) > 1 else date(2026, 5, 1)
    end = date.fromisoformat(argv[2]) if len(argv) > 2 else date(2026, 8, 6)
    print(f"Landsat nearshore ST vs GLSEA offshore anchor ({start}..{end})\n")
    print(f"{'station':10}{'Landsat ST':>12}{'@dist':>7}{'scene':>12}  GLSEA anchor       Δ")
    diffs = []
    for nm, la, lo in STATIONS:
        px = landsat_st.fetch_recent_st(la, lo, BBOX, start, end, max_cloud=25)
        g = glsea.fetch_recent_sst(la, lo, end)
        if px and g:
            d = px.sst_c - g.sst_c
            diffs.append(d)
            print(f"{nm:10}{px.sst_c:10.1f}C{px.dist_m:6.0f}m{px.day:>12}  "
                  f"{g.sst_c:.1f}C @ {g.dist_km:.1f}km   {d:+.1f}")
        else:
            print(f"{nm:10}{'(no clear scene / no GLSEA)':>30}")
    if diffs:
        print(f"\nmean Δ (nearshore Landsat − offshore GLSEA): {sum(diffs)/len(diffs):+.1f} C")
        print("positive = the real shore water is warmer than GLSEA's offshore anchor "
              "(note: scene vs GLSEA day may differ — direction + rough magnitude).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
