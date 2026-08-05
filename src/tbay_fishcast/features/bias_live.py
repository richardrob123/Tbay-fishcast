"""Live bias + upwelling context — the lean, matplotlib-free core the heartbeat needs.

`pooled_subsurface_bias` measures the LSOFS subsurface warm bias against the live NDBC
buoys (pooled over recent days, tight-time-matched, stale sensors dropped). It is the
correction the whole product hangs on, so it lives in a light module that the cron
heartbeat can import without pulling in matplotlib/rendering. No LLM.
"""
from __future__ import annotations

from datetime import date

import numpy as np

from ..features.wind import in_sector
from ..ingest import era5_wind, lsofs_grid, ndbc
from ..ingest.backfill import _open_first
from ..ingest.lsofs_extract import extract_nodes, valid_time_from_dataset
from ..ingest.lsofs_paths import LsofsFile, candidate_urls

BIAS_DAYS = 4     # pool the subsurface bias over this many recent days
MATCH_H = 1.5     # buoy within +/- this of model 12Z (tight, no smearing)


def pooled_subsurface_bias(cfg, day):
    """LSOFS subsurface warm bias pooled over BIAS_DAYS x 3 buoys, tight-time-matched.

    Returns (central, lo, hi, per_buoy_rows, n). Central = pooled median (robust);
    band = pooled mean ± 1σ — the day-to-day + spatial spread that becomes the isotherm
    band. Stale-sensor buoys (e.g. 45028, dead since June) drop out.
    """
    buoyz = {"45027": 6.0, "45023": 5.0, "45216": 3.0}
    series = {}
    for sid in buoyz:
        try:
            series[sid] = ndbc.fetch_ocean_realtime(int(sid))
        except Exception:  # noqa: BLE001
            series[sid] = []

    def buoy_at(sid, z, t):
        pool = [r for r in series[sid]
                if abs(r.depth_m - z) < 0.6 and abs((r.time - t).total_seconds()) <= MATCH_H * 3600]
        return float(np.mean([r.temp_c for r in pool])) if pool else None

    per = {sid: [] for sid in buoyz}
    days = [date.fromordinal(day.toordinal() - k) for k in range(BIAS_DAYS)]
    for d in days:
        f = LsofsFile(d, "t12z", "n", 6)
        try:
            ds = _open_first(candidate_urls(f, cfg.lsofs.recent_bucket,
                                            cfg.lsofs.archive_bucket, byterange=False))
        except Exception:  # noqa: BLE001
            continue
        grid = lsofs_grid.read_grid(ds)
        vt = valid_time_from_dataset(ds)
        for sid, z in buoyz.items():
            bt = buoy_at(sid, z, vt)
            if bt is None:
                continue
            nm = lsofs_grid.nearest_node(grid, ndbc.BUOYS[sid].lat, ndbc.BUOYS[sid].lon, min_depth_m=3.0)
            lz = extract_nodes(ds, {f"b{sid}": nm.node}, [z])[0].temp_c
            per[sid].append(lz - bt)
        ds.close()

    allb = [v for vals in per.values() for v in vals]
    rows = []
    for sid, z in buoyz.items():
        v = per[sid]
        rows.append((sid, z, (float(np.mean(v)) if v else None),
                     (float(np.std(v)) if v else None), len(v)))
    if not allb:
        return 0.0, 0.0, 0.0, rows, 0
    central = float(np.median(allb))
    mu, sd = float(np.mean(allb)), float(np.std(allb))
    return central, mu - sd, mu + sd, rows, len(allb)


def upwelling_context(day):
    """Recent W-quadrant wind at Thunder Bay: is upwelling being driven? (dict or None)."""
    try:
        w = era5_wind.fetch_wind((date.fromordinal(day.toordinal() - 4)).isoformat(), day.isoformat())
    except Exception:  # noqa: BLE001
        return None
    sp = np.asarray(w.get("wind_speed_10m") or [], dtype=float)
    dr = np.asarray(w.get("wind_direction_10m") or [], dtype=float)
    if not len(sp):
        return None
    fav = in_sector(dr)
    last48 = slice(-48, None)
    fav_h = int(fav[last48].sum())
    insec = sp[last48][fav[last48]]
    return {"fav_hours_48": fav_h, "mean_insector_kn": float(insec.mean()) if insec.size else 0.0,
            "max_kn_24": float(sp[-24:].max()) if len(sp) >= 24 else float(sp.max())}
