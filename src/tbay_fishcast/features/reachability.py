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
    """NAIVE distance-from-shore field — Euclidean distance from any water pixel to the
    nearest land/nodata pixel.

    WARNING (AUDIT_ROUND3): NONNA marks unsurveyed WATER as NaN too, so this treats
    survey seams and no-data aprons as coastline and can fabricate "castable shore" in
    open water (measured 65-90% artifact area at some stations). Production paths must
    use `corrected_fields` below (the map pipeline); this stays only as its last-resort
    degraded fallback, and any consumer of the naive field must say so."""
    water = np.isfinite(depth)
    return distance_transform_edt(water) * res_ground_m


def corrected_fields(depth: np.ndarray, bounds_3857, res_ground_m: float):
    """The ONE corrected (depth, shore-distance, degraded?) pipeline — shared by the
    map and the station/pin verdicts so they cannot re-diverge (AUDIT_ROUND3: they had —
    pins were using the naive NaN=shore transform the map had to abandon).

    Steps: fill mid-water survey seams; use the satellite basemap to tell unsurveyed
    water from land; bridge narrow unsurveyed nearshore gaps; distance from MAINLAND
    shore only. Degrades to the naive field (degraded=True) if imagery is unavailable —
    callers must surface that flag."""
    from ..ingest import basemap
    from .overlay import (bridge_survey_gaps, fill_unsurveyed_water,
                          imagery_unsurveyed_water, land_shore_distance)

    depth = fill_unsurveyed_water(depth, res_ground_m)
    not_land = None
    degraded = False
    try:
        rgb = basemap.fetch_imagery_3857(bounds_3857, size_px=depth.shape[0])
        unsurv = imagery_unsurveyed_water(depth, rgb.mean(axis=2))
        depth, not_land = bridge_survey_gaps(depth, unsurv, res_ground_m)
    except Exception:  # noqa: BLE001
        degraded = True
    dist, _land = land_shore_distance(depth, res_ground_m, not_land=not_land)
    return depth, dist, degraded


def reachable_mask(depth: np.ndarray, dist: np.ndarray, iso_depth_m: float | None,
                   cast_range_m: float, max_depth_m: float | None = None) -> np.ndarray:
    """Boolean grid: cold water on the bottom, within cast range, and (if `max_depth_m`)
    shallow enough to actually present a lure on the bottom. The map layer caps at the
    product's max_reach_depth_m — pass the same cap here or pins and map diverge."""
    if iso_depth_m is None:
        return np.zeros(depth.shape, dtype=bool)
    water = np.isfinite(depth)
    m = water & (depth >= iso_depth_m) & (dist <= cast_range_m)
    if max_depth_m is not None:
        m &= depth <= max_depth_m
    return m


def reachability(depth: np.ndarray, dist: np.ndarray, iso_depth_m: float | None,
                 cast_range_m: float, res_ground_m: float,
                 max_depth_m: float | None = None):
    """(reachable, closest_dist_m, area_m2) for a given isotherm depth.

    Cold water sits on the bottom wherever the bottom is at least as deep as the
    isotherm (depth >= iso_depth_m). Reachable = such a pixel within cast range
    (and within `max_depth_m` if given — keep in lockstep with the map's cap).
    Returns (False, None, 0.0) if the isotherm is absent or nothing is in range.
    """
    mask = reachable_mask(depth, dist, iso_depth_m, cast_range_m, max_depth_m)
    if not mask.any():
        return False, None, 0.0
    return True, float(dist[mask].min()), float(mask.sum()) * res_ground_m * res_ground_m
