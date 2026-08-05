"""2-D reachability — the one true "is the cold water castable?" test.

The 1-D transect (cross_shore.depth_to_distance) walks a single offshore bearing and
misses shoals and points off to the side. The 2-D map instead asks, over the whole
NONNA depth patch: is there ANY water pixel where the bottom is at/below the target
isotherm depth (so cold water sits on the bottom) AND that pixel is within cast range
of shore? That is the correct test, and the operator's whole point about points and
shoals. This module is that test, factored out so the map and the forecast use ONE
implementation (they were drifting: the forecast said "out of range" where the map
said "reachable").

Pure and deterministic (no network, no LLM). Depth is positive-down metres with NaN
for land/nodata; res_ground_m is the pixel size in real ground metres.
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import distance_transform_edt


def shore_distance_m(depth: np.ndarray, res_ground_m: float) -> np.ndarray:
    """Distance-from-shore field (ground metres) — Euclidean distance from any water
    pixel to the nearest land/nodata pixel. Time-invariant, so compute once."""
    water = np.isfinite(depth)
    return distance_transform_edt(water) * res_ground_m


def reachable_mask(depth: np.ndarray, dist: np.ndarray, iso_depth_m: float | None,
                   cast_range_m: float) -> np.ndarray:
    """Boolean grid of pixels with cold water on the bottom AND within cast range."""
    if iso_depth_m is None:
        return np.zeros(depth.shape, dtype=bool)
    water = np.isfinite(depth)
    return water & (depth >= iso_depth_m) & (dist <= cast_range_m)


def reachability(depth: np.ndarray, dist: np.ndarray, iso_depth_m: float | None,
                 cast_range_m: float, res_ground_m: float):
    """(reachable, closest_dist_m, area_m2) for a given isotherm depth.

    Cold water sits on the bottom wherever the bottom is at least as deep as the
    isotherm (depth >= iso_depth_m). Reachable = such a pixel within cast range.
    Returns (False, None, 0.0) if the isotherm is absent or nothing is in range.
    """
    mask = reachable_mask(depth, dist, iso_depth_m, cast_range_m)
    if not mask.any():
        return False, None, 0.0
    return True, float(dist[mask].min()), float(mask.sum()) * res_ground_m * res_ground_m
