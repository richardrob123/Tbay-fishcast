"""LSOFS `regulargrid` reader — lat/lon gridded product (PLAN task 2, G2 tune season).

Distinct from the native-`fields` node reader. Verified live 2026-08-05 against
lsofs.t12z.20240815.regulargrid.n006.nc:

  * temp(time, Depth, ny, nx), °C, _FillValue -99999.0
  * Depth = 27 standard z-levels; **6.0 m is an EXACT level (index 4)** — no sigma
    interpolation needed on the tune side (contrast the fields reader's interp_column).
  * Latitude/Longitude are 2-D (ny,nx) but separable; longitude is **-180..180**
    (do NOT apply the fields 0-360 wrap). mask ∈ {0,1} (1 = water). h = bathymetry.
  * ~0.005° grid (~0.5 km); nx=1555, ny=529.

This is the ONLY nowcast source for the 2024 ice-free tune season (flat archive
months carry regulargrid nowcast; native fields nowcast starts ~2024-11). Pure
numpy; no LLM. Returns the same TempRow bronze shape as the fields extractor so the
downstream event pipeline is source-agnostic.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .lsofs_extract import TempRow, valid_time_from_dataset

FILL = -99999.0
EARTH_R_KM = 6371.0


@dataclass(frozen=True)
class RegularGrid:
    lat1d: np.ndarray  # (ny,) latitude down columns
    lon1d: np.ndarray  # (nx,) longitude across rows (-180..180)
    mask: np.ndarray   # (ny, nx) 1=water
    depth: np.ndarray  # (nz,) z-levels, m
    h: np.ndarray      # (ny, nx) bathymetry, m


@dataclass(frozen=True)
class PixelMatch:
    iy: int
    ix: int
    dist_km: float
    pixel_lat: float
    pixel_lon: float
    depth_m: float  # bathymetry h at the pixel


def read_regulargrid_grid(ds) -> RegularGrid:
    lat = np.asarray(ds.variables["Latitude"][:], dtype=float)
    lon = np.asarray(ds.variables["Longitude"][:], dtype=float)
    lat1d = lat[:, 0] if lat.ndim == 2 else lat
    lon1d = lon[0, :] if lon.ndim == 2 else lon
    mask = np.asarray(ds.variables["mask"][:], dtype=float)
    if mask.ndim == 3:  # (time, ny, nx)
        mask = mask[0]
    h = np.asarray(ds.variables["h"][:], dtype=float)
    if h.ndim == 3:
        h = h[0]
    return RegularGrid(lat1d=lat1d, lon1d=lon1d, mask=mask,
                       depth=np.asarray(ds.variables["Depth"][:], dtype=float), h=h)


def depth_index(grid: RegularGrid, target_m: float, *, exact_tol: float = 1e-6) -> tuple[int, bool]:
    """Index of the z-level for `target_m`. Returns (index, is_exact)."""
    diffs = np.abs(grid.depth - target_m)
    idx = int(np.argmin(diffs))
    return idx, bool(diffs[idx] <= exact_tol)


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = np.radians(lat2 - lat1), np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return float(2 * EARTH_R_KM * np.arcsin(np.sqrt(a)))


def nearest_water_pixel(grid: RegularGrid, lat: float, lon: float,
                        max_radius: int = 12) -> PixelMatch:
    """Nearest WATER pixel (mask==1) to (lat, lon). Starts at the separable nearest
    index and spirals outward until a water pixel is found (coastal points snap to the
    nearest offshore water cell — same reality as the fields nodes and GLSEA pixels)."""
    iy0 = int(np.argmin(np.abs(grid.lat1d - lat)))
    ix0 = int(np.argmin(np.abs(grid.lon1d - lon)))
    ny, nx = grid.mask.shape
    best = None
    for r in range(max_radius + 1):
        y0, y1 = max(0, iy0 - r), min(ny - 1, iy0 + r)
        x0, x1 = max(0, ix0 - r), min(nx - 1, ix0 + r)
        for iy in range(y0, y1 + 1):
            for ix in range(x0, x1 + 1):
                if r > 0 and (y0 < iy < y1) and (x0 < ix < x1):
                    continue  # only the expanding ring, interior already tested
                if grid.mask[iy, ix] != 1:
                    continue
                d = _haversine_km(lat, lon, grid.lat1d[iy], grid.lon1d[ix])
                if best is None or d < best.dist_km:
                    best = PixelMatch(iy, ix, d, float(grid.lat1d[iy]),
                                      float(grid.lon1d[ix]), float(grid.h[iy, ix]))
        if best is not None:
            return best
    raise ValueError(f"no water pixel within {max_radius} cells of ({lat},{lon})")


def bootstrap_pixels(grid: RegularGrid, stations) -> dict[str, PixelMatch]:
    """Nearest water pixel per Superior-shore station (uses node coords if pinned,
    else the station coords)."""
    out: dict[str, PixelMatch] = {}
    for s in stations:
        if not getattr(s, "lsofs_shore", False):
            continue
        lat = s.node_lat if getattr(s, "node_lat", None) is not None else s.lat
        lon = s.node_lon if getattr(s, "node_lon", None) is not None else s.lon
        out[s.id] = nearest_water_pixel(grid, lat, lon)
    return out


def extract_regulargrid(ds, station_pixels: dict[str, PixelMatch],
                        target_depths_m) -> list[TempRow]:
    """Temperature at each station pixel and target depth (exact z-level or nearest).

    Skips a (station, depth) with a fill value at that pixel/level (returns no row for
    it) rather than emitting junk — the caller sees a gap, not a -99999.
    """
    grid = read_regulargrid_grid(ds)
    vt = valid_time_from_dataset(ds)
    targets = np.atleast_1d(np.asarray(target_depths_m, dtype=float))
    temp = ds.variables["temp"]
    rows: list[TempRow] = []
    for sid, px in station_pixels.items():
        for d in targets:
            zi, _exact = depth_index(grid, float(d))
            # filled(): masked/fill land elements -> nan (no masked-scalar warning)
            val = float(np.ma.filled(temp[0, zi, px.iy, px.ix], np.nan))
            if val == FILL or not np.isfinite(val):
                continue
            rows.append(TempRow(
                station_id=sid, node=px.iy * grid.mask.shape[1] + px.ix,
                depth_m=float(d), temp_c=val, valid_time=vt,
                water_column_m=px.depth_m, clamped=(float(d) > px.depth_m),
            ))
    return rows
