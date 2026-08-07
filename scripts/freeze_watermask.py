"""Freeze the authoritative OSM water mask for every coast stretch into COMMITTED rasters.

The Lake Superior shoreline is a static geographic fact. Re-fetching it live from Overpass on
every runner build made the shoreline non-deterministic: a partial/rate-limited fetch shifted
the coast and dropped the within-cast cold wedge off narrow points (Silver Harbour tip flicked
solid↔faint between builds). This one-off (re-run only when STRETCHES / HALF_M / PX change)
resolves the mask ONCE, from a known-good fetch, and writes data/watermask_frozen/*.npz which
`watermask.water_mask()` loads in preference to any network call. Run:  python scripts/freeze_watermask.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import numpy as np  # noqa: E402

from tbay_fishcast.ingest import nonna  # noqa: E402
from tbay_fishcast.ingest.watermask import (WaterMaskError, _frozen_get,  # noqa: E402
                                            save_frozen_mask, water_mask)

from build_coast_site import HALF_M, PX, STRETCHES  # noqa: E402


def main() -> int:
    ok = 0
    for sid, name, clat, clon, _exposure in STRETCHES:
        try:
            patch = nonna.fetch_patch(clat, clon, half_m=HALF_M, scale_px=PX)
        except Exception as e:  # noqa: BLE001
            print(f"skip {sid}: NONNA fetch failed ({str(e)[:60]})")
            continue
        H, W = patch.depth.shape
        # water_mask() saves the frozen raster on a successful live build via its hook; but
        # if a frozen file already exists it would short-circuit, so force a fresh resolve.
        try:
            mask = water_mask(patch.bounds_3857, (H, W))
        except WaterMaskError as e:
            print(f"skip {sid}: water mask unavailable ({str(e)[:60]})")
            continue
        path = save_frozen_mask(patch.bounds_3857, mask)
        # sanity: round-trip and confirm the committed file reproduces the mask exactly
        back = _frozen_get(patch.bounds_3857, H, W)
        match = back is not None and bool(np.array_equal(back, mask))
        print(f"{sid:20s} {H}x{W}  water={mask.mean():6.1%}  -> {path.name}  roundtrip={'ok' if match else 'FAIL'}")
        ok += 1
    print(f"\nfroze {ok}/{len(STRETCHES)} stretch masks into data/watermask_frozen/")
    return 0 if ok == len(STRETCHES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
