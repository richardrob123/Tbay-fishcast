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


def _merc_to_lonlat(x: float, y: float) -> tuple[float, float]:
    return (math.degrees(x / _R),
            math.degrees(2.0 * math.atan(math.exp(y / _R)) - math.pi / 2.0))


def _rdp(pts, eps):
    """Ramer-Douglas-Peucker simplification (iterative) — collapses the pixel-grid
    staircase of a raw contour into a few straight segments."""
    n = len(pts)
    if n < 3:
        return pts
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        x0, y0 = pts[i]; xj, yj = pts[j]
        dx, dy = xj - x0, yj - y0
        seg = math.hypot(dx, dy) or 1.0
        dmax, idx = 0.0, -1
        for k in range(i + 1, j):
            xk, yk = pts[k]
            d = abs(dy * xk - dx * yk + xj * y0 - yj * x0) / seg
            if d > dmax:
                dmax, idx = d, k
        if dmax > eps and idx != -1:
            keep[idx] = True
            stack.append((i, idx)); stack.append((idx, j))
    return [p for p, k in zip(pts, keep) if k]


def _chaikin(pts, iters=2):
    """Chaikin corner-cutting — rounds the simplified polyline into a smooth curve."""
    for _ in range(iters):
        if len(pts) < 3:
            break
        out = [pts[0]]
        for a, b in zip(pts[:-1], pts[1:]):
            out.append((a[0] * 0.75 + b[0] * 0.25, a[1] * 0.75 + b[1] * 0.25))
            out.append((a[0] * 0.25 + b[0] * 0.75, a[1] * 0.25 + b[1] * 0.75))
        out.append(pts[-1])
        pts = out
    return pts


def _chaikin_closed(pts, iters=2):
    """Chaikin corner-cutting for a CLOSED ring (wraps around, stays closed)."""
    p = list(pts[:-1]) if len(pts) > 1 and pts[0] == pts[-1] else list(pts)
    for _ in range(iters):
        n = len(p)
        if n < 3:
            break
        out = []
        for i in range(n):
            a, b = p[i], p[(i + 1) % n]
            out.append((a[0] * 0.75 + b[0] * 0.25, a[1] * 0.75 + b[1] * 0.25))
            out.append((a[0] * 0.25 + b[0] * 0.75, a[1] * 0.25 + b[1] * 0.75))
        p = out
    p.append(p[0])
    return p


def reachable_area_features(reachable_mask: np.ndarray, bounds_3857, res_m: float, *,
                            min_area_m2: float = 900.0, simplify_m: float = 14.0,
                            smooth_iters: int = 2):
    """Polygonize the reachable-cold mask into smoothed lon/lat polygons.

    Vector, so the green area (like the red line) stays crisp at any zoom instead of
    pixelating as a raster overlay does. rasterio.features.shapes traces the mask ->
    shapely simplifies (topology-preserving Douglas-Peucker) -> Chaikin rounds the
    pixel staircase. Polygons below `min_area_m2` are dropped. Returns a list of
    polygons, each a list of rings (exterior first, then holes), each ring a list of
    [lon, lat].
    """
    from rasterio.features import shapes
    from shapely.geometry import shape as _shape

    ny, nx = reachable_mask.shape
    x0, y0, x1, y1 = bounds_3857
    m = reachable_mask.astype(np.uint8)

    def px_to_lonlat(col, row):
        mx = x0 + (col / nx) * (x1 - x0)          # rasterio traces pixel edges (0..nx)
        my = y1 - (row / ny) * (y1 - y0)          # row 0 = top = y1
        return list(_merc_to_lonlat(mx, my))

    tol_px = simplify_m / max(res_m, 1e-6)
    out = []
    for geom, val in shapes(m, mask=m.astype(bool)):
        if val != 1:
            continue
        g = _shape(geom)
        if g.area * res_m * res_m < min_area_m2:  # area is in pixel^2 -> * res^2 = m^2
            continue
        g = g.simplify(tol_px, preserve_topology=True)
        if g.is_empty:
            continue
        rings = []
        for ring in [g.exterior, *g.interiors]:
            pts = _chaikin_closed(list(ring.coords), smooth_iters)
            rings.append([px_to_lonlat(c, r) for c, r in pts])
        out.append(rings)
    return out


def isobath_line_features(depth: np.ndarray, iso_field: np.ndarray, dist: np.ndarray,
                          bounds_3857, *, line_band_m: float = 250.0,
                          min_len_m: float = 90.0):
    """The 12 C line as VECTOR polylines (lon/lat), for a MapLibre line layer.

    A vector line stays a crisp constant-width stroke at any zoom — unlike a raster
    overlay, which turns every pixel into a block when you zoom to the marina. We
    contour `depth - iso_field` at 0 (where the target isotherm meets the bottom),
    confined to `line_band_m` of shore, then DROP polylines shorter than `min_len_m`
    (the little closed loops around isolated deep pockets and interpolation noise that
    read as stray red specks). Returns a list of [[lon, lat], ...] paths.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ny, nx = depth.shape
    water = np.isfinite(depth)
    field = np.where(water & (dist <= line_band_m), depth - iso_field, np.nan)
    fig = plt.figure()
    try:
        cs = fig.add_subplot().contour(field, levels=[0.0])
        segs = list(cs.allsegs[0]) if cs.allsegs else []
    except Exception:  # noqa: BLE001
        segs = []
    finally:
        plt.close(fig)

    x0, y0, x1, y1 = bounds_3857          # row 0 = top = y1; col 0 = left = x0
    res_x = abs(x1 - x0) / (nx - 1)        # mercator metres per pixel
    out = []
    for seg in segs:
        if len(seg) < 2:
            continue
        xs = x0 + seg[:, 0] / (nx - 1) * (x1 - x0)
        ys = y1 - seg[:, 1] / (ny - 1) * (y1 - y0)
        length = float(np.hypot(np.diff(xs), np.diff(ys)).sum())
        if length < min_len_m:
            continue
        pts = list(zip(xs.tolist(), ys.tolist()))
        pts = _rdp(pts, eps=2.2 * res_x)   # collapse the ~1 px staircase into straight runs
        pts = _chaikin(pts, iters=3)       # round the corners into a smooth curve
        out.append([list(_merc_to_lonlat(x, y)) for x, y in pts])
    return out


def cold_front_features(depth: np.ndarray, iso_field: np.ndarray, reachable: np.ndarray,
                        bounds_3857, *, min_len_m: float = 35.0):
    """The 12 C front as VECTOR polylines — the interface between the reachable-cold
    band (green) and the warm shallow water just inshore of it.

    Deriving the line from the SAME mask that makes the green area is what keeps the two
    coherent: red is exactly green's warm-facing inshore edge, so it runs continuously
    along the green and is never present where there is no green (nor green without it),
    unlike an independently-contoured/length-filtered isobath. We build a field that is
    1 on green, 0 on warm water, NaN everywhere else (land, and the cold water offshore
    of the cast/depth limit) and contour it at 0.5. matplotlib draws a segment only
    across a quad whose corners are all non-NaN, i.e. only between green and warm cells:
    the green/land edge (the shoreline) and the green/deep-water edge (the offshore cast
    limit) are NOT thermal fronts and correctly produce no line. Dust shorter than
    `min_len_m` is dropped. Returns a list of [[lon, lat], ...] paths.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ny, nx = depth.shape
    water = np.isfinite(depth)
    warm = water & (depth < iso_field)
    field = np.full(depth.shape, np.nan)
    field[warm] = 0.0
    field[reachable] = 1.0                 # green wins where a cell is both (it isn't)
    fig = plt.figure()
    try:
        cs = fig.add_subplot().contour(field, levels=[0.5])
        segs = list(cs.allsegs[0]) if cs.allsegs else []
    except Exception:  # noqa: BLE001
        segs = []
    finally:
        plt.close(fig)

    x0, y0, x1, y1 = bounds_3857
    res_x = abs(x1 - x0) / (nx - 1)
    out = []
    for seg in segs:
        if len(seg) < 2:
            continue
        xs = x0 + seg[:, 0] / (nx - 1) * (x1 - x0)
        ys = y1 - seg[:, 1] / (ny - 1) * (y1 - y0)
        length = float(np.hypot(np.diff(xs), np.diff(ys)).sum())
        if length < min_len_m:
            continue
        pts = list(zip(xs.tolist(), ys.tolist()))
        pts = _rdp(pts, eps=2.2 * res_x)   # collapse the ~1 px staircase into straight runs
        pts = _chaikin(pts, iters=3)       # round the corners into a smooth curve
        out.append([list(_merc_to_lonlat(x, y)) for x, y in pts])
    return out


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
