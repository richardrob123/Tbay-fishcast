"""Deterministic map-overlay masks — the one place cold/line/reachable are defined.

Shared by the dynamic map and the hosted coast site so they cannot drift. Pure and
deterministic (no network, no LLM). Depth is positive-down metres with NaN for
land/nodata; res_m is the pixel size in real ground metres.

Two subtleties this module gets right, learned from real map artifacts:
  * NONNA marks BOTH land and unsurveyed deep water as NaN. A plain distance
    transform fakes a coastline at offshore no-data holes and spawns "reachable"
    cold water mid-lake. So a NaN component counts as shore only if it borders
    genuinely shallow water AND is big enough not to be a stray no-data speck.
  * On a saturated-cold day the whole shelf is below the isotherm, so the "12 C
    line" is the thin warm strip at the waterline. The line is therefore drawn at
    the cold/warm interface for warm water that CONNECTS TO SHORE, at any width —
    not via a size threshold that drops thin strips, and not at the offshore edge
    or interior interpolation pinholes.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.ndimage import binary_dilation, distance_transform_edt, label

_R = 6378137.0


def merc(lat: float, lon: float) -> tuple[float, float]:
    """Lat/lon -> Web-Mercator (EPSG:3857) metres."""
    return (math.radians(lon) * _R,
            math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * _R)


def land_shore_distance(depth: np.ndarray, res_m: float, *, shallow_m: float = 2.5,
                        min_land_px: int = 12):
    """Distance-to-shore (ground metres) keeping only GENUINE land as shore.

    A NaN component counts as land only if it (a) borders water shallower than
    `shallow_m` (real coast reaches ~0 m; offshore no-data holes border deep water)
    and (b) is at least `min_land_px` pixels (drops 1-3 px no-data specks that
    otherwise fake tiny offshore islands and spawn reachable speckle around them).
    Returns (dist_m, land_mask).
    """
    water = np.isfinite(depth)
    lbl, n = label(~water)
    land = np.zeros_like(water)
    for c in range(1, n + 1):
        comp = lbl == c
        if comp.sum() < min_land_px:
            continue
        ring = binary_dilation(comp) & water
        if ring.any() and float(np.nanmin(depth[ring])) < shallow_m:
            land |= comp
    return distance_transform_edt(~land) * res_m, land


def cold_line_reachable(depth: np.ndarray, iso_field: np.ndarray, dist: np.ndarray, *,
                        cast_m: float = 75.0, max_reach_depth_m: float = 22.0,
                        min_reach_px: int = 20):
    """(cold, line, reachable) boolean masks for one isotherm field.

    cold      = water at/below the isotherm depth (cold water sits on the bottom).
    line      = the target-isotherm contour: cold pixels bordering shallow WARM water
                that connects to shore (the real drop-off edge), at any strip width.
                Not drawn where cold merely runs to shore, nor at the offshore/no-data
                edge, nor around interior interpolation pinholes.
    reachable = cold within a cast of shore AND shallow enough to shore-fish, with
                tiny isolated specks removed (fake offshore "shore").
    """
    water = np.isfinite(depth)
    cold = water & (depth >= iso_field)
    warm = water & ~cold

    # warm water that connects to shore (the real nearshore shelf) vs deep pinholes
    lbl, n = label(warm)
    shore_warm = np.zeros_like(warm)
    for c in range(1, n + 1):
        comp = lbl == c
        if (binary_dilation(comp) & ~water).any():   # touches land / shore
            shore_warm |= comp
    line = cold & binary_dilation(shore_warm, iterations=1)

    reachable = cold & (dist <= cast_m) & (depth <= max_reach_depth_m)
    rl, rn = label(reachable)
    clean = np.zeros_like(reachable)
    for c in range(1, rn + 1):
        comp = rl == c
        if comp.sum() >= min_reach_px:
            clean |= comp
    return cold, line, clean
