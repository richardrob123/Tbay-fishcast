"""Ordered shoreline polylines from the frozen water masks (ADR-046).

The product already commits its water masks (``data/watermask_frozen/``) so the coast is
deterministic across builds. Those masks are rasters, and a coloured shoreline needs a LINE — an
ordered sequence of points you can cut into runs and hand to MapLibre.

APPROACH: exact lattice boundary, not a smoothed contour. Every pair of 4-adjacent pixels where
one is water and one is land contributes the unit edge they share. Those unit edges ARE the
boundary of the water region — no interpolation, no marching-squares ambiguity, no extra
dependency — and chaining them by shared endpoints yields ordered polylines. Each edge also
remembers WHICH SIDE was land, which is what lets the classifier sample a point that is genuinely
on the bank rather than in the lake.

OVERLAPPING MASKS. The frozen set is a tile pyramid: several coarse masks are fully contained in
finer ones, and the 6 m tiles overlap each other by 13-40%. Tracing all of them would draw parts
of the coast twice, at two resolutions, with two independent classifications. :func:`trace_all`
therefore assigns each mask a priority (finest first, then by name for determinism) and drops any
edge whose land pixel falls inside a higher-priority mask's extent. Chains are cut at those tile
seams, which is harmless — a run boundary is invisible on a coloured line.
"""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

R_EARTH = 6378137.0


def merc_to_lonlat(mx: float, my: float) -> tuple[float, float]:
    """Inverse spherical WebMercator, closed form (avoids a pyproj dependency)."""
    lon = mx / R_EARTH * 180.0 / math.pi
    lat = (2.0 * math.atan(math.exp(my / R_EARTH)) - math.pi / 2.0) * 180.0 / math.pi
    return lon, lat


def ground_scale(my: float) -> float:
    """Ground metres per WebMercator metre at northing ``my`` (= cos(latitude)).

    WebMercator metres are NOT ground metres — at 48.4 deg N they are inflated by 1.51x. Sampling
    "every 60 m" without this correction silently samples every 90 m.
    """
    _, lat = merc_to_lonlat(0.0, my)
    return math.cos(math.radians(lat))


def load_mask(path):
    """Frozen mask -> (water_bool_2d, bounds). ``True`` is water; row 0 is the NORTH edge."""
    d = np.load(path)
    h, w = int(d["h"]), int(d["w"])
    m = np.unpackbits(d["packed"])[: h * w].reshape(h, w).astype(bool)
    return m, tuple(float(v) for v in d["bounds"])


def shore_edges(mask: np.ndarray):
    """Unit boundary edges of the water region.

    Returns ``(n0, n1, land_rc, normal)`` as parallel arrays over edges:
      * ``n0``/``n1`` — corner-lattice node ids, ``i*(w+1)+j``
      * ``land_rc``   — (row, col) of the LAND pixel on that edge
      * ``normal``    — (drow, dcol) unit step from the edge INTO the land pixel

    A node id is a lattice corner, so two edges that touch share an id exactly; chaining is
    integer-equality, never a float tolerance.
    """
    h, w = mask.shape
    stride = w + 1
    n0, n1, lr, lc, nr, nc = [], [], [], [], [], []

    # vertical edges: between (r, c) and (r, c+1); the shared edge runs from corner
    # (r, c+1) down to corner (r+1, c+1)
    diff = mask[:, :-1] ^ mask[:, 1:]
    rr, cc = np.nonzero(diff)
    if len(rr):
        left_is_land = ~mask[rr, cc]
        n0 += list(rr * stride + (cc + 1))
        n1 += list((rr + 1) * stride + (cc + 1))
        lr += list(rr)
        lc += list(np.where(left_is_land, cc, cc + 1))
        nr += [0] * len(rr)
        nc += list(np.where(left_is_land, -1, 1))

    # horizontal edges: between (r, c) and (r+1, c); shared edge runs from corner
    # (r+1, c) across to corner (r+1, c+1)
    diff = mask[:-1, :] ^ mask[1:, :]
    rr, cc = np.nonzero(diff)
    if len(rr):
        top_is_land = ~mask[rr, cc]
        n0 += list((rr + 1) * stride + cc)
        n1 += list((rr + 1) * stride + (cc + 1))
        lr += list(np.where(top_is_land, rr, rr + 1))
        lc += list(cc)
        nr += list(np.where(top_is_land, -1, 1))
        nc += [0] * len(rr)

    return (np.asarray(n0, dtype=np.int64), np.asarray(n1, dtype=np.int64),
            np.column_stack([lr, lc]).astype(np.int64) if lr else np.zeros((0, 2), np.int64),
            np.column_stack([nr, nc]).astype(np.int64) if nr else np.zeros((0, 2), np.int64))


def chain_edges(n0, n1, keep=None):
    """Chain unit edges into ordered polylines. Returns ``[[edge_idx, ...], ...]``.

    Walks each chain in both directions and STOPS at any lattice corner with more than two
    unvisited edges. Those degree-4 corners are diagonal water/land touches, where "carry
    straight on" is not defined by the raster; splitting there is the honest choice and costs
    nothing but an extra run boundary.
    """
    idx = range(len(n0)) if keep is None else [i for i in range(len(n0)) if keep[i]]
    adj = defaultdict(list)
    for i in idx:
        adj[int(n0[i])].append(i)
        adj[int(n1[i])].append(i)
    used = set()
    chains = []

    def walk(start_edge, node):
        """Follow edges from ``node`` away from ``start_edge`` while the path is unambiguous."""
        out = []
        cur_node = node
        while True:
            cand = [e for e in adj[cur_node] if e not in used]
            if len(cand) != 1:
                return out
            e = cand[0]
            used.add(e)
            out.append(e)
            cur_node = int(n1[e]) if int(n0[e]) == cur_node else int(n0[e])

    for i in idx:
        if i in used:
            continue
        used.add(i)
        fwd = walk(i, int(n1[i]))
        back = walk(i, int(n0[i]))
        chains.append(list(reversed(back)) + [i] + fwd)
    return chains


def chain_nodes(chain, n0, n1):
    """Edge chain -> ordered node ids (len = len(chain) + 1)."""
    if not chain:
        return []
    e0 = chain[0]
    if len(chain) == 1:
        return [int(n0[e0]), int(n1[e0])]
    e1 = chain[1]
    # orient the first edge so its far end is the one shared with the second edge
    shared = {int(n0[e1]), int(n1[e1])}
    start = int(n0[e0]) if int(n1[e0]) in shared else int(n1[e0])
    nodes = [start]
    cur = start
    for e in chain:
        cur = int(n1[e]) if int(n0[e]) == cur else int(n0[e])
        nodes.append(cur)
    return nodes


def node_xy(node: int, w: int, bounds) -> tuple[float, float]:
    x0, y0, x1, y1 = bounds
    i, j = divmod(int(node), w + 1)
    return x0 + j * ((x1 - x0) / w), y1 - i * ((y1 - y0) / w)


def pixel_xy(r: int, c: int, shape, bounds) -> tuple[float, float]:
    h, w = shape
    x0, y0, x1, y1 = bounds
    return x0 + (c + 0.5) * ((x1 - x0) / w), y1 - (r + 0.5) * ((y1 - y0) / h)


def trace_mask(mask: np.ndarray, bounds, keep_mask_fn=None):
    """Trace one mask into chains of ``(lon, lat)`` vertices plus per-edge bank samples.

    Yields dicts with:
      ``pts``     — [(lon, lat)] polyline vertices, one more than there are edges
      ``bank``    — [(lon, lat)] the land-pixel centre for each edge
      ``normal``  — [(dx_merc, dy_merc)] unit step from the edge into the land, in mercator axes
      ``seg_m``   — [float] ground length of each edge
    """
    h, w = mask.shape
    n0, n1, land_rc, normal = shore_edges(mask)
    if not len(n0):
        return []
    keep = None
    if keep_mask_fn is not None:
        keep = keep_mask_fn(land_rc)
    x0, y0, x1, y1 = bounds
    resx, resy = (x1 - x0) / w, (y1 - y0) / h
    scale = ground_scale((y0 + y1) / 2.0)
    out = []
    for chain in chain_edges(n0, n1, keep):
        nodes = chain_nodes(chain, n0, n1)
        pts = [merc_to_lonlat(*node_xy(n, w, bounds)) for n in nodes]
        bank, norms, seg = [], [], []
        for e in chain:
            r, c = int(land_rc[e][0]), int(land_rc[e][1])
            bank.append(merc_to_lonlat(*pixel_xy(r, c, mask.shape, bounds)))
            dr, dc = int(normal[e][0]), int(normal[e][1])
            norms.append((float(dc), float(-dr)))     # +row is south, so dy = -dr
            seg.append((resy if dc else resx) * scale)
        out.append({"pts": pts, "bank": bank, "normal": norms, "seg_m": seg})
    return out


def trace_all(paths):
    """Trace every frozen mask once, de-duplicating the overlapping tile pyramid.

    Masks are ordered finest-resolution-first (ties broken by name, so the result is byte-stable),
    and an edge is dropped when its land pixel falls inside the extent of a mask already traced.
    """
    metas = []
    for p in sorted(paths, key=lambda q: str(q)):
        m, b = load_mask(p)
        metas.append((str(getattr(p, "stem", p)), m, b, (b[2] - b[0]) / m.shape[1]))
    metas.sort(key=lambda t: (round(t[3], 3), t[0]))
    done_bounds = []
    result = []
    for name, m, b, _res in metas:
        h, w = m.shape
        x0, y0, x1, y1 = b
        resx, resy = (x1 - x0) / w, (y1 - y0) / h

        def covered(land_rc, _x0=x0, _y1=y1, _rx=resx, _ry=resy):
            if not done_bounds:
                return np.ones(len(land_rc), dtype=bool)
            mx = _x0 + (land_rc[:, 1] + 0.5) * _rx
            my = _y1 - (land_rc[:, 0] + 0.5) * _ry
            keep = np.ones(len(land_rc), dtype=bool)
            for bx0, by0, bx1, by1 in done_bounds:
                keep &= ~((mx >= bx0) & (mx <= bx1) & (my >= by0) & (my <= by1))
            return keep

        for ch in trace_mask(m, b, keep_mask_fn=covered):
            ch["mask"] = name
            result.append(ch)
        done_bounds.append(b)
    return result


def sample_positions(seg_m, step_m: float):
    """Edge indices to classify along a chain: one every ``step_m`` of ground distance.

    Always returns at least one index, so a chain shorter than the sampling interval (a small
    island) still gets classified rather than silently dropped.
    """
    n = len(seg_m)
    if n == 0:
        return []
    cum = np.cumsum(seg_m)
    total = float(cum[-1])
    if total <= step_m:
        return [n // 2]
    k = max(1, int(round(total / step_m)))
    targets = [(i + 0.5) * total / k for i in range(k)]
    return [int(np.searchsorted(cum, t, side="left")) for t in targets]


def runs_from_labels(labels):
    """[c, c, d, d, d] -> [(0, 2, c), (2, 5, d)]; half-open edge-index spans."""
    out = []
    if not labels:
        return out
    s = 0
    for i in range(1, len(labels) + 1):
        if i == len(labels) or labels[i] != labels[s]:
            out.append((s, i, labels[s]))
            s = i
    return out
