"""GLERL Lake Superior mooring — the offshore stratification-climatology prior.

The single multi-year, full-depth *observed* temperature profile available for open
Lake Superior: NOAA/GLERL's 2018-2020 mooring at 47.13 N, -86.87 W (~46 mi N of Rock
River MI), 21 depths from 5.8 m to 202 m, hourly. Everything else offshore is model
(LSOFS FVCOM) or satellite skin (GLSEA/Landsat). This is the one deep *in-water* record.

It is NOT live and NOT nearshore, so it never enters the heartbeat. Its role is a
committed SEASONAL PRIOR: for a given time of year, the observed offshore 12 C-isotherm
depth and mixed-layer temperature, from two years of real profiles. It (a) gives an
observation-grounded plausibility envelope for LSOFS's offshore thermocline depth, and
(b) corroborates ERA5-FLake's mixed-layer-depth prior with actual multi-year profiles —
a second independent, and *observed*, opinion on where the offshore thermocline sits.

`build_mooring_climatology.py` reads the NetCDF once (offline) and writes the compact,
committed JSON this module serves. No LLM (ADR-001).

    Source: NOAA GLERL, Lake Superior temperature mooring 2018-2020
    (NCEI accession 0220860). Public domain (US Government work).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..features.cross_shore import isotherm_depth

MOORING_LAT, MOORING_LON = 47.12733, -86.87163
NCEI_URL = ("https://www.ncei.noaa.gov/data/oceans/archive/arc0162/0220860/2.2/"
            "data/0-data/glerl_lake_superior_temperature_mooring_2018_2020.nc")
TARGET_C = 12.0
_CLIM = Path(__file__).resolve().parents[3] / "knowledge" / "mooring_superior_climatology.json"


@dataclass(frozen=True)
class OffshorePrior:
    """Observed offshore stratification for a half-month period."""
    period: str            # e.g. "07b" = second half of July
    iso12_depth_m: float | None
    surface_c: float
    mixed_layer_c: float   # mean of the top three sensors (~5.8-10.7 m)
    n_profiles: int
    source: str = "GLERL Lake Superior mooring 2018-2020 (NCEI 0220860)"


def period_of(d: date) -> str:
    """Half-month key: '<MM>a' for days 1-15, '<MM>b' for 16-end."""
    return f"{d.month:02d}{'a' if d.day <= 15 else 'b'}"


def parse_mooring(path: str | Path):
    """(depths_m, times, temps[time, z]) from the GLERL mooring NetCDF. Needs xarray."""
    import numpy as np
    import xarray as xr

    with xr.open_dataset(path) as ds:
        depths = np.asarray(ds["z"].values, dtype=float)
        temps = np.asarray(ds["sea_water_temperature"].values, dtype=float)  # (time, z)
        times = ds["time"].values
    return depths, times, temps


def build_climatology(path: str | Path, *, target_c: float = TARGET_C) -> dict:
    """Half-month climatology of the observed offshore column. Offline builder only."""
    import numpy as np

    depths, times, temps = parse_mooring(path)
    # times may be numpy datetime64 or cftime objects; both expose the calendar fields we need.
    def _md(t):
        if hasattr(t, "month"):                       # cftime
            return t.month, t.day
        t = np.datetime64(t, "D").astype("datetime64[D]").item()
        return t.month, t.day
    keys = np.array([f"{m:02d}{'a' if d <= 15 else 'b'}" for m, d in (_md(t) for t in times)])
    top3 = np.argsort(depths)[:3]
    out: dict[str, dict] = {}
    for k in sorted(set(keys)):
        rows = temps[keys == k]
        prof = np.nanmean(rows, axis=0)
        ok = np.isfinite(prof)
        if ok.sum() < 3:
            continue
        z, t = depths[ok], prof[ok]
        # A real thermocline needs the column to actually cross target_c. An all-cold
        # (isothermal winter) column has no 12 C isotherm — report None, not the surface.
        iso = isotherm_depth(z, t, target_c) if np.nanmax(t) >= target_c else None
        out[k] = {
            "iso12_depth_m": round(iso, 2) if iso is not None else None,
            "surface_c": round(float(t[np.argmin(z)]), 2),
            "mixed_layer_c": round(float(np.nanmean(prof[top3])), 2),
            "n_profiles": int(rows.shape[0]),
            "depths_m": [round(float(x), 1) for x in z],
            "mean_temp_c": [round(float(x), 2) for x in t],
        }
    return {
        "lat": MOORING_LAT, "lon": MOORING_LON, "target_c": target_c,
        "source": "GLERL Lake Superior mooring 2018-2020 (NCEI 0220860)",
        "url": NCEI_URL, "periods": out,
    }


def load_climatology(path: str | Path | None = None) -> dict:
    p = Path(path) if path else _CLIM
    return json.loads(p.read_text())


def prior_for_day(d: date, clim: dict | None = None) -> OffshorePrior | None:
    """Observed offshore stratification prior for the half-month containing `d`.

    Falls back to the nearest populated half-month if the exact period is unsampled
    (the mooring is a 2-year record, so a few winter periods can be missing)."""
    clim = clim or load_climatology()
    periods = clim["periods"]
    key = period_of(d)
    if key not in periods:
        if not periods:
            return None
        order = sorted(periods, key=lambda k: abs(int(k[:2]) * 2 + (k[2] == "b")
                                                   - (int(key[:2]) * 2 + (key[2] == "b"))))
        key = order[0]
    p = periods[key]
    return OffshorePrior(
        period=key, iso12_depth_m=p["iso12_depth_m"], surface_c=p["surface_c"],
        mixed_layer_c=p["mixed_layer_c"], n_profiles=p["n_profiles"],
    )
