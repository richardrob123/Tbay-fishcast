"""Wind-run features for upwelling — the INDEPENDENT physical driver (PLAN task 4).

West-quadrant wind (FROM ~225-315°) drives offshore Ekman transport on Thunder Bay's
city/north shore → upwelling (seed corpus; Wedderburn W<1 at ~12-17 kt sustained).
Wind is used to CORROBORATE/classify events (did favorable wind precede this event?),
never inside the LSOFS-vs-GLSEA agreement itself — so it cannot contaminate the gate.

Pure functions over ERA5 hourly wind (era5_wind.fetch_wind). No LLM.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

FAVORABLE_SECTOR = (225.0, 315.0)  # degrees FROM which wind blows (SW..NW = west quadrant)

# numpy 2.0 renamed trapz -> trapezoid; support both.
_trapz = getattr(np, "trapezoid", None) or np.trapz


def in_sector(dir_deg, sector=FAVORABLE_SECTOR) -> np.ndarray:
    """Boolean mask: wind direction within [lo, hi] (handles wrap past 360)."""
    d = np.asarray(dir_deg, dtype=float) % 360.0
    lo, hi = sector
    if lo <= hi:
        return (d >= lo) & (d <= hi)
    return (d >= lo) | (d <= hi)  # wrapped sector


def favorable_wind_run(times, speed_kn, dir_deg, *, window_h: float = 24.0,
                       sector=FAVORABLE_SECTOR) -> np.ndarray:
    """Trailing favorable wind-run integral (kt·h) at each time: sum of speed over the
    trailing window_h for hours whose direction is in the favorable sector.

    A large trailing favorable wind-run is the physical precondition for upwelling.
    Returns an array aligned to the sorted input times.
    """
    t = np.asarray([np.datetime64(_naive(x), "s") for x in times])
    v = np.asarray(speed_kn, dtype=float)
    dirs = np.asarray(dir_deg, dtype=float)
    if not (t.size == v.size == dirs.size):
        raise ValueError("times/speed/dir length mismatch")
    order = np.argsort(t)
    t, v, dirs = t[order], v[order], dirs[order]
    hours = (t - t[0]) / np.timedelta64(1, "h")
    fav = in_sector(dirs, sector)
    vfav = np.where(fav, v, 0.0)
    out = np.zeros(t.size)
    for i in range(t.size):
        w = (hours >= hours[i] - window_h) & (hours <= hours[i])
        # trapezoidal-ish: mean favorable speed x window hours actually present
        idx = np.flatnonzero(w)
        if idx.size >= 2:
            out[i] = float(_trapz(vfav[idx], hours[idx]))
        else:
            out[i] = 0.0
    return out


def wind_consistent(onset: datetime, times, speed_kn, dir_deg, *,
                    lead_h: float = 24.0, run_threshold: float = 120.0,
                    sector=FAVORABLE_SECTOR) -> bool:
    """Was there sufficient favorable wind-run in the lead_h before `onset`?

    run_threshold in kt·h (e.g. ~12 kt sustained over ~10 h ≈ 120). Used to label an
    event 'wind-consistent' for the corroboration subset — not a gate.
    """
    onset64 = np.datetime64(_naive(onset), "s")
    t = np.asarray([np.datetime64(_naive(x), "s") for x in times])
    v = np.asarray(speed_kn, dtype=float)
    dirs = np.asarray(dir_deg, dtype=float)
    lead = onset64 - np.timedelta64(int(lead_h * 3600), "s")
    w = (t >= lead) & (t <= onset64)
    if not w.any():
        return False
    fav = in_sector(dirs[w], sector)
    hrs = (t[w] - t[w][0]) / np.timedelta64(1, "h")
    vfav = np.where(fav, v[w], 0.0)
    run = float(_trapz(vfav, hrs)) if hrs.size >= 2 else 0.0
    return run >= run_threshold


def _naive(x):
    if isinstance(x, datetime) and x.tzinfo is not None:
        return x.astimezone(timezone.utc).replace(tzinfo=None)
    return x
