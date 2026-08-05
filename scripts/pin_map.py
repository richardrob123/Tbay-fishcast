"""Drop-a-pin laker report for ANY Thunder Bay location — not just the five stations.

Give it a latitude/longitude (a spot you fish, or one you're scouting) and it runs
the exact same satellite-validated pipeline as the station map: snap to the nearest
LSOFS water node, correct the temperature (GLSEA surface + buoy-measured subsurface
band), find where the 12 °C laker line meets the real NONNA bottom, and flag the
reachable cold water — overlaid on satellite imagery.

    python scripts/pin_map.py LAT LON ["Name"] [YYYY-MM-DD]
    python scripts/pin_map.py 48.006 -89.166 "Little Trout Bay"

Writes viz/pin_map.html (self-contained). Falls back with a clear message if the
pin lands off the NONNA-10 survey (no fine bathymetry there).
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import build_isotherm_maps as bim  # noqa: E402
from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.features import thermocline  # noqa: E402

OUT_HTML = Path(__file__).resolve().parents[1] / "viz" / "pin_map.html"


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "pin"


def main(argv) -> int:
    if len(argv) < 3:
        print(__doc__); return 2
    lat, lon = float(argv[1]), float(argv[2])
    name = argv[3] if len(argv) > 3 and not re.match(r"\d{4}-\d{2}-\d{2}", argv[3]) else f"Pin {lat:.4f},{lon:.4f}"
    dstr = next((a for a in argv[3:] if re.match(r"\d{4}-\d{2}-\d{2}", a)), None)
    day = date.fromisoformat(dstr) if dstr else date(2026, 8, 4)

    cfg = load_config()
    ds, grid = bim.open_lsofs(cfg, day)
    try:
        central, lo, hi, detail, n = bim.pooled_subsurface_bias(cfg, day)
        print(f"pooled subsurface warm bias: central {central:+.2f} band [{lo:+.2f},{hi:+.2f}] n={n}")
        wind = bim.upwelling_context(day)
        try:
            card = bim.analyze_location(ds, grid, day, lat, lon, name, _slug(name),
                                        (central, lo, hi, n))
        except Exception as e:  # noqa: BLE001
            print(f"analysis failed: {e}"); return 1
    finally:
        ds.close()

    if card is None:
        print(f"'{name}' ({lat},{lon}) has no NONNA-10 nearshore coverage or no 12 °C water "
              f"in the column — can't render a fine plan view here.")
        return 1
    print(f"{name}: iso {card['iso_central']:.1f} m (band {card['iso_lo']:.1f}-{card['iso_hi']:.1f}) "
          f"reachable={card['reachable']}")
    biasmodel = thermocline.BiasModel(0.0, central, lo, hi, n_buoys=n)
    OUT_HTML.write_text(bim.build_page(day, [card], biasmodel, detail, wind))
    print(f"wrote {OUT_HTML}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
