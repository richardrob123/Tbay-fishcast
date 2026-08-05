"""LEGIT validation: LSOFS vs in-situ NDBC buoy water temperature (G1 + G2 at a real
Lake Superior upwelling shore). This is the independent SUBSURFACE truth GLSEA could
not provide — a real thermistor, not a smoothed satellite composite.

For a buoy + year:
  * fetch the buoy's in-situ temp series at its deploy depth (NDBC .ocean);
  * extract LSOFS at the buoy's location and SAME depth, 6-hourly, over Jun15-Sep30
    (regulargrid for 2024 = exact z-levels; native fields for 2025+);
  * G1: MAE/bias/RMSE/r of LSOFS vs the buoy;
  * G2: detect upwelling events (persistence-guarded 4C/48h) in BOTH series and match
    them -> real POD/FAR against in-situ truth.

    python scripts/validate_buoy.py 45027 2025

Same detector/matcher as the Thunder Bay G2 (verification/g2.py) — only the truth
changes from satellite to in-situ.
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

import fsspec
import netCDF4 as nc
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.ingest.lsofs_extract import extract_nodes  # noqa: E402
from tbay_fishcast.ingest.lsofs_grid import nearest_node, read_grid  # noqa: E402
from tbay_fishcast.ingest.lsofs_paths import (  # noqa: E402
    LsofsFile, archive_flat_url, candidate_urls,
)
from tbay_fishcast.ingest.lsofs_regulargrid import (  # noqa: E402
    extract_regulargrid, nearest_water_pixel, read_regulargrid_grid,
)
from tbay_fishcast.ingest.ndbc import BUOYS, fetch_ocean_history, fetch_ocean_realtime  # noqa: E402
from tbay_fishcast.verification.g2 import (  # noqa: E402
    cluster_episodes, detect_onsets, match_episodes,
)
from tbay_fishcast.verification.scorecard import Contingency, temperature_error  # noqa: E402

_FS = fsspec.filesystem("https", block_size=16_000_000)
DROP_C, WINDOW_H, PERSIST_H = 4.0, 48.0, 12.0


def _open(url):
    return nc.Dataset("m", "r", memory=_FS.cat_file(url))


def _season(year):
    d, end = date(year, 6, 15), date(year, 9, 30)
    while d <= end:
        yield d
        d += timedelta(days=1)


def lsofs_series(cfg, buoy, year, depth_m):
    """6-hourly LSOFS temp at the buoy location/depth over the season."""
    use_regrid = year <= 2024
    if use_regrid:
        seed = archive_flat_url(LsofsFile(date(2024, 8, 15), "t12z", "n", 6),
                                cfg.lsofs.archive_bucket, "regulargrid", byterange=False)
        ds = _open(seed); grid = read_regulargrid_grid(ds); ds.close()
        pix = nearest_water_pixel(grid, buoy.lat, buoy.lon)
        print(f"  regulargrid pixel dist={pix.dist_km:.2f}km h={pix.depth_m:.1f}m")
        pixels = {buoy.station: pix}
    else:
        # bootstrap nearest fields node from a recent file
        f = LsofsFile(date(year, 8, 1), "t12z", "n", 6)
        ds = _open(candidate_urls(f, cfg.lsofs.recent_bucket, cfg.lsofs.archive_bucket,
                                  byterange=False)[1]); grid = read_grid(ds); ds.close()
        nm = nearest_node(grid, buoy.lat, buoy.lon, min_depth_m=depth_m)
        print(f"  fields node={nm.node} dist={nm.dist_m:.0f}m h={nm.node_depth_m:.1f}m")
        nodes = {buoy.station: nm.node}

    def one(day, cyc):
        f = LsofsFile(day, cyc, "n", 6)
        if use_regrid:
            url = archive_flat_url(f, cfg.lsofs.archive_bucket, "regulargrid", byterange=False)
            ds = _open(url)
            try:
                return extract_regulargrid(ds, pixels, [depth_m])
            finally:
                ds.close()
        ds = _open(candidate_urls(f, cfg.lsofs.recent_bucket, cfg.lsofs.archive_bucket,
                                  byterange=False)[1] if day < date(2026, 7, 6)
                   else candidate_urls(f, cfg.lsofs.recent_bucket, cfg.lsofs.archive_bucket,
                                       byterange=False)[0])
        try:
            return extract_nodes(ds, nodes, [depth_m])
        finally:
            ds.close()

    rows, miss = [], 0
    items = [(d, c) for d in _season(year) for c in cfg.lsofs.cycles]
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = {ex.submit(one, d, c): (d, c) for d, c in items}
        for fut in as_completed(futs):
            try:
                rows.extend(fut.result())
            except Exception:  # noqa: BLE001
                miss += 1
    df = pd.DataFrame([{"t": r.valid_time, "temp": r.temp_c} for r in rows])
    df = df.dropna().sort_values("t").drop_duplicates("t")
    print(f"  LSOFS: {len(df)} pts ({miss} misses)")
    return df.set_index("t")["temp"]


def buoy_series(station, year, target_depth=1.0):
    recs = fetch_ocean_history(station, year)
    if not recs:
        recs = fetch_ocean_realtime(station)
    df = pd.DataFrame([{"t": r.time, "depth": r.depth_m, "temp": r.temp_c} for r in recs])
    if df.empty:
        return None, None
    df["t"] = pd.to_datetime(df["t"], utc=True)
    depth = float(df["depth"].mode().iloc[0])
    s = df[df["depth"] == depth]
    s = s[(s.t >= pd.Timestamp(f"{year}-06-15", tz="UTC")) &
          (s.t <= pd.Timestamp(f"{year}-09-30", tz="UTC"))]
    ser = s.set_index("t")["temp"].resample("6h").mean().dropna()
    return ser, depth


def events(series):
    t = list(series.index.to_pydatetime())
    y = series.to_numpy()
    return cluster_episodes(detect_onsets(t, y, drop_c=DROP_C, persist_h=PERSIST_H,
                                          window_h=WINDOW_H), 1)


def main(station: str, year: int) -> int:
    cfg = load_config()
    buoy = BUOYS[station]
    print(f"=== VALIDATE LSOFS vs NDBC {station} ({buoy.name}) {year} ===")
    b, depth = buoy_series(station, year)
    if b is None:
        print("no buoy data"); return 1
    print(f"  buoy: {len(b)} 6h pts at {depth} m, range [{b.min():.1f},{b.max():.1f}]C")
    l = lsofs_series(cfg, buoy, year, depth)

    j = pd.concat({"lsofs": l, "buoy": b}, axis=1).dropna()
    print(f"  matched: {len(j)} pairs")
    st = temperature_error(j["lsofs"].to_numpy(), j["buoy"].to_numpy(),
                           depth_caveat=f"in-situ {depth} m (real thermistor)")
    print(f"\n  G1 @ {depth} m vs IN-SITU: MAE={st.mae:.2f} bias={st.bias:+.2f} "
          f"RMSE={st.rmse:.2f} r={st.pearson_r:+.2f} n={st.n}  "
          f"[G1<=2.0: {'PASS' if st.mae <= 2.0 else 'FAIL'}]")
    # decomposition: seasonal envelope vs sub-daily event timing
    mm = j.groupby(j.index.month).mean().round(1)
    print(f"  monthly means  lsofs={mm['lsofs'].to_dict()}")
    print(f"                 buoy ={mm['buoy'].to_dict()}")
    daily = j.resample("1D").mean().dropna()
    ds = temperature_error(daily["lsofs"].to_numpy(), daily["buoy"].to_numpy())
    dm = j.resample("30D").mean().dropna()  # coarse seasonal
    seas = temperature_error(dm["lsofs"].to_numpy(), dm["buoy"].to_numpy())
    print(f"  DAILY-smoothed MAE={ds.mae:.2f} r={ds.pearson_r:+.2f} | "
          f"std lsofs={j['lsofs'].std():.2f} buoy={j['buoy'].std():.2f} | "
          f"seasonal(30d) MAE={seas.mae:.2f}")

    # --- DECISION METRIC (ADR-006): skill vs climatology, on DETRENDED anomalies ---
    # Both series trivially rise through summer; climatology predicts that for free.
    # LSOFS earns its keep only if it predicts the DEVIATION from the seasonal normal.
    # Seasonal normal = 15-day centered rolling mean (per series); anomaly = value - normal.
    win = 60  # 6-hourly samples over 15 days
    la = (j["lsofs"] - j["lsofs"].rolling(win, center=True, min_periods=win // 2).mean())
    ba = (j["buoy"] - j["buoy"].rolling(win, center=True, min_periods=win // 2).mean())
    an = pd.concat({"l": la, "b": ba}, axis=1).dropna()
    anom_r = float(np.corrcoef(an["l"], an["b"])[0, 1]) if len(an) > 5 else float("nan")
    # climatology baseline = predict zero anomaly (i.e. the seasonal normal itself).
    clim_mae = float(np.mean(np.abs(an["b"])))             # error of "always normal"
    lsofs_anom_mae = float(np.mean(np.abs(an["l"] - an["b"])))  # error of LSOFS's anomaly call
    skill = 1.0 - lsofs_anom_mae / clim_mae if clim_mae > 0 else float("nan")
    print(f"\n  ANOMALY SKILL (the decision metric, detrended):")
    print(f"    corr(LSOFS anomaly, buoy anomaly) = {anom_r:+.2f}  (this is the REAL skill,"
          f" not the {st.pearson_r:+.2f} inflated by the shared seasonal cycle)")
    print(f"    climatology MAE (always-normal) = {clim_mae:.2f}C | LSOFS-anomaly MAE = "
          f"{lsofs_anom_mae:.2f}C | SKILL SCORE = {skill:+.2f}  "
          f"[{'beats climatology' if skill > 0 else 'WORSE than climatology'}]")

    e_l, e_b = events(j["lsofs"]), events(j["buoy"])
    m = match_episodes(e_b, e_l, tau_days=1)
    c = Contingency(m.hits, m.misses, m.false_alarms, correct_neg=0)
    print(f"\n  G2 events: LSOFS detected {len(e_l)}, buoy truth {len(e_b)} | "
          f"hits={m.hits} miss={m.misses} FA={m.false_alarms}")
    pod = f"{c.pod:.2f}" if not np.isnan(c.pod) else "n/a"
    far = f"{c.far:.2f}" if not np.isnan(c.far) else "n/a"
    print(f"  G2 POD={pod} FAR={far}  [POD>=0.7 & FAR<=0.3: "
          f"{'PASS' if (not np.isnan(c.pod) and c.pod>=0.7 and not np.isnan(c.far) and c.far<=0.3) else 'see numbers'}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], int(sys.argv[2])))
