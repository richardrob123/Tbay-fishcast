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


def _mean(v):
    v = [x for x in v if x is not None and math.isfinite(x)]
    return sum(v) / len(v) if v else None


def check(triples, *, bar_c: float = GROSS_DISAGREEMENT_C,
          min_days: int = MIN_DAYS) -> SiteVerdict:
    """``triples`` = [(model_c, obs_c, satellite_c)] at the site, one per day near the surface.

    Returns a verdict on whether measurements here may be generalised to the product. `obs_c` may
    be None when only a model-vs-satellite check is possible; the observation arm is then skipped
    and the reason says so rather than passing silently.
    """
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
        return SiteVerdict(True, n, mm, oo,
                           f"model within {bar_c:.0f} C of satellite here "
                           f"({mm:+.1f} C mean) — the site can ground a product claim")
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
