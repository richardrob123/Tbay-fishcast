"""Longitudinal river structure from lidar — pools, riffles, rapids and barriers (ADR-045).

WHY THIS IS MEASUREMENT AND NOT INFERENCE. Lidar does not penetrate water, so over a channel the
return is the WATER SURFACE, and water-surface slope is the physical definition of the thing we
want: a pool is flat water, a rapid is steep water, a barrier is a step. So this reads channel
character off a 1 m survey rather than guessing it from planform curvature. That matters for tier:
these reaches are T1 measured, the same standing as the NONNA bathymetry driving the lake map —
not the T3 "inferred from map shape" layer this feature could easily have been.

Two independent checks anchor the method, both of which the Current River passed on first run:
the profile's downstream end must land on Lake Superior's surface (measured 182.82 m against the
183.2 m datum), and known impoundments must fall out as flat runs without being told (Boulevard
Lake appeared as 1,460 m at 0.3 m of drop, with its dam as a 75 m/km step immediately below).

NO ARBITRARY THRESHOLDS. "Steep" and "flat" are not hardcoded numbers; they are percentiles of the
POOLED slope distribution measured across every river in the domain, exactly as ADR-039 anchors
the gold structure ramp to measured regional percentiles. If the region's rivers are gentle, the
bar for "rapid" moves with them, and the classification keeps meaning "unusual for HERE".

WHY BARRIERS ARE NOT SIMPLY EXCLUSIONS. It is tempting to treat steep water as unfishable and stop
there. For migratory fish the opposite is the useful reading: the first impassable step is where
the run STOPS AND STACKS UP, which is precisely why the North Shore Steelhead Association built a
fishway on the Current. A barrier is a destination, and the plunge pool below it is holding water.
The classifier therefore labels barriers, and separately labels the pool immediately downstream.

Pure functions over arrays; no I/O and no network here (the script does that), no LLM (ADR-001).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# Percentiles of the pooled regional slope distribution that define the reach classes. Chosen as
# distribution SHAPE, not as slope values: the edges themselves are measured per-region and stored
# in data/calib/river_reach_calib.json alongside the sample count that produced them.
FLAT_PCT = 25.0     # at or below this pooled percentile -> pool-like (holding water)
STEEP_PCT = 90.0    # at or above -> rapid-like (fast, broken water)
BARRIER_PCT = 99.0  # a step this steep is a candidate migration barrier

# A reach must persist to be real. Pool-riffle units in gravel-bed rivers scale with channel width
# (classically 5-7 widths between riffles), so the floor is expressed in CHANNEL WIDTHS and
# converted per-river using its own measured width — never a bare metre count.
MIN_REACH_WIDTHS = 2.0
DEFAULT_WIDTH_M = 25.0

# Slope is fitted over a window that also scales with width: too short and it reads bed noise,
# too long and it averages a rapid into its pool.
SLOPE_WINDOW_WIDTHS = 8.0


@dataclass
class Reach:
    """One classified stretch of river."""

    cls: str                 # pool | riffle | rapid | barrier
    start_m: float           # distance downstream from the profile head
    end_m: float
    z_start: float
    z_end: float
    slope_m_km: float        # mean over the reach
    lat: float = 0.0         # midpoint
    lon: float = 0.0
    meta: dict = field(default_factory=dict)

    @property
    def length_m(self) -> float:
        return self.end_m - self.start_m

    @property
    def drop_m(self) -> float:
        return self.z_start - self.z_end


def percentile(vals, pct: float) -> float:
    """Linear-interpolated percentile over a plain list (keeps this module numpy-free)."""
    v = sorted(x for x in vals if x is not None and math.isfinite(x))
    if not v:
        raise ValueError("no finite values")
    if len(v) == 1:
        return v[0]
    k = (len(v) - 1) * pct / 100.0
    lo = int(k)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def _seg_len(w):
    mlat = 111320.0
    mlon = 111320.0 * math.cos(math.radians(w[0][0]))
    return sum(math.hypot((b[0] - a[0]) * mlat, (b[1] - a[1]) * mlon)
               for a, b in zip(w, w[1:]))


def chain_ways(ways, *, snap_m: float = 8.0, mouth=None, mouth_tol_m: float = 400.0):
    """Order OSM ways into ONE downstream polyline — the longest connected run available.

    Three things make this harder than it looks, and the first build got all three wrong, which
    the datum check caught immediately (Current River stopped after 2.4 km at 267 m instead of
    running 14.3 km to the lake at 183 m):

      1. A way's own node order is meaningful, but its DIRECTION is not — OSM stores plenty of
         waterway segments pointing upstream. Both orientations have to be tried.
      2. A river's ways form several disconnected fragments (bridges, culverts and name changes
         break them). Seeding from the first way with a free head picks an arbitrary fragment, so
         every seed is tried and the LONGEST result wins.
      3. Endpoints rarely match to the last decimal, so joints are snapped within `snap_m`
         rather than compared exactly.

    A fourth problem only appears once the first three are fixed: "longest" is the wrong
    objective. At a confluence the greedy walk takes whichever branch it meets first, and the
    longest chain then happily runs up a tributary and past the headwaters instead of down to the
    lake — the Current River came out 19.1 km ending 84 m ABOVE lake level. So when a `mouth`
    is supplied, only chains that actually reach it are eligible, and the longest of THOSE wins.
    The mouth is the one point on a fishing river we already know for certain.

    Nearest-neighbour walking over a flattened point cloud — the obvious alternative — is worse
    still: it wandered up a tributary and produced 17% uphill samples.

    CONTRACT: the returned polyline is direction-AGNOSTIC. Which end comes first depends on which
    seed won, and both orientations of the same run are equally long. The caller orients it
    downstream from ELEVATION, which is the only reliable flow-direction signal available — OSM
    way direction is not one.
    """
    ways = [list(w) for w in ways if len(w) >= 2]
    if not ways:
        return []
    mlat = 111320.0
    mlon = 111320.0 * math.cos(math.radians(ways[0][0][0]))

    def close(a, b):
        return math.hypot((b[0] - a[0]) * mlat, (b[1] - a[1]) * mlon) <= snap_m

    def extend(seed_i, seed_rev):
        w = ways[seed_i][::-1] if seed_rev else ways[seed_i]
        chain, used = [w], {seed_i}
        moved = True
        while moved:                       # grow forward, then backward, until nothing attaches
            moved = False
            tail = chain[-1][-1]
            for j, wj in enumerate(ways):
                if j in used:
                    continue
                if close(tail, wj[0]):
                    chain.append(wj); used.add(j); moved = True; break
                if close(tail, wj[-1]):
                    chain.append(wj[::-1]); used.add(j); moved = True; break
            if moved:
                continue
            head = chain[0][0]
            for j, wj in enumerate(ways):
                if j in used:
                    continue
                if close(head, wj[-1]):
                    chain.insert(0, wj); used.add(j); moved = True; break
                if close(head, wj[0]):
                    chain.insert(0, wj[::-1]); used.add(j); moved = True; break
        out = []
        for w_ in chain:
            out.extend(w_ if not out else w_[1:])
        return out

    def reaches_mouth(c):
        if mouth is None:
            return True
        return any(math.hypot((q[0] - mouth[0]) * mlat, (q[1] - mouth[1]) * mlon) <= mouth_tol_m
                   for q in c)

    # SEED SELECTION IS ALSO A PERFORMANCE DECISION. `extend` is O(W^2) in the number of ways, so
    # trying every way as a seed is O(W^4) — on the Kaministiquia (a long river with many ways)
    # that ran over ten minutes before being killed, far too slow for a build step. When a mouth is
    # known we need not search at all: seed only from ways touching it, which collapses this to a
    # couple of extends. The all-seeds sweep survives solely as the no-mouth fallback.
    seeds = list(range(len(ways)))
    if mouth is not None:
        near = [i for i, w in enumerate(ways)
                if min(math.hypot((q[0] - mouth[0]) * mlat, (q[1] - mouth[1]) * mlon)
                       for q in (w[0], w[-1])) <= mouth_tol_m]
        if near:
            seeds = near

    best, best_len = [], -1.0
    fallback, fallback_len = [], -1.0
    for i in seeds:
        for rev in (False, True):
            c = extend(i, rev)
            if len(c) < 2:
                continue
            L = _seg_len(c)
            if L > fallback_len:
                fallback, fallback_len = c, L
            if reaches_mouth(c) and L > best_len:
                best, best_len = c, L
    # No chain reaches the mouth (bad coord, or the river genuinely joins another watercourse
    # upstream of the lake): fall back to the longest, and let the datum check flag it.
    return best if best else fallback


def _m_per_deg(lat: float):
    return 111320.0, 111320.0 * math.cos(math.radians(lat))


def densify(pts, step_m: float):
    """Resample a lat/lon polyline to even spacing. Returns (pts, cumulative_distance_m)."""
    if len(pts) < 2:
        return list(pts), [0.0] * len(pts)
    mlat, mlon = _m_per_deg(pts[0][0])
    d = [0.0]
    for a, b in zip(pts, pts[1:]):
        d.append(d[-1] + math.hypot((b[0] - a[0]) * mlat, (b[1] - a[1]) * mlon))
    total = d[-1]
    n = max(2, int(total // step_m) + 1)
    out, dist = [], []
    for i in range(n):
        s = i * step_m
        if s > total:
            break
        j = max(1, next((k for k in range(1, len(d)) if d[k] >= s), len(d) - 1))
        f = 0.0 if d[j] == d[j - 1] else (s - d[j - 1]) / (d[j] - d[j - 1])
        out.append((pts[j - 1][0] + (pts[j][0] - pts[j - 1][0]) * f,
                    pts[j - 1][1] + (pts[j][1] - pts[j - 1][1]) * f))
        dist.append(s)
    return out, dist


def reach_slope(dist_m, z_m, window_m: float):
    """Least-squares slope (m/km, positive = dropping downstream) in a moving window."""
    n = len(dist_m)
    out = [float("nan")] * n
    if n < 3:
        return out
    for i in range(n):
        lo, hi = dist_m[i] - window_m / 2, dist_m[i] + window_m / 2
        xs, ys = [], []
        for j in range(n):
            if lo <= dist_m[j] <= hi:
                xs.append(dist_m[j])
                ys.append(z_m[j])
        if len(xs) < 3:
            continue
        mx = sum(xs) / len(xs)
        my = sum(ys) / len(ys)
        den = sum((x - mx) ** 2 for x in xs)
        if den <= 0:
            continue
        b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
        out[i] = -b * 1000.0
    return out


def classify_points(slopes, edges: dict):
    """Per-sample class from measured percentile edges."""
    out = []
    for s in slopes:
        if s is None or not math.isfinite(s):
            out.append("unknown")
        elif s >= edges["barrier"]:
            out.append("barrier")
        elif s >= edges["steep"]:
            out.append("rapid")
        elif s <= edges["flat"]:
            out.append("pool")
        else:
            out.append("riffle")
    return out


def to_reaches(dist_m, z_m, latlon, classes, min_len_m: float):
    """Collapse per-sample classes into contiguous reaches, dropping ones too short to be real."""
    reaches = []
    i = 0
    n = len(classes)
    while i < n:
        c = classes[i]
        j = i
        while j + 1 < n and classes[j + 1] == c:
            j += 1
        if c != "unknown" and dist_m[j] - dist_m[i] >= min_len_m:
            mid = (i + j) // 2
            span = max(1e-6, dist_m[j] - dist_m[i])
            reaches.append(Reach(cls=c, start_m=dist_m[i], end_m=dist_m[j],
                                 z_start=z_m[i], z_end=z_m[j],
                                 slope_m_km=1000.0 * (z_m[i] - z_m[j]) / span,
                                 lat=latlon[mid][0], lon=latlon[mid][1]))
        i = j + 1
    return reaches


def calibrate(all_slopes) -> dict:
    """Measured class edges from the POOLED regional slope distribution (never hardcoded)."""
    finite = [s for s in all_slopes if s is not None and math.isfinite(s)]
    if len(finite) < 100:
        raise ValueError(f"only {len(finite)} finite slope samples — too thin to calibrate")
    return {
        "flat": percentile(finite, FLAT_PCT),
        "steep": percentile(finite, STEEP_PCT),
        "barrier": percentile(finite, BARRIER_PCT),
        "n_samples": len(finite),
        "pcts": {"flat": FLAT_PCT, "steep": STEEP_PCT, "barrier": BARRIER_PCT},
    }


def holding_water_below(reaches, max_gap_m: float = 150.0):
    """Mark the pool immediately downstream of each barrier.

    This is the fishing-relevant output, and the reason barriers are not treated as exclusions:
    migratory fish stop at the first impassable step and hold below it. Returns the barrier index
    -> pool index mapping so the caller can label those pools explicitly.
    """
    out = {}
    for bi, b in enumerate(reaches):
        if b.cls != "barrier":
            continue
        for pi in range(bi + 1, len(reaches)):
            if reaches[pi].start_m - b.end_m > max_gap_m:
                break
            if reaches[pi].cls == "pool":
                out[bi] = pi
                break
    return out


# --- HYDRAULICS: the variable that actually makes rivers comparable ---------------------------
# WHY THIS REPLACES RAW SLOPE. The first build classified reaches by percentiles of slope pooled
# across all five rivers, copying ADR-039's regional-percentile discipline. That import was wrong,
# and the measurement showed it: the pooled "rapid" bar came out at 10.73 m/km while the
# Kaministiquia's MEAN gradient is 1.86 m/km — 0.17x the bar. A big river is flat by construction
# (hydraulic geometry: gradient falls as discharge rises), so the Kam could never register a rapid
# no matter how fast its water actually ran. Recall showed exactly that: 100% on the Current
# (mean gradient 8.73, near the bar) against 25% on the Kam.
#
# Unit stream power is the physically correct variable for "fast, broken water":
#     omega = rho * g * Q * S / w        [W/m^2]
# It rises with discharge and slope and falls with width, so a wide low-gradient river carrying a
# lot of water and a narrow steep creek become directly comparable. Crucially, that restores the
# regional-percentile method: pooling is valid on omega precisely because omega IS comparable
# across rivers, which raw slope is not.
#
# It also makes the layer LIVE. Q is measured daily (ECCC gauges 02AB006 Kam, 02AB014 Current,
# 02AB008 Neebing floodway), so a seam at spring freshet is not the same seam in August low water —
# which is true of real rivers and is exactly what an angler needs to know.
RHO = 1000.0        # kg/m^3
G = 9.81            # m/s^2


def unit_stream_power(slope_m_km: float, q_cms: float, width_m: float) -> float | None:
    """omega = rho*g*Q*S/w in W/m^2. None if any input is missing or non-physical."""
    if slope_m_km is None or q_cms is None or width_m is None:
        return None
    if not all(math.isfinite(v) for v in (slope_m_km, q_cms, width_m)):
        return None
    if width_m <= 0 or q_cms < 0:
        return None
    s = max(0.0, slope_m_km) / 1000.0          # m/km -> m/m; negative slope is survey noise
    return RHO * G * q_cms * s / width_m


# --- BARRIERS: absolute physics, never a percentile -------------------------------------------
# A percentile ALWAYS returns its quantile. Run p99 over McIntyre's 0.03 m/km floodway and it will
# dutifully manufacture "barriers" out of survey noise. But passability is not relative: a 2 m
# vertical step stops a steelhead on any river, in any region, whatever the local distribution
# says. So barriers are tested against fish capability in metres.
#
# Leap capability is species-specific, and that is a FEATURE rather than a complication — it means
# the upstream limit differs by species, which is precisely what a per-species app should say. The
# heights below are the conventional design values used in fish-passage engineering; they are
# stated as a JUDGMENT (tier T3), not measured here, and the barrier call is reported with the
# height that produced it so a reader can disagree with the number without re-deriving the method.
LEAP_M = {
    "steelhead": 1.5,      # strongest leaper of the four; ascends substantial falls with a pool
    "chinook": 1.2,
    "coho": 1.2,
    "salmon": 1.2,         # app-level grouping
    "brook_trout": 0.6,    # weak leaper — coasters are stopped by steps others clear
    "lake_trout": 0.3,     # essentially a non-leaper; enters only the lowest reaches
}
# A step needs water below it to jump from. Without a plunge pool the same height is impassable,
# so a drop concentrated in a very short distance is treated as a barrier for everything.
CHUTE_LEN_M = 15.0


def barrier_for(drop_m: float, length_m: float, species: str) -> bool:
    """Is this step impassable for `species`? Absolute geometry vs leap capability."""
    if drop_m is None or not math.isfinite(drop_m) or drop_m <= 0:
        return False
    leap = LEAP_M.get(species, 1.0)
    if length_m <= CHUTE_LEN_M:
        return drop_m > leap                        # near-vertical step
    # A sustained steep chute is passable only if no single leap within it exceeds capability;
    # approximate that by the drop over one chute-length of the reach.
    per_chute = drop_m * (CHUTE_LEN_M / max(length_m, 1e-6))
    return per_chute > leap


def upstream_limit(reaches, species: str):
    """Index of the first reach (from the mouth upstream) that stops `species`, or None.

    THE FISHING-RELEVANT OUTPUT. Migratory fish run until something stops them and then hold
    below it — which is why the NSSA built a fishway on the Current rather than writing that
    stretch off. Reaches are expected in downstream order, so this walks from the mouth back up.
    """
    for i in range(len(reaches) - 1, -1, -1):
        r = reaches[i]
        if barrier_for(r.drop_m, r.length_m, species):
            return i
    return None


# --- SEAM GEOMETRY: the other dimensions of the survey ----------------------------------------
# Downstream slope is ONE dimension of a 1 m 3-D survey. Fish sit on seams, and seams are made by
# geometry we were not reading. Each of the following was probed on the Current River (the
# best-validated of the five, 100% barrier recall) and confirmed to carry real signal.

def width_gradient(widths, dist_m):
    """dW/ds — channel narrowing (+ scour below) or widening (deposition, slack water).

    A constriction accelerates flow and scours a pool immediately below it; an expansion drops
    velocity and deposits. This is literally the "sharp edge that makes a seam" case, and it costs
    nothing once width is measured per station. Measured on the Current: |dW/ds| p50 0.15,
    p90 0.65, p99 1.62 m/m — a wide spread, so the sharp cases stand out clearly.
    """
    n = len(widths)
    out = [None] * n
    for i in range(1, n - 1):
        a, b = widths[i - 1], widths[i + 1]
        ds = dist_m[i + 1] - dist_m[i - 1]
        if a is None or b is None or ds <= 0:
            continue
        out[i] = (b - a) / ds
    return out


def curvature(points):
    """Signed planform curvature (~sin of the turn angle); + is a left bend looking downstream.

    On a meander bend the thalweg swings to the OUTER bank and scours the pool there, while the
    inner bank builds a point bar — the most reliable "where is the deep water on a bend" rule in
    fluvial geomorphology, and it needs only the centreline. Sign matters because it tells you
    WHICH bank holds the pool, which is what an angler standing on one side needs to know.
    Measured on the Current: |curvature| p50 0.11, p90 0.42, p99 0.78.
    """
    n = len(points)
    out = [None] * n
    if n < 5:
        return out
    mlat = 111320.0
    mlon = 111320.0 * math.cos(math.radians(points[0][0]))
    xy = [((p[1]) * mlon, (p[0]) * mlat) for p in points]
    for i in range(2, n - 2):
        ax, ay = xy[i - 2]
        bx, by = xy[i]
        cx, cy = xy[i + 2]
        abx, aby = bx - ax, by - ay
        bcx, bcy = cx - bx, cy - by
        la = math.hypot(abx, aby)
        lb = math.hypot(bcx, bcy)
        if la > 1.0 and lb > 1.0:
            out[i] = (abx * bcy - aby * bcx) / (la * lb)
    return out


def outer_bank(curv_value: float | None) -> str | None:
    """Which bank holds the scour pool on this bend, looking downstream."""
    if curv_value is None or not math.isfinite(curv_value):
        return None
    if abs(curv_value) < 0.15:            # p50 of measured curvature — straighter than a bend
        return None
    return "right" if curv_value > 0 else "left"
