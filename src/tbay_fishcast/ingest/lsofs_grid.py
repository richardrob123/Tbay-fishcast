"""LSOFS grid bootstrap — nearest FVCOM node per station (PLAN Phase 0 task 1).

Reads the static grid (lon/lat/h/siglay) from any LSOFS file, builds a KDTree in a
local equirectangular projection, and resolves the nearest WATER node to each
Superior-shore station. Node index + node depth get pinned into stations.yaml so
every backtest samples the same node (reproducibility; ADR-013 spirit).

The grid is static across cycles, so this runs once (or when NOAA re-grids LSOFS).
Pure numpy/scipy + one netCDF read; no LLM.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

EARTH_R_M = 6_371_000.0


@dataclass(frozen=True)
class Grid:
    lon: np.ndarray  # (node,)
    lat: np.ndarray  # (node,)
    h: np.ndarray  # (node,) bathymetry, m, positive down
    siglay: np.ndarray  # (siglay, node) negative sigma fractions

    @property
    def n_nodes(self) -> int:
        return self.lon.size


@dataclass(frozen=True)
class NodeMatch:
    node: int
    dist_m: float
    node_depth_m: float
    node_lon: float
    node_lat: float


def read_grid(dataset) -> Grid:
    """Extract the static grid from an open netCDF4 Dataset (or DAP handle)."""
    return Grid(
        lon=np.asarray(dataset.variables["lon"][:], dtype=float),
        lat=np.asarray(dataset.variables["lat"][:], dtype=float),
        h=np.asarray(dataset.variables["h"][:], dtype=float),
        siglay=np.asarray(dataset.variables["siglay"][:], dtype=float),
    )


def _local_xy(lon, lat, lon0, lat0):
    """Equirectangular projection to metres around (lon0, lat0). Fine for a bay.

    LSOFS stores longitude in 0-360 convention; stations are in -180..180. The
    longitude delta is wrapped to [-180, 180] so either convention works.
    """
    lat0r = np.radians(lat0)
    dlon = (np.asarray(lon, dtype=float) - lon0 + 180.0) % 360.0 - 180.0
    x = np.radians(dlon) * np.cos(lat0r) * EARTH_R_M
    y = np.radians(np.asarray(lat) - lat0) * EARTH_R_M
    return x, y


def nearest_node(grid: Grid, lat: float, lon: float, min_depth_m: float = 0.5) -> NodeMatch:
    """Nearest node to (lat, lon) among nodes with depth >= min_depth_m (water).

    min_depth_m screens out dried/land nodes (h down to 0.1 m exist in the grid).
    """
    water = grid.h >= min_depth_m
    if not np.any(water):
        raise ValueError("no water nodes above min_depth_m")
    idx_water = np.flatnonzero(water)
    gx, gy = _local_xy(grid.lon[water], grid.lat[water], lon, lat)
    tree = cKDTree(np.column_stack([gx, gy]))
    dist, j = tree.query([0.0, 0.0])
    node = int(idx_water[j])
    return NodeMatch(
        node=node,
        dist_m=float(dist),
        node_depth_m=float(grid.h[node]),
        node_lon=float(grid.lon[node]),
        node_lat=float(grid.lat[node]),
    )


def bootstrap_stations(grid: Grid, stations, min_depth_m: float = 0.5) -> dict[str, NodeMatch]:
    """Resolve nearest nodes for every Superior-shore station.

    stations: iterable of objects with .id, .lat, .lon, .lsofs_shore.
    Returns {station_id: NodeMatch} for shore stations only.
    """
    out: dict[str, NodeMatch] = {}
    for s in stations:
        if not getattr(s, "lsofs_shore", False):
            continue
        out[s.id] = nearest_node(grid, s.lat, s.lon, min_depth_m=min_depth_m)
    return out
