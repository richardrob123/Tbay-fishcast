"""ERA5 FLake lake column — the INDEPENDENT lake-physics cross-check (non-FVCOM).

The whole subsurface product rests on one model, LSOFS (NOAA FVCOM). DATA_AUDIT.md
round 2 named this the core limitation: "there is no second model to consume" — every
open subsurface source was either FVCOM-lineage (GLCFS), lake-masked (RTOFS), or ended
before the tune window. The one genuinely independent physics left open was **ERA5's
FLake** lake model (a 1-D two-layer lake scheme, ECMWF-forced, NOT FVCOM), which needs a
free ECMWF CDS key. With a key configured, this module pulls it — closing that gap.

Verified live 2026-08-06 via the CDS (`cds.climate.copernicus.eu`, dataset
`reanalysis-era5-single-levels`):
  * `lake_mix_layer_temperature` (K), `lake_mix_layer_depth` (m), `lake_bottom_temperature`
    (K). FLake resolves the whole Lake Superior grid (no masking) — 45/45 cells valid.
  * Grid **0.25° (~28 km)**, so each cell is a coarse open-lake average — it CANNOT
    resolve the nearshore embayment; it is a regional/offshore reference, not a live
    nearshore anchor. Latency **~5 days** (ERA5T), so `fetch_flake` walks back to the
    most recent available day.
  * Validated against NDBC buoys (2026-08-01): mixed-layer temp within ~1 °C of buoys
    that sit inside the mixed layer, and it independently reproduced the shallow mixed
    layer + cold bottom at the upwelling buoy 45027 — trustworthy, unlike MUR SST
    (~4 °C cold-biased in the lake, rejected).

Role: an independent QA cross-check on LSOFS's thermocline/mixed-layer DEPTH (the root of
the isotherm-depth error), and a seasonal mixed-layer-depth prior. NOT in the heartbeat
(network + CDS queue; ADR-001). Needs `~/.cdsapirc` (CDS url + key). No LLM.

  Licence: Copernicus / ECMWF ERA5 (accept once on the CDS site). Attribute
  "Generated using Copernicus Climate Change Service information [year]" in derivatives.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

CDS_DATASET = "reanalysis-era5-single-levels"
_VARS = ["lake_mix_layer_temperature", "lake_mix_layer_depth", "lake_bottom_temperature"]


@dataclass
class FlakeCell:
    lat: float
    lon: float
    mixed_layer_temp_c: float
    mixed_layer_depth_m: float
    bottom_temp_c: float
    day: str
    dist_km: float


def _k_to_c(x: float) -> float:
    return float(x) - 273.15 if x > 200.0 else float(x)


def parse_flake(path: str, lat: float, lon: float) -> FlakeCell | None:
    """Nearest-cell FlakeCell from an ERA5 single-levels netCDF (pure; unit-testable)."""
    import math

    import netCDF4  # noqa: N813
    import numpy as np

    d = netCDF4.Dataset(path)
    try:
        la = d.variables["latitude"][:]
        lo = d.variables["longitude"][:]
        i = int(np.argmin(np.abs(la - lat)))
        j = int(np.argmin(np.abs(lo - lon)))

        def at(name):
            # ma.filled: netCDF4 returns masked arrays; np.array() would pass raw
            # fill values (~9.97e36) through as finite "temperatures".
            arr = np.ma.filled(d.variables[name][:], np.nan).squeeze()
            return float(np.ravel(arr)[0]) if arr.ndim == 0 else float(arr[i, j])

        mt = _k_to_c(at("lmlt"))
        md = at("lmld")
        bt = _k_to_c(at("lblt"))
    finally:
        d.close()
    if not all(np.isfinite(v) for v in (mt, md, bt)):
        return None
    dist_km = math.hypot((float(la[i]) - lat) * 111.0,
                         (float(lo[j]) - lon) * 111.0 * math.cos(math.radians(lat)))
    return FlakeCell(float(la[i]), float(lo[j]), mt, md, bt, "", round(dist_km, 1))


def fetch_flake(lat: float, lon: float, day: date, *, max_back: int = 8,
                half_deg: float = 0.3) -> FlakeCell | None:
    """Most recent available ERA5 FLake cell at (lat, lon). Walks back over ERA5's
    ~5-day latency. Requires a configured CDS key (`~/.cdsapirc`). Returns None if the
    dataset can't be reached for any day in the window."""
    import os
    import tempfile

    import cdsapi

    client = cdsapi.Client(quiet=True, wait_until_complete=True)
    for k in range(max_back):
        d = day - timedelta(days=k)
        area = [lat + half_deg, lon - half_deg, lat - half_deg, lon + half_deg]
        fd, path = tempfile.mkstemp(suffix=".nc")
        os.close(fd)
        try:
            client.retrieve(CDS_DATASET, {
                "product_type": "reanalysis", "variable": _VARS,
                "year": f"{d.year:04d}", "month": f"{d.month:02d}", "day": f"{d.day:02d}",
                "time": "12:00", "area": area, "data_format": "netcdf",
            }, path)
        except Exception:  # noqa: BLE001  (day not yet in ERA5T — walk back)
            os.path.exists(path) and os.remove(path)
            continue
        try:
            cell = parse_flake(path, lat, lon)
        finally:
            os.path.exists(path) and os.remove(path)
        if cell is not None:
            cell.day = d.isoformat()
            return cell
    return None
