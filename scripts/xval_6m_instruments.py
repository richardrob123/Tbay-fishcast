"""Cross-validate the two LSOFS 6 m instruments (PLAN task 2 / G2 pre-registration).

The G2 split tunes on 2024 (regulargrid, exact 6 m z-level) and validates on 2025-26
(native fields, 6 m sigma-interpolated). Those are two different instruments; before
trusting the handoff we must quantify their offset where BOTH exist. Flat month 202408
carries fields-nowcast AND regulargrid-nowcast at identical valid times — an ice-free
overlap. This reports, per station, mean(regulargrid - fields) and RMSE at 6 m.

    python scripts/xval_6m_instruments.py 2024-08-10 2024-08-16
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

import fsspec
import netCDF4 as nc
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.ingest.backfill import station_node_map  # noqa: E402
from tbay_fishcast.ingest.lsofs_extract import extract_nodes  # noqa: E402
from tbay_fishcast.ingest.lsofs_paths import LsofsFile, archive_flat_url  # noqa: E402
from tbay_fishcast.ingest.lsofs_regulargrid import (  # noqa: E402
    bootstrap_pixels, extract_regulargrid, read_regulargrid_grid,
)
from tbay_fishcast.verification.scorecard import temperature_error  # noqa: E402

_FS = fsspec.filesystem("https", block_size=16_000_000)


def _open(url):
    return nc.Dataset("m", "r", memory=_FS.cat_file(url))


def main(start: date, end: date) -> int:
    cfg = load_config()
    nodes = station_node_map(cfg)
    # bootstrap regulargrid pixels once
    seed = archive_flat_url(LsofsFile(date(2024, 8, 15), "t12z", "n", 6),
                            cfg.lsofs.archive_bucket, "regulargrid", byterange=False)
    ds = _open(seed); px = bootstrap_pixels(read_regulargrid_grid(ds), cfg.stations); ds.close()

    days = []
    d = start
    while d <= end:
        days.append(d); d += timedelta(days=1)
    items = [(dd, cyc, h) for dd in days for cyc in cfg.lsofs.cycles for h in range(7)]

    def one(item):
        dd, cyc, h = item
        f = LsofsFile(dd, cyc, "n", h)
        out = {}
        try:
            dsf = _open(archive_flat_url(f, cfg.lsofs.archive_bucket, "fields", byterange=False))
            try:
                for r in extract_nodes(dsf, nodes, [6.0]):
                    out[("fields", r.station_id, r.valid_time)] = r.temp_c
            finally:
                dsf.close()
            dsr = _open(archive_flat_url(f, cfg.lsofs.archive_bucket, "regulargrid", byterange=False))
            try:
                for r in extract_regulargrid(dsr, px, [6.0]):
                    out[("regrid", r.station_id, r.valid_time)] = r.temp_c
            finally:
                dsr.close()
        except Exception as e:  # noqa: BLE001
            return {"__err__": f"{item}: {type(e).__name__}"}
        return out

    merged, errs = {}, 0
    with ThreadPoolExecutor(max_workers=12) as ex:
        for fut in as_completed([ex.submit(one, it) for it in items]):
            r = fut.result()
            if "__err__" in r:
                errs += 1; continue
            merged.update(r)

    # pair fields vs regrid by (station, valid_time)
    pairs = {s.id: ([], []) for s in cfg.shore_stations}
    keys = {(sid, vt) for (src, sid, vt) in merged if src == "fields"}
    for (sid, vt) in keys:
        f = merged.get(("fields", sid, vt)); g = merged.get(("regrid", sid, vt))
        if f is not None and g is not None:
            pairs[sid][0].append(g); pairs[sid][1].append(f)  # (regrid, fields)

    print(f"6 m instrument cross-validation {start}..{end}  ({errs} file-pair errors)\n")
    print(f"{'station':22s} {'n':>4s} {'mean(rg-fld)':>12s} {'rmse':>6s} {'r':>6s}")
    allg, allf = [], []
    for sid, (g, f) in pairs.items():
        if not g:
            print(f"{sid:22s}    0"); continue
        allg += g; allf += f
        st = temperature_error(g, f)  # model=regrid, truth=fields -> bias = rg-fld
        print(f"{sid:22s} {st.n:4d} {st.bias:+12.3f} {st.rmse:6.3f} {st.pearson_r:+6.2f}")
    if allg:
        o = temperature_error(allg, allf)
        print(f"\n{'POOLED':22s} {o.n:4d} {o.bias:+12.3f} {o.rmse:6.3f} {o.pearson_r:+6.2f}")
        print(f"\n=> apply offset (fields = regulargrid - {o.bias:+.3f}) when handing the "
              f"2024 tune (regulargrid) to 2025-26 validation (fields), OR keep it as a "
              f"declared instrument uncertainty of ~{o.rmse:.2f} C.")
    return 0


if __name__ == "__main__":
    a = datetime.fromisoformat(sys.argv[1]).date()
    b = datetime.fromisoformat(sys.argv[2]).date()
    raise SystemExit(main(a, b))
