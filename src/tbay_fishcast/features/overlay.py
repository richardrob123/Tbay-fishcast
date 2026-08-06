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


def cold_reachable(depth: np.ndarray, iso_field: np.ndarray, dist: np.ndarray, *,
                   cast_m: float = 75.0, max_reach_depth_m: float = 22.0,
                   min_reach_px: int = 20):
    """(cold, reachable) boolean masks for one isotherm field.

    `dist` is distance to the (mainland) shore. cold = water at/below the isotherm
    depth (cold water sits on the bottom). reachable = cold within a cast of shore
    AND shallow enough to shore-fish, with tiny isolated specks removed (fake
    offshore "shore"). The 12 C LINE is rendered separately as a true contour
    (`isobath_line_rgba`) so complex bathymetry gives a thin line, not a dilated blob.
    """
    water = np.isfinite(depth)
    cold = water & (depth >= iso_field)
    reachable = cold & (dist <= cast_m) & (depth <= max_reach_depth_m)
    rl, rn = label(reachable)
    clean = np.zeros_like(reachable)
    for c in range(1, rn + 1):
        comp = rl == c
        if comp.sum() >= min_reach_px:
            clean |= comp
    return cold, clean


def isobath_line_rgba(depth: np.ndarray, iso_field: np.ndarray, dist: np.ndarray, *,
                      line_band_m: float = 300.0, color=(255, 30, 60), linewidth: float = 1.6):
    """The 12 C line rendered as a TRUE contour (constant width, follows the isobath).

    Contouring `depth - iso_field` at 0 traces exactly where the target isotherm meets
    the bottom. Rendered by matplotlib at a fixed line width, it stays a clean thin
    line even where the bottom is complex or near-flat at the isotherm depth — unlike a
    dilated pixel-boundary, which blobs up there. Confined to `line_band_m` of shore.
    Returns an (ny, nx, 4) uint8 RGBA with the line drawn (transparent elsewhere).
    matplotlib is imported lazily so this module stays import-light for pure use.
    """
    import io

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from PIL import Image

    ny, nx = depth.shape
    water = np.isfinite(depth)
    field = np.where(water & (dist <= line_band_m), depth - iso_field, np.nan)
    fig = plt.figure(figsize=(nx / 100.0, ny / 100.0), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_axis_off()
    ax.set_xlim(0, nx - 1); ax.set_ylim(ny - 1, 0)
    try:
        ax.contour(field, levels=[0.0], colors=[tuple(c / 255 for c in color)],
                   linewidths=linewidth)
    except Exception:  # noqa: BLE001  (no contour in this frame)
        pass
    # save TRANSPARENT so only the drawn line carries alpha (the canvas buffer would
    # otherwise be opaque white and wipe everything it's composited over).
    out = io.BytesIO()
    fig.savefig(out, format="png", dpi=100, transparent=True)
    plt.close(fig)
    out.seek(0)
    img = Image.open(out).convert("RGBA")
    if img.size != (nx, ny):
        img = img.resize((nx, ny), Image.NEAREST)
    return np.asarray(img).copy()
