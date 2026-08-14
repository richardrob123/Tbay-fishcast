"""Can this validation site ground a claim about the product? (ADR-052)

THE FAILURE THIS EXISTS TO PREVENT, in full, because it already happened. ADR-049 measured LSOFS
against the only subsurface mooring within reach and concluded that every forecast lead failed the
ADR-006 demotion bar. Every check run on that conclusion was a check for a bug in OUR pipeline —
reader agreement (0.000 C against the product's own reader), the time axis, the sigma-to-depth
mapping, missing-not-at-random selection, bootstrap degeneracy — and all of them passed. That is
exactly why the wrong conclusion looked solid.

None of them asked the different question: IS THE SITE REPRESENTATIVE OF WHAT WE ARE CLAIMING
ABOUT? It was not. On 2025-08-12 the same model hour put the deep offshore stations at 17.6-21.7 C
over a 3.97-3.99 C hypolimnion and Thunder Bay at 19.5 C, while the validation mooring alone read
8.60 C — and independent satellite SST 0.43 km from that mooring tracked the BUOY, not the model.
The model was locally broken at one node on an upwelling coast, and nothing about it generalised.

So a validation gate needs a SITE check as much as it needs a statistics check, and the site check
has to be independent of both the model and the in-situ observation being compared. Satellite SST
is that third party.

Two distinct outcomes, and conflating them is the whole point of separating them:
  * The model disagrees with SATELLITE at the site  -> the model is locally wrong. The measurement
    is real; the site cannot ground a claim about the product elsewhere.
  * The in-situ observation disagrees with SATELLITE -> the observation or its handling is suspect,
    and the measurement itself should not be trusted.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# A satellite SST analysis carries a few tenths of a degree of its own error, and a mooring's
# shallowest sensor can sit a degree or two off a satellite skin temperature on a calm sunny day.
# So agreement is judged against a bar well above that noise: this is a test for a site being
# GROSSLY unrepresentative, not for it being imperfect. Set at 3 C because that is the scale at
# which a disagreement stops being instrumentation and starts being a different body of water.
GROSS_DISAGREEMENT_C = 3.0
MIN_DAYS = 5
# A satellite SST is a SKIN temperature. In a stratified lake the water a few metres down is
# genuinely colder — often by several degrees — so comparing a deep sensor against it is not a
# like-for-like check at all. Fed a 6 m thermistor, this module confidently reported that the
# BUOY was suspect when the difference was simply the thermocline. Only observations shallow
# enough to sit in the mixed layer may be compared against a skin temperature; deeper ones make
# the check refuse rather than misattribute.
MAX_SKIN_COMPARE_DEPTH_M = 3.0


@dataclass(frozen=True)
class SiteVerdict:
    usable: bool                 # may this site ground a claim about the product?
    n_days: int
    model_vs_sat_c: float | None    # mean signed difference, model minus satellite
    obs_vs_sat_c: float | None      # mean signed difference, in-situ minus satellite
    reason: str

    def as_dict(self) -> dict:
        return {"usable": self.usable, "n_days": self.n_days,
                "model_vs_satellite_c": (None if self.model_vs_sat_c is None
                                         else round(self.model_vs_sat_c, 3)),
                "obs_vs_satellite_c": (None if self.obs_vs_sat_c is None
                                       else round(self.obs_vs_sat_c, 3)),
                "reason": self.reason}


# --- REFERENCE VALIDITY -----------------------------------------------------------------------
# ADR-053. The site check above asks "is this PLACE representative?". It does not ask the other
# question, and missing it cost a second wrong conclusion: "is this REFERENCE actually the thing
# we are trying to predict?"
#
# GLSEA/ACSPO satellite SST looked like an ideal truth for Thunder Bay — local, multi-year, T1.
# Measured against a real thermistor at the same place, its day-to-day change has a standard
# deviation of 0.38 C where the water's is 1.93 C. It is FIVE TIMES SMOOTHER than the lake,
# because it is a cloud-gap-filled, temporally relaxed analysis. Two things follow, and both
# invalidated a published verdict:
#
#   * A PERSISTENCE BASELINE built on it is not a forecast bar at all. Persisting a smooth
#     analysis against itself scored 0.295 C — which is simply GLSEA's own mean day-to-day change
#     (0.294 C). No physical forecast can beat that, and beating it would mean nothing.
#   * Its VARIANCE is damped, so a physical model compared against it looks "over-dispersed"
#     when the reference is under-dispersed.
#
# A reference can be perfectly good for one job and disqualifying for another: GLSEA tracks the
# seasonal cycle well (amplitude 4.09 C against the buoy's 5.08 C) and is fine for a mean-bias
# check. The failure was using it for the two jobs it cannot do.
VARIABILITY_RATIO_MIN = 0.5      # below this the reference is too smooth to be a skill baseline
# The sampling distribution of a VARIANCE RATIO is wide: at ~10 paired daily changes it cannot
# separate 1.0 from 0.5, so a thin sample can cheerfully certify a reference the full record
# disqualifies. Seen live — a 16-day slice returned "usable, ratio 1.10" for the same satellite
# product a 99-day slice had measured at 5x too smooth. 30 changes is where the ratio starts to
# be worth acting on; below it the honest answer is that we cannot tell.
MIN_CHANGES = 30


def reference_variability(ref_daily: dict, insitu_daily: dict) -> dict:
    """Is a candidate reference as variable as the real water? (ADR-053)

    Both arguments are ``{ISO day: value}`` at the SAME place. Returns the day-to-day change
    statistics of each and a verdict on which jobs the reference can do. Run this before any
    reference is used as truth for a skill comparison — not after a verdict has been published.
    """
    import statistics as _st
    from datetime import date as _date

    def _chg(series):
        keys = sorted(set(series))
        out = []
        for a, b in zip(keys, keys[1:]):
            if (_date.fromisoformat(b) - _date.fromisoformat(a)).days == 1:
                out.append(series[b] - series[a])
        return out

    common = sorted(set(ref_daily) & set(insitu_daily))
    r = _chg({d: ref_daily[d] for d in common})
    o = _chg({d: insitu_daily[d] for d in common})
    if len(r) < MIN_CHANGES or len(o) < MIN_CHANGES:
        return {"n_days": len(common), "n_changes": min(len(r), len(o)),
                "usable_as_skill_baseline": False,
                "reason": (f"only {min(len(r), len(o))} paired day-to-day changes "
                           f"(need {MIN_CHANGES}) — a variance ratio this thin cannot separate "
                           f"a faithful reference from a smoothed one, so the reference is "
                           f"unjudged rather than approved")}
    rs, os_ = _st.pstdev(r), _st.pstdev(o)
    ratio = (rs / os_) if os_ > 0 else None
    ok = bool(ratio is not None and ratio >= VARIABILITY_RATIO_MIN)
    return {
        "n_days": len(common), "n_changes": len(r),
        "reference_daily_change_sd_c": round(rs, 3),
        "insitu_daily_change_sd_c": round(os_, 3),
        "variability_ratio": round(ratio, 3) if ratio is not None else None,
        "usable_as_skill_baseline": ok,
        "reason": (f"reference day-to-day variability is {ratio:.2f} of the real water's — "
                   f"usable as a skill baseline" if ok else
                   f"reference is {1 / ratio:.1f}x SMOOTHER than the water "
                   f"({rs:.2f} vs {os_:.2f} C day-to-day sd). Persisting it scores its own "
                   f"smoothness, not forecast difficulty, and its damped variance makes a "
                   f"physical model look over-dispersed. Usable for mean bias and the seasonal "
                   f"cycle; NOT for skill or variance."),
    }


def _mean(v):
    v = [x for x in v if x is not None and math.isfinite(x)]
    return sum(v) / len(v) if v else None


def check(triples, *, bar_c: float = GROSS_DISAGREEMENT_C,
          min_days: int = MIN_DAYS, obs_depth_m: float | None = None) -> SiteVerdict:
    """``triples`` = [(model_c, obs_c, satellite_c)] at the site, one per day near the surface.

    Returns a verdict on whether measurements here may be generalised to the product. `obs_c` may
    be None when only a model-vs-satellite check is possible; the observation arm is then skipped
    and the reason says so rather than passing silently.

    ``obs_depth_m`` is the depth the in-situ values come from. Pass it: against a satellite SKIN
    temperature, anything below the mixed layer is a different quantity, and the check refuses
    rather than blaming the instrument for the thermocline.
    """
    if obs_depth_m is not None and obs_depth_m > MAX_SKIN_COMPARE_DEPTH_M:
        return SiteVerdict(
            False, 0, None, None,
            f"the shallowest usable observation is at {obs_depth_m:.1f} m, below the "
            f"{MAX_SKIN_COMPARE_DEPTH_M:.0f} m limit for comparison against a satellite SKIN "
            f"temperature — in a stratified column that difference is the thermocline, not an "
            f"error, so this site cannot be judged with this reference")
    m_d, o_d, n = [], [], 0
    for model_c, obs_c, sat_c in triples:
        if sat_c is None or not math.isfinite(sat_c):
            continue
        n += 1
        if model_c is not None and math.isfinite(model_c):
            m_d.append(model_c - sat_c)
        if obs_c is not None and math.isfinite(obs_c):
            o_d.append(obs_c - sat_c)
    mm, oo = _mean(m_d), _mean(o_d)
    if n < min_days:
        return SiteVerdict(False, n, mm, oo,
                           f"only {n} day(s) with satellite coverage — cannot judge the site")
    if mm is not None and abs(mm) <= bar_c and (oo is None or abs(oo) <= bar_c):
        # Say out loud when only one arm ran. A model-only pass means "the model is not grossly
        # wrong here", NOT "the observation checks out" — there was no observation. Reporting
        # both cases with the same sentence would let a one-armed check read as a two-armed one.
        arm = ("" if oo is not None else
               " (model arm only — no in-situ observation was supplied to cross-check)")
        return SiteVerdict(True, n, mm, oo,
                           f"model within {bar_c:.0f} C of satellite here "
                           f"({mm:+.1f} C mean) — the site can ground a product claim{arm}")
    # COMPARATIVE, NOT ABSOLUTE, and a test caught why. On the real LLO1 case the buoy sits 4.5 C
    # below the satellite — a perfectly ordinary skin-vs-bulk difference on calm sunny days, since
    # a satellite sees the top microns and a 1 m thermistor sees mixed water — while the model
    # sits 11.8 C below. An absolute bar blamed the OBSERVATION for the skin effect and let the
    # model off. Whichever side is FURTHER from the independent third party is the suspect.
    dm = abs(mm) if mm is not None else -1.0
    do = abs(oo) if oo is not None else -1.0
    if do > dm:
        return SiteVerdict(
            False, n, mm, oo,
            f"the in-situ observation is further from satellite ({oo:+.1f} C) than the model is "
            f"({mm:+.1f} C) — the OBSERVATION or its handling is suspect, so the measurement "
            f"itself cannot be trusted")
    return SiteVerdict(
        False, n, mm, oo,
        f"the model is further from satellite ({mm:+.1f} C) than the observation is "
        f"({oo:+.1f} C) — the model is locally wrong here, so results measure this node and "
        f"must not be generalised to the product")
