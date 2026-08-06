"""Build the committed offshore stratification climatology from the GLERL mooring.

OFFLINE builder (run once / on data refresh): downloads the 2.35 MB NCEI mooring NetCDF
to a temp path, computes the half-month climatology, and writes the compact committed
JSON that ingest/mooring.py serves. The raw NetCDF is not committed (raw data, gitignored);
the small derived climatology is. No LLM (ADR-001).

    python scripts/build_mooring_climatology.py [path_to.nc]
"""
from __future__ import annotations

import json
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tbay_fishcast.ingest import mooring  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "knowledge" / "mooring_superior_climatology.json"


def main(argv) -> int:
    if len(argv) > 1:
        nc_path = argv[1]
    else:
        nc_path = Path(tempfile.gettempdir()) / "glerl_superior_mooring.nc"
        if not nc_path.exists():
            print(f"downloading {mooring.NCEI_URL} ...")
            urllib.request.urlretrieve(mooring.NCEI_URL, nc_path)  # noqa: S310
    clim = mooring.build_climatology(nc_path)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(clim, indent=2) + "\n")
    print(f"wrote {OUT.relative_to(OUT.parents[1])}  ({len(clim['periods'])} half-month periods)")
    for k, p in sorted(clim["periods"].items()):
        print(f"  {k}: iso12={p['iso12_depth_m']} m  surf={p['surface_c']}C  "
              f"mixedT={p['mixed_layer_c']}C  n={p['n_profiles']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
