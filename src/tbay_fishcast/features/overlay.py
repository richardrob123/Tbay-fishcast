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
from scipy.ndimage import binary_dilation, binary_opening, distance_transform_edt, label

_R = 6378137.0


def merc(lat: float, lon: float) -> tuple[float, float]:
    """Lat/lon -> Web-Mercator (EPSG:3857) metres."""
    return (math.radians(lon) * _R,
            math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * _R)


def land_shore_distance(depth: np.ndarray, res_m: float, *, shallow_m: float = 2.5,
                        min_land_px: int = 12, mainland_only: bool = True):
    """Distance-to-shore (ground metres) keeping only GENUINE, walk-to land as shore.

    A NaN component counts as land only if it (a) borders water shallower than
    `shallow_m` (real coast reaches ~0 m; offshore no-data holes border deep water)
    and (b) is at least `min_land_px` pixels (drops 1-3 px no-data specks that
    otherwise fake tiny offshore islands and spawn reachable speckle around them).

    With `mainland_only` (default), the shore is further restricted to land that
    reaches the image border — i.e. the connected coast a person can walk. Isolated
    offshore islands, breakwaters, and shoals in the harbour interior are dropped, so
    the map shows cold water reachable FROM the shore, not around mid-lake structures
    or NONNA tile-seam/no-data-boundary artifacts. Returns (dist_m, shore_mask).
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
    if mainland_only and land.any():
        clbl, _ = label(land)
        border = (set(clbl[0, :]) | set(clbl[-1, :]) | set(clbl[:, 0]) | set(clbl[:, -1]))
        border.discard(0)
        if border:                                   # keep only coast reaching the edge
            land = np.isin(clbl, list(border))
    # remove thin land tendrils (NONNA tile-seam / no-data slivers that reach into the
    # lake). Opening erodes then dilates, so the solid coast is preserved (edge restored)
    # but 1-2 px slivers vanish — killing the linear "reachable" streaks they seed.
    if land.any():
        land = binary_opening(land, iterations=1)
    return distance_transform_edt(~land) * res_m, land


def cold_line_reachable(depth: np.ndarray, iso_field: np.ndarray, dist: np.ndarray, *,
                        cast_m: float = 75.0, max_reach_depth_m: float = 22.0,
                        min_reach_px: int = 20, line_band_m: float = 300.0):
    """(cold, line, reachable) boolean masks for one isotherm field.

    `dist` is distance to the (mainland) shore. Both products are confined to the
    nearshore so harbour-interior structures and NONNA artifacts don't render:

    cold      = water at/below the isotherm depth (cold water sits on the bottom).
    line      = the target-isotherm contour within `line_band_m` of shore: cold pixels
                bordering shallow WARM water that connects to shore (the real drop-off
                edge), at any strip width. Not drawn where cold merely runs to shore,
                nor at the offshore/no-data edge, nor around interpolation pinholes,
                nor out in the lake interior.
    reachable = cold within a cast of shore AND shallow enough to shore-fish, tiny
                isolated specks removed.
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
    line = cold & binary_dilation(shore_warm, iterations=1) & (dist <= line_band_m)
    # the raw contour is 1 px wide, so on diagonals it staircases into dots. Thicken
    # it to a continuous stroke (kept in water so it doesn't bleed onto land).
    line = binary_dilation(line, iterations=2) & water & (dist <= line_band_m)

    reachable = cold & (dist <= cast_m) & (depth <= max_reach_depth_m)
    rl, rn = label(reachable)
    clean = np.zeros_like(reachable)
    for c in range(1, rn + 1):
        comp = rl == c
        if comp.sum() >= min_reach_px:
            clean |= comp
    return cold, line, clean
