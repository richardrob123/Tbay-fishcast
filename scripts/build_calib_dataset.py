"""Assemble a persisted matched dataset (LSOFS + in-situ buoy + ERA5 wind) for
correction modelling. One parquet per (buoy, year): 6-hourly rows with the buoy's
in-situ temp, LSOFS at the same location/depth, and the trailing favorable wind-run.

    python scripts/build_calib_dataset.py 45023 2024

Reuses validate_buoy's extraction. Saved to data/calib/<buoy>_<year>.parquet so the
modelling script can iterate without re-extracting LSOFS.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # import sibling script

from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.features.wind import favorable_wind_run  # noqa: E402
from tbay_fishcast.ingest.era5_wind import fetch_wind  # noqa: E402
from tbay_fishcast.ingest.ndbc import BUOYS  # noqa: E402
from validate_buoy import buoy_series, lsofs_series  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def main(station: str, year: int) -> int:
    cfg = load_config()
    buoy = BUOYS[station]
    b, depth = buoy_series(station, year)
    if b is None:
        print("no buoy data"); return 1
    l = lsofs_series(cfg, buoy, year, depth)
    # wind at the buoy location (favorable = west-quadrant, as at Thunder Bay's north shore)
    start = min(b.index.min(), l.index.min()).date().isoformat()
    end = max(b.index.max(), l.index.max()).date().isoformat()
    h = fetch_wind(start, end, lat=buoy.lat, lon=buoy.lon)
    wt = pd.DatetimeIndex([pd.Timestamp(t, tz="UTC") for t in h["time"]])
    run = pd.Series(favorable_wind_run([t.to_pydatetime() for t in wt],
                                       np.asarray(h["wind_speed_10m"], float),
                                       np.asarray(h["wind_direction_10m"], float),
                                       window_h=48.0), index=wt)
    j = pd.concat({"lsofs": l, "buoy": b}, axis=1).dropna()
    j["favwindrun"] = [run.asof(t) for t in j.index]
    j["doy"] = j.index.dayofyear
    j["depth"] = depth
    j["station"] = station
    j["year"] = year
    out = REPO / "data" / "calib" / f"{station}_{year}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    j.reset_index(names="t").to_parquet(out, index=False)
    print(f"wrote {len(j)} rows -> {out}  (depth {depth}m, "
          f"buoy[{j.buoy.min():.1f},{j.buoy.max():.1f}] lsofs bias {(j.lsofs-j.buoy).mean():+.2f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], int(sys.argv[2])))
