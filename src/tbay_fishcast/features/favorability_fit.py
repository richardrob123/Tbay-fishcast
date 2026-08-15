"""Does west wind actually cool THIS shore? Pure statistics for the recalibration (ADR-058).

`data/calib/upwelling_favorability.json` currently reads `calibrated: false,
fit_rejected(auc=0.485)`. The layer runs on the physics prior because its only calibration
attempt could not discriminate at all. The recorded reason is a siting error, in the calibrator's
own words: offshore western-Superior buoys "do NOT show the coastal-upwelling wind->cooling
response ... coastal upwelling is a NEARSHORE phenomenon these deep-water buoys miss."

That is ADR-050 wearing different clothes — a real measurement taken at a place that cannot speak
for the claim. This module supports redoing it where the product actually operates.

THREE METHOD CHOICES, each made to avoid a specific way of fooling ourselves:

  1. THE RESPONSE IS A DIFFERENCE, shore pixel minus an offshore reference pixel. Upwelling makes
     the NEARSHORE cold relative to offshore; a cold front makes everything cold. The previous
     attempt used one buoy's absolute temperature, which cannot tell those apart — and cold fronts
     arrive with exactly the west-quadrant wind we are testing, so the confound is not incidental,
     it is aligned with the predictor. Differencing two pixels under the same air mass removes it,
     along with the seasonal cycle and any common-mode satellite bias.

  2. THE PRIMARY TEST IS THRESHOLD-FREE. A rank correlation between wind and subsequent cooling
     needs no "what counts as an event" bar, so it cannot be tuned into significance. The logistic
     the product consumes is fitted second, and its event bar is measured from the data's own
     quiet-day noise rather than chosen.

  3. THE LAG IS SCANNED AND REPORTED WHOLE, not picked. Setup is ~10 h and the seiche ~40 h, so
     the response lands somewhere in the following days — but choosing the best lag after seeing
     the answers is how a null becomes a finding. Every lag is reported; if one is selected for
     the operational fit, it is selected on training years only.

THE ASYMMETRY THAT BOUNDS ANY RESULT HERE, stated up front because it decides what a null means.
The only continuous nearshore temperature available at Thunder Bay is a daily satellite analysis
(verified, not assumed: Welcome Island is a met station with no water temperature, and the region
has zero marine buoys). ADR-053 measured that product as five times temporally smoother than real
water. Smoothing DAMPS amplitude and BLURS timing, so it can only weaken a real association, never
manufacture one. Therefore: a positive result here is credible and conservative, and a null is
NOT evidence that the shore does not upwell — only that a smoothed daily product cannot see it.
"""
from __future__ import annotations

import math


def _rank(v):
    """Ranks with ties averaged — required, since satellite values repeat at 0.01 C resolution."""
    order = sorted(range(len(v)), key=lambda i: v[i])
    out = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def spearman(x, y):
    """Rank correlation, with the two-sided p-value from the large-sample normal approximation."""
    pairs = [(a, b) for a, b in zip(x, y)
             if a is not None and b is not None and math.isfinite(a) and math.isfinite(b)]
    n = len(pairs)
    if n < 10:
        return {"n": n, "rho": None, "p_two_sided": None}
    rx, ry = _rank([p[0] for p in pairs]), _rank([p[1] for p in pairs])
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    if den == 0:
        return {"n": n, "rho": None, "p_two_sided": None}
    rho = num / den
    z = abs(rho) * math.sqrt(n - 1)
    p = math.erfc(z / math.sqrt(2.0))
    return {"n": n, "rho": round(rho, 4), "p_two_sided": round(p, 6)}


def auc(scores, labels):
    """Mann-Whitney AUC: P(score of a positive > score of a negative), ties counted as half."""
    pos = [s for s, l in zip(scores, labels) if l]
    neg = [s for s, l in zip(scores, labels) if not l]
    if not pos or not neg:
        return None
    r = _rank(list(pos) + list(neg))
    rp = sum(r[:len(pos)])
    return round((rp - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg)), 4)


def fit_logistic(x, labels, *, iters: int = 100, tol: float = 1e-9):
    """P(label) = sigmoid((x - s50) / width), by Newton-Raphson on (intercept, slope).

    Returns (s50, width) in the units of `x`, or None if the fit is degenerate. Degeneracy is
    NOT an edge case here: with no discrimination the slope collapses toward zero and s50 = -a/b
    explodes — which is exactly how the previous attempt produced `s50=425.4` for a quantity
    measured in knots. Reporting that as a fit rather than a failure is the trap.
    """
    pts = [(float(a), 1.0 if b else 0.0) for a, b in zip(x, labels)
           if a is not None and math.isfinite(a)]
    if len(pts) < 20:
        return None
    ys = [p[1] for p in pts]
    if not (0 < sum(ys) < len(ys)):          # all one class — nothing to separate
        return None
    a, b = 0.0, 0.0
    for _ in range(iters):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for xi, yi in pts:
            p = 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, a + b * xi))))
            r = yi - p
            w = max(p * (1.0 - p), 1e-12)
            g0 += r
            g1 += r * xi
            h00 += w
            h01 += w * xi
            h11 += w * xi * xi
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-14:
            return None
        da = (h11 * g0 - h01 * g1) / det
        db = (h00 * g1 - h01 * g0) / det
        a += da
        b += db
        if abs(da) < tol and abs(db) < tol:
            break
    if not (math.isfinite(a) and math.isfinite(b)) or abs(b) < 1e-6:
        return None
    return (-a / b, 1.0 / b)


def noise_floor(changes) -> float | None:
    """Standard deviation of the response on QUIET days — the data's own null distribution.

    The event bar is then a multiple of THIS rather than a number someone liked. Passing the
    quiet-day subset is the caller's job, because only the caller knows which days were quiet.
    """
    v = [c for c in changes if c is not None and math.isfinite(c)]
    if len(v) < 30:
        return None
    m = sum(v) / len(v)
    return math.sqrt(sum((c - m) ** 2 for c in v) / len(v))


# The event bar cuts an extreme tail, so the number of EVENTS — not the number of days — is the
# sample size. Learned the expensive way twice: ADR-057 nearly published a wind verdict off three
# storms, and the first run of this calibration printed a held-out AUC of 0.34 computed on SEVEN
# events. Both looked like findings and neither was one.
MIN_EVENTS = 25


def partial_spearman(x, y, z):
    """Rank correlation of x and y with the linear effect of z removed, on ranks.

    THE CONFOUND THIS EXISTS FOR. West-quadrant wind drives upwelling AND carries cold air, and
    cold air cools the surface by itself. The two are not merely correlated, they arrive together
    by construction, so a raw wind-vs-cooling correlation cannot separate them. Controlling for
    in-bay AIR temperature at the same mast does — and unlike a second satellite pixel, it adds
    no new instrument's noise to the response.
    """
    trip = [(a, b, c) for a, b, c in zip(x, y, z)
            if a is not None and b is not None and c is not None
            and math.isfinite(a) and math.isfinite(b) and math.isfinite(c)]
    n = len(trip)
    if n < 30:
        return {"n": n, "rho": None, "p_two_sided": None}
    rx, ry, rz = (_rank([t[0] for t in trip]), _rank([t[1] for t in trip]),
                  _rank([t[2] for t in trip]))

    def _resid(r):
        mz, mr = sum(rz) / n, sum(r) / n
        den = sum((c - mz) ** 2 for c in rz)
        if den == 0:
            return None
        b = sum((c - mz) * (v - mr) for c, v in zip(rz, r)) / den
        return [v - (mr + b * (c - mz)) for v, c in zip(r, rz)]

    ex, ey = _resid(rx), _resid(ry)
    if ex is None or ey is None:
        return {"n": n, "rho": None, "p_two_sided": None}
    mx, my = sum(ex) / n, sum(ey) / n
    num = sum((a - mx) * (b - my) for a, b in zip(ex, ey))
    den = math.sqrt(sum((a - mx) ** 2 for a in ex) * sum((b - my) ** 2 for b in ey))
    if den == 0:
        return {"n": n, "rho": None, "p_two_sided": None}
    rho = num / den
    z_ = abs(rho) * math.sqrt(n - 2)          # one control variable partialled out
    return {"n": n, "rho": round(rho, 4),
            "p_two_sided": round(math.erfc(z_ / math.sqrt(2.0)), 6)}
