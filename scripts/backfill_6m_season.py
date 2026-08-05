"""Backfill the 6 m upwelling-season temperature series -> bronze parquet (PLAN task 2,
feeds G2). Persisted (addresses the audit's 'backfill never produced a dataset').

Source auto-routes by year (see docs/G2_PREREGISTRATION.md):
  * 2024 -> regulargrid (flat archive, 6 m EXACT z-level) — the tune season.
  * 2025+ -> native fields (recent + nested archive, 6 m sigma-interpolated) — validation.

Sampling: n006 of each cycle = a clean 6-hourly series (valid 00/06/12/18 UTC). The
upwelling signal (setup ~10 h, seiche ~40 h) is well-sampled at 6 h; this cuts the
file count ~7x vs all nowcast hours. Deduped by (station, valid_time).

    python scripts/backfill_6m_season.py 2024   # Jun 15 - Sep 30 of that year
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import fsspec
import netCDF4 as nc
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.ingest.backfill import (  # noqa: E402
    BackfillItem, extract_item, extract_regulargrid_item, station_node_map,
)
from tbay_fishcast.ingest.lsofs_paths import LsofsFile, archive_flat_url  # noqa: E402
from tbay_fishcast.ingest.lsofs_regulargrid import (  # noqa: E402
    bootstrap_pixels, read_regulargrid_grid,
)
from tbay_fishcast.storage.parquet_store import write_bronze  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
_FS = fsspec.filesystem("https", block_size=16_000_000)


def season_days(year: int) -> list[date]:
    d, end = date(year, 6, 15), date(year, 9, 30)
    out = []
    while d <= end:
        out.append(d); d += timedelta(days=1)
    return out


def main(year: int) -> int:
    cfg = load_config()
    nodes = station_node_map(cfg)
    use_regrid = year <= 2024
    pixels = None
    if use_regrid:
        seed = archive_flat_url(LsofsFile(date(2024, 8, 15), "t12z", "n", 6),
                                cfg.lsofs.archive_bucket, "regulargrid", byterange=False)
        ds = nc.Dataset("m", "r", memory=_FS.cat_file(seed))
        pixels = bootstrap_pixels(read_regulargrid_grid(ds), cfg.stations); ds.close()

    days = season_days(year)
    items = [BackfillItem(d, cyc, 6) for d in days for cyc in cfg.lsofs.cycles]  # n006
    print(f"6m season backfill {year} ({'regulargrid' if use_regrid else 'fields'}): "
          f"{len(days)} days, {len(items)} files")

    def one(it):
        if use_regrid:
            return extract_regulargrid_item(cfg, it, pixels, [6.0])
        return [r for r in extract_item(cfg, it, nodes) if r.depth_m == 6.0]

    rows, miss = [], 0
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = {ex.submit(one, it): it for it in items}
        for fut in as_completed(futs):
            try:
                rows.extend(fut.result())
            except Exception:  # noqa: BLE001
                miss += 1

    # dedup by (station, valid_time), keep last (all n006 -> identical anyway)
    df = pd.DataFrame([{"station_id": r.station_id, "valid_time": r.valid_time,
                        "temp_c": r.temp_c, "node": r.node,
                        "water_column_m": r.water_column_m} for r in rows])
    df = df.sort_values("valid_time").drop_duplicates(["station_id", "valid_time"], keep="last")
    out = REPO / "data" / "bronze" / "g2_6m" / f"year={year}" / "temps.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    print(f"  {miss} file misses; wrote {len(df)} rows -> {out}")
    # quick per-station coverage + range
    for sid, g in df.groupby("station_id"):
        print(f"    {sid:22s} n={len(g):4d}  6m range [{g.temp_c.min():.1f},{g.temp_c.max():.1f}]C")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1])))
