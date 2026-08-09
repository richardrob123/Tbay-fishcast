"""1-D river hydraulics: depth, velocity, Froude number and seams (ADR-045).

WHAT THIS BUYS. Lidar cannot see through water, so we measure the water SURFACE and know nothing
about depth — yet depth is what defines a pool, and velocity is what defines a seam. Manning's
equation inverts the three things we DO measure into the two we don't:

    d = (Q * n / (w * sqrt(S)))^(3/5)          V = Q / (w * d)

Q is gauged daily (ECCC), w and S come from the 1 m lidar. Only the friction coefficient n is
unmeasured, and that single unknown governs how much of this can be believed — see UNCERTAINTY.

WHY FROUDE IS THE RIGHT CLASSIFIER. Fr = V / sqrt(g*d) is dimensionless, so it is comparable
across rivers by construction — no regional percentile needed, and no repeat of the mistake that
made raw slope incomparable between the Kaministiquia and McVicar. More importantly Fr = 1 is a
PHYSICAL transition, not a chosen number: below it flow is subcritical and tranquil, above it
supercritical and broken. Modelled on the Current River at Q=10, w=28, the crossing lands exactly
where it should — pool Fr 0.19, run 0.43, riffle 0.67, fast riffle 1.01, rapid 1.38, chute 1.99.
This project bans arbitrary thresholds; Fr = 1 is the opposite of arbitrary.

UNCERTAINTY, AND WHY IT IS THE HEADLINE RATHER THAN A FOOTNOTE. n is a judgment (T3), plausibly
0.025 for smooth gravel to 0.070 for boulder-and-brush. Because d scales as n^0.6 and Fr as
d^-1.5, that range propagates to a 2.53x spread in Fr — a reach computing Fr 1.38 carries a band
of 0.74-1.87, straddling critical. So the honest output is a BAND, and a reach may be called
supercritical ONLY when its entire band clears 1. Most will not qualify, and that is the correct
answer rather than a disappointing one. The whole layer is T3 derived and must never be shown with
the confidence of the T1 lidar geometry it rests on.

WHERE MANNING IS SIMPLY INVALID. It assumes slope-driven uniform flow. Near a river mouth the
water surface is held up by the lake, depth is set by lake level rather than slope, and S -> 0
makes the inversion diverge exactly where the drowned mouths are. Those reaches are detected and
excluded rather than reported with a wrong number — see `is_backwater`, whose tolerance is our own
MEASURED datum error (0.39-0.45 m across the five rivers), not a guess.

Pure functions; no I/O, no network, no LLM (ADR-001).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

G = 9.81

# Manning's n for natural channels. Stated as a JUDGMENT (T3) with its range, because the range is
# what makes the output honest: it is the dominant uncertainty in everything below.
N_LO, N_MID, N_HI = 0.025, 0.035, 0.070

# Backwater tolerance = the datum error we actually measured when the five profiles landed on Lake
# Superior (0.39, 0.43, 0.44, 0.45, 0.45 m against the 183.2 m datum). Within that distance of lake
# level, a water surface is indistinguishable from the lake's own, so slope cannot be trusted to
# drive flow there. Deriving the tolerance from measured error rather than picking a round number
# is the same discipline the rest of the project uses.
BACKWATER_TOL_M = 0.5

# PLAUSIBILITY GATE. Manning is happy to return a depth of 2 cm for a 28 m river if you feed it a
# discharge from the wrong gauge — which is exactly what happened on the Current River, whose
# station 02AB014 measures a tributary at 0.08 m3/s and produced a median depth of 0.02 m. That is
# not a river, it is a wet road. Garbage-in-garbage-out is not acceptable when the output looks
# like a measurement, so a depth-to-width ratio below this is reported as implausible rather than
# as a number. Natural channels essentially never run this shallow relative to their width; when
# they appear to, the discharge is wrong.
MIN_DEPTH_WIDTH_RATIO = 1.0 / 500.0


@dataclass
class Hydraulics:
    """Depth/velocity/Froude with the band implied by Manning's n. All depths m, velocity m/s."""

    depth_m: float | None
    velocity_ms: float | None
    froude: float | None
    froude_lo: float | None
    froude_hi: float | None
    backwater: bool = False
    note: str = ""

    @property
    def state(self) -> str:
        """Flow regime, admitting uncertainty rather than papering over it.

        'supercritical' is claimed only when the whole n-band clears Fr = 1. Anything spanning the
        transition is 'transitional' — genuinely unresolved at our level of knowledge about n, and
        saying so is more useful than a confident coin-flip.
        """
        if self.backwater:
            return "backwater"
        if self.froude is None or self.froude_lo is None or self.froude_hi is None:
            return "unknown"
        if self.froude_lo > 1.0:
            return "supercritical"
        if self.froude_hi < 1.0:
            return "subcritical"
        return "transitional"


def manning_depth(q_cms, width_m, slope_m_km, n: float = N_MID):
    """Mean depth (m) from discharge, width and water-surface slope. None if non-physical."""
    if None in (q_cms, width_m, slope_m_km):
        return None
    if not all(math.isfinite(float(v)) for v in (q_cms, width_m, slope_m_km)):
        return None
    s = float(slope_m_km) / 1000.0
    if s <= 0 or width_m <= 0 or q_cms <= 0 or n <= 0:
        return None
    return (float(q_cms) * n / (float(width_m) * math.sqrt(s))) ** 0.6


def velocity(q_cms, width_m, depth_m):
    if None in (q_cms, width_m, depth_m) or width_m <= 0 or depth_m <= 0:
        return None
    return float(q_cms) / (float(width_m) * float(depth_m))


def froude(velocity_ms, depth_m):
    if None in (velocity_ms, depth_m) or depth_m <= 0:
        return None
    return float(velocity_ms) / math.sqrt(G * float(depth_m))


def is_backwater(z_m, lake_datum_m: float, tol_m: float = BACKWATER_TOL_M) -> bool:
    """Is this reach's water surface indistinguishable from lake level?

    A PHYSICAL criterion, not a slope threshold: if the surface sits within our own measurement
    error of the lake, the lake is the control and Manning does not apply however the slope reads.
    """
    if z_m is None or not math.isfinite(float(z_m)):
        return False
    return abs(float(z_m) - lake_datum_m) <= tol_m


def solve(q_cms, width_m, slope_m_km, *, z_m=None, lake_datum_m=None,
          n_lo: float = N_LO, n_mid: float = N_MID, n_hi: float = N_HI) -> Hydraulics:
    """Full hydraulic state with its uncertainty band."""
    if lake_datum_m is not None and is_backwater(z_m, lake_datum_m):
        return Hydraulics(None, None, None, None, None, backwater=True,
                          note="water surface at lake level — depth set by the lake, not by slope")
    d = manning_depth(q_cms, width_m, slope_m_km, n_mid)
    if d is None:
        return Hydraulics(None, None, None, None, None, note="inputs missing or non-physical")
    if d / float(width_m) < MIN_DEPTH_WIDTH_RATIO:
        return Hydraulics(None, None, None, None, None,
                          note=(f"implausible: depth {d:.3f} m on a {float(width_m):.0f} m channel "
                                f"(d/w < 1/500) — the discharge is almost certainly from the "
                                f"wrong gauge, not a real reading of this river"))
    v = velocity(q_cms, width_m, d)
    fr = froude(v, d)
    # a LOWER n gives a shallower, faster flow => a HIGHER Froude number
    d_hi_n = manning_depth(q_cms, width_m, slope_m_km, n_hi)
    d_lo_n = manning_depth(q_cms, width_m, slope_m_km, n_lo)
    fr_lo = froude(velocity(q_cms, width_m, d_hi_n), d_hi_n)      # rough bed -> deep, slow, low Fr
    fr_hi = froude(velocity(q_cms, width_m, d_lo_n), d_lo_n)      # smooth bed -> shallow, fast
    return Hydraulics(d, v, fr, fr_lo, fr_hi)


# --- SEAMS ------------------------------------------------------------------------------------
# A seam is a shear boundary between water moving at different speeds — which is why it needed
# velocity, and why no amount of surface imagery would have produced it. Two kinds, fished
# differently, so they are labelled separately rather than merged into one "seam" score:
#
#   LONGITUDINAL — fast water running into slow (a pool head or tail). Found as dV/ds.
#   LATERAL      — the shear runs ALONG the flow: the fast outer bank of a bend against the slow
#                  inside, or the jet through a constriction against the slack water beside it.
#                  Found from curvature and width gradient, which we already measure.
SEAM_DV_PCT = 90.0        # a longitudinal seam is a velocity change in the top decile FOR THAT RIVER


def velocity_gradient(velocities, dist_m):
    """dV/ds (per second): negative where flow decelerates into a pool, positive into a chute."""
    n = len(velocities)
    out = [None] * n
    for i in range(1, n - 1):
        a, b = velocities[i - 1], velocities[i + 1]
        ds = dist_m[i + 1] - dist_m[i - 1]
        if a is None or b is None or ds <= 0:
            continue
        out[i] = (b - a) / ds
    return out


def find_seams(dvds, curvature_vals, width_grad, *, dv_edge: float,
               curv_edge: float, dwds_edge: float):
    """Per-station seam labels. Edges are measured percentiles supplied by the caller.

    Deceleration is called out separately from acceleration because they are not the same
    fishing feature: flow slowing into a pool drops what it carries and fish face INTO it, while
    flow accelerating into a chute is somewhere they hold below, not in.
    """
    n = max(len(dvds), len(curvature_vals), len(width_grad))
    out = []
    for i in range(n):
        tags = []
        dv = dvds[i] if i < len(dvds) else None
        cv = curvature_vals[i] if i < len(curvature_vals) else None
        dw = width_grad[i] if i < len(width_grad) else None
        if dv is not None and math.isfinite(dv) and abs(dv) >= dv_edge:
            tags.append("seam_decel" if dv < 0 else "seam_accel")
        if cv is not None and math.isfinite(cv) and abs(cv) >= curv_edge:
            tags.append("seam_bend_right" if cv > 0 else "seam_bend_left")
        if dw is not None and math.isfinite(dw) and abs(dw) >= dwds_edge:
            tags.append("seam_constriction" if dw < 0 else "seam_expansion")
        out.append(tags)
    return out
