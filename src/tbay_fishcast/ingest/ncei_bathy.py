"""NCEI Great Lakes bathymetry — the coarse continuous fallback.

CHS NONNA-10 is the primary bathymetry (10 m, hydrographic-survey grade) but has
nearshore coverage holes (e.g. MacKenzie Point). Where NONNA is empty, fall back
to the NOAA/NCEI "Lake Superior morphometry" grid (superior_lld.grd, ~92 m,
whole-lake, continuous) so every spot still gets a transect — flagged as coarse.

The .grd is a NetCDF (x, y, z; z negative = depth below datum) ~165 MB — too big to
commit, and gitignored. This reader is OFFLINE-only (build_bathymetry.py); it emits
the same small profile dict as nonna.build_profile, which IS committed. Point the
reader at the grid with the NCEI_SUPERIOR_GRD env var or an explicit path.

    Source: NOAA National Centers for Environmental Information, Lake Superior
    bathymetry/morphometry. Public domain (US Government work).
"""
from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np

from . import nonna

_MLAT = 111_132.0  # metres per degree latitude


def default_grd_path() -> Path | None:
    env = os.environ.get("NCEI_SUPERIOR_GRD")
    if env and Path(env).exists():
        return Path(env)
    return None


def _nearest_index(arr: np.ndarray, value: float) -> int:
    return int(np.argmin(np.abs(arr - value)))


def build_profile(lat: float, lon: float, grd_path: str | Path, *,
                  bearing_deg: float | None = None, half_deg: float = 0.06,
                  max_n: int = 40) -> dict:
    """Cross-shore profile from the NCEI grid — same shape as nonna.build_profile."""
    from scipy.io import netcdf_file

    f = netcdf_file(str(grd_path), mmap=True)
    try:
        xv = f.variables["x"][:].copy()
        yv = f.variables["y"][:].copy()
        j0, j1 = sorted((_nearest_index(xv, lon - half_deg), _nearest_index(xv, lon + half_deg)))
        i0, i1 = sorted((_nearest_index(yv, lat - half_deg * 0.8),
                         _nearest_index(yv, lat + half_deg * 0.8)))
        sub = f.variables["z"][i0:i1, j0:j1].astype(float)
    finally:
        f.close()
    sublon, sublat = xv[j0:j1], yv[i0:i1]
    depth = np.where(sub < 0, -sub, np.nan)  # positive-down; land(>=0) -> NaN

    mlon = 111_320.0 * math.cos(math.radians(lat))
    dx = abs(sublon[1] - sublon[0]) * mlon if len(sublon) > 1 else 92.0
    dy = abs(sublat[1] - sublat[0]) * _MLAT if len(sublat) > 1 else 92.0
    res_g = (dx + dy) / 2.0

    pr, pc = _nearest_index(sublat, lat), _nearest_index(sublon, lon)
    snap = nonna.nearest_water_px(depth, pr, pc, max_radius=40)
    cov = float(np.isfinite(depth).mean())
    if snap is None:
        return {"lat": lat, "lon": lon, "bearing_deg": bearing_deg, "dist_m": [],
                "depth_m": [], "resolution_ground_m": round(res_g, 1),
                "coverage_frac": round(cov, 3), "snapped": False,
                "source": "NCEI Lake Superior grid (92 m)",
                "note": "no water pixel near pin even in NCEI grid"}
    sr, sc = snap
    brg = bearing_deg
    auto = nonna.offshore_bearing(depth, sr, sc, hint_deg=brg, hint_tol_deg=70.0)
    brg = auto if auto is not None else (brg if brg is not None else 90.0)
    dists, depths = nonna.walk_profile(depth, sr, sc, brg, res_ground_m=res_g, max_n=max_n)
    return {"lat": lat, "lon": lon, "bearing_deg": round(float(brg), 1),
            "dist_m": [round(d, 1) for d in dists],
            "depth_m": [round(h, 2) for h in depths],
            "resolution_ground_m": round(res_g, 1),
            "coverage_frac": round(cov, 3), "snapped": True,
            "source": "NCEI Lake Superior grid (~92 m, coarse fallback)",
            "note": f"COARSE fallback ({res_g:.0f} m grid) — NONNA-10 has no nearshore "
                    f"coverage here; bearing {brg:.0f}deg, {len(dists)} pts to {dists[-1]:.0f} m"}
