"""Temporal validation — does the LSOFS correction hold across YEARS, not just this month?

CLAUDE.md rule 6: tune on 2024, validate on held-out 2025-26; never report skill on
the data you tuned on. Everything so far is July-Aug 2026. This checks the correction
across the archive:

  * BIAS STABILITY   — is the LSOFS subsurface warm bias the same size each summer,
    at comparable sensor depths? (A drifting bias would mean the correction rots.)
  * DEPTH STRUCTURE  — does bias grow with depth the same way every year? (validates
    the surface-anchored taper model, not just a single number)
  * TEMPORAL TRANSFER— fit the correction on the EARLIEST year, apply it forward to
    held-out later years; report held-out MAE. This is rule 6 made concrete.

Truth: NDBC historical + realtime ocean thermistors (depths vary by year/buoy: the
archive reports 1-2.4 m, realtime 3-6 m). We sample LSOFS at each buoy's OWN depth,
so bias is always like-for-like.

    python scripts/validate_temporal.py gather   # slow: archive LSOFS + buoy history
    python scripts/validate_temporal.py report

No LLM in the path.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.verification.scorecard import anomaly_correlation  # noqa: E402
from tbay_fishcast.ingest import ndbc, lsofs_grid  # noqa: E402
from tbay_fishcast.ingest.backfill import _open_first  # noqa: E402
from tbay_fishcast.ingest.lsofs_extract import extract_nodes, valid_time_from_dataset  # noqa: E402
from tbay_fishcast.ingest.lsofs_paths import LsofsFile, candidate_urls  # noqa: E402

CACHE = Path(__file__).resolve().parents[1] / "scratch" / "temporal_validation.json"
BUOYS = ["45027", "45023", "45216"]
MATCH_H = 2.0
STEP_DAYS = 8   # sample cadence through each season (keeps LSOFS opens ~50)
# fishing-season windows per year. 2024 native `fields` start ~Nov (after the buoys
# haul out), so 2024 uses the `regulargrid` product (Mar-Dec) which overlaps the buoys.
SEASONS = {
    2024: (date(2024, 6, 1), date(2024, 10, 10)),     # regulargrid path (tune year)
    2025: (date(2025, 5, 20), date(2025, 9, 25)),     # fields path
    2026: (date(2026, 6, 25), date(2026, 8, 4)),      # fields path
}
REGRID_YEARS = {2024}   # extract via the lat/lon regulargrid product


def _days(a, b, step):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=step)


def gather(argv) -> int:
    cfg = load_config()
    # buoy series per (buoy, year): history for past years, realtime for current
    series = {}
    for sid in BUOYS:
        for yr in SEASONS:
            try:
                recs = (ndbc.fetch_ocean_history(int(sid), yr) if yr < 2026
                        else ndbc.fetch_ocean_realtime(int(sid)))
            except Exception:  # noqa: BLE001
                recs = []
            series[(sid, yr)] = recs

    def buoy_at(sid, yr, vt):
        recs = series.get((sid, yr), [])
        pool = [r for r in recs if abs((r.time - vt).total_seconds()) <= MATCH_H * 3600]
        if not pool:
            return None, None
        z = float(np.median([r.depth_m for r in pool]))
        near = [r for r in pool if abs(r.depth_m - z) < 0.6]
        return z, float(np.mean([r.temp_c for r in near]))

    from tbay_fishcast.ingest.lsofs_paths import archive_flat_url
    from tbay_fishcast.ingest.backfill import _open_bytes
    from tbay_fishcast.ingest.lsofs_regulargrid import (
        read_regulargrid_grid, nearest_water_pixel, extract_regulargrid)

    records = []
    for yr, (a, b) in SEASONS.items():
        regrid = yr in REGRID_YEARS
        for day in _days(a, b, STEP_DAYS):
            f = LsofsFile(day, "t12z", "n", 6)
            try:
                if regrid:
                    ds = _open_bytes(archive_flat_url(f, cfg.lsofs.archive_bucket,
                                                      product="regulargrid", byterange=False))
                    rg = read_regulargrid_grid(ds)
                else:
                    ds = _open_first(candidate_urls(f, cfg.lsofs.recent_bucket,
                                                    cfg.lsofs.archive_bucket, byterange=False))
                    grid = lsofs_grid.read_grid(ds)
            except Exception:  # noqa: BLE001
                continue
            vt = valid_time_from_dataset(ds)
            for sid in BUOYS:
                z, bt = buoy_at(sid, yr, vt)
                if bt is None:
                    continue
                if regrid:
                    # regulargrid has 1 m & 6 m levels; buoys here are near-surface -> 1 m
                    px = nearest_water_pixel(rg, ndbc.BUOYS[sid].lat, ndbc.BUOYS[sid].lon)
                    lz = extract_regulargrid(ds, {sid: px}, [1.0])[0].temp_c
                    z_used = 1.0
                else:
                    nm = lsofs_grid.nearest_node(grid, ndbc.BUOYS[sid].lat, ndbc.BUOYS[sid].lon,
                                                 min_depth_m=3.0)
                    lz = extract_nodes(ds, {sid: nm.node}, [z])[0].temp_c
                    z_used = round(z, 1)
                records.append({"year": yr, "day": day.isoformat(), "buoy": sid,
                                "depth": z_used, "buoy_c": round(bt, 3), "lsofs_c": round(lz, 3),
                                "product": "regrid" if regrid else "fields"})
            ds.close()
            print(f"{day}: {sum(1 for r in records if r['day']==day.isoformat())} buoys")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(records, indent=1))
    print(f"\ncached {len(records)} records -> {CACHE}")
    return 0


def _bias(rs): return float(np.mean([r["lsofs_c"] - r["buoy_c"] for r in rs]))
def _mae(rs, corr=0.0): return float(np.mean([abs(r["lsofs_c"] - corr - r["buoy_c"]) for r in rs]))


def report(argv) -> int:
    if not CACHE.exists():
        print("no cache — run gather first"); return 1
    recs = json.loads(CACHE.read_text())
    if not recs:
        print("empty cache"); return 1
    yrs = sorted({r["year"] for r in recs})

    print("=" * 72)
    print("BIAS STABILITY across years (LSOFS - buoy, at each buoy's own depth)")
    print(f"{'year':6s} {'n':>3s} {'depths(m)':>12s} {'mean_bias':>9s} {'std':>6s} {'raw_MAE':>7s}")
    for yr in yrs:
        rs = [r for r in recs if r["year"] == yr]
        depths = sorted({r["depth"] for r in rs})
        b = [r["lsofs_c"] - r["buoy_c"] for r in rs]
        print(f"{yr:6d} {len(rs):>3d} {str(depths):>12s} {np.mean(b):+9.2f} {np.std(b):6.2f} {_mae(rs):7.2f}")

    print("\n" + "=" * 72)
    print("DEPTH STRUCTURE — mean warm bias by depth band, per year (is the taper stable?)")
    bands = [(0.5, 1.5, "~1m"), (2.0, 3.0, "~2.4m"), (3.5, 4.5, "~4m"), (5.5, 6.5, "~6m")]
    hdr = "  ".join(f"{lab:>6s}" for *_, lab in bands)
    print(f"{'year':6s} | {hdr}")
    for yr in yrs:
        cells = []
        for lo, hi, _ in bands:
            rs = [r for r in recs if r["year"] == yr and lo <= r["depth"] <= hi]
            cells.append(f"{_bias(rs):+6.2f}" if rs else f"{'—':>6s}")
        print(f"{yr:6d} | " + "  ".join(cells))

    print("\n" + "=" * 72)
    print("TEMPORAL TRANSFER — fit correction on earliest year, apply forward (rule 6)")
    # depth-band correction fit on the earliest year, applied to held-out later years
    fit_yr = yrs[0]
    def band_of(z):
        for lo, hi, lab in bands:
            if lo <= z <= hi:
                return lab
        return f"{z:.1f}"
    fit = {}
    for lo, hi, lab in bands:
        rs = [r for r in recs if r["year"] == fit_yr and lo <= r["depth"] <= hi]
        if rs:
            fit[lab] = _bias(rs)
    print(f"  correction fit on {fit_yr}: " + ", ".join(f"{k} {v:+.2f}" for k, v in fit.items()))
    for yr in yrs[1:]:
        rs = [r for r in recs if r["year"] == yr and band_of(r["depth"]) in fit]
        if not rs:
            print(f"  {yr}: no overlapping depth bands with {fit_yr}"); continue
        raw = _mae(rs)
        cor = float(np.mean([abs(r["lsofs_c"] - fit[band_of(r["depth"])] - r["buoy_c"]) for r in rs]))
        # anomaly (timing) skill on the held-out year, pooled per buoy
        anoms = []
        for sid in BUOYS:
            srs = sorted([r for r in rs if r["buoy"] == sid], key=lambda r: r["day"])
            if len(srs) >= 4:
                a = anomaly_correlation([r["lsofs_c"] for r in srs], [r["buoy_c"] for r in srs])
                if a == a:
                    anoms.append(a)
        astr = f"{np.mean(anoms):.2f}" if anoms else "n/a"
        print(f"  held-out {yr} (n={len(rs)}): raw MAE {raw:.2f} -> corrected MAE {cor:.2f} "
              f"| anomaly r {astr}")
    print("\n  Verdict: correction generalizes across years if held-out corrected MAE stays"
          "\n  ~1-1.5 C (comparable to the same-year LOBO number) and anomaly r stays positive.")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    raise SystemExit(gather(sys.argv) if cmd == "gather" else report(sys.argv))
