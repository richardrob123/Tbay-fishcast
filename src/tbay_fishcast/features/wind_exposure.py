"""Land wind -> lake wind: what the airport reading is worth over the water (ADR-056).

THE STANDING GAP. The product's phase classifier fires on a sustained-blow threshold
(`upwelling_phase.OBSERVED_THRESHOLD_KN`), and the wind it reads live is CYQT — an airport
anemometer 16 km inland. Wind over water is faster than wind over land at the same synoptic
forcing, because the surface is smoother. So the same 13 kt bar applied to the airport reading
is not the same bar applied to the lake, and the scorecard has carried this note for months:
"the airport offset is what a separate phase threshold would need to earn — currently within
noise, so one Wedderburn bar is used."

That "within noise" was measured against NDBC buoys 130-200 km away, where the land/lake
difference is hopelessly confounded with 200 km of geography. Welcome Island is 16 km from CYQT
under the same synoptic system, which is what makes the exposure difference separable at all.

TWO FORMS, and the choice is not cosmetic. A roughness change scales wind MULTIPLICATIVELY, so
`lake = b * land` should fit better than `lake = land + a`; an additive offset fitted on mostly
light winds would then UNDER-correct exactly the strong-wind hours the threshold cares about.
Both are fitted and scored on held-out years so the answer is measured rather than asserted.

HONEST CAVEAT, stated because it bounds every number this produces: Welcome Island is an island
station, not a buoy. Its anemometer stands on land, so it under-reads a true over-water wind by
some unknown amount. It is far closer to the lake than the airport is, and it is not the lake.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ExposureFit:
    form: str                  # "multiplicative" | "additive" | "affine"
    scale: float               # b in lake = a + b*land  (1.0 for pure additive)
    offset: float              # a
    n_train: int

    def apply(self, land_kn: float | None) -> float | None:
        if land_kn is None or not math.isfinite(land_kn):
            return None
        return max(0.0, self.offset + self.scale * land_kn)

    def as_dict(self) -> dict:
        return {"form": self.form, "scale": round(self.scale, 4),
                "offset_kn": round(self.offset, 4), "n_train": self.n_train}


def fit(pairs, *, form: str = "multiplicative") -> ExposureFit | None:
    """``pairs`` = [(land_kn, lake_kn)]. Least squares in the requested form.

    The multiplicative fit is forced through the origin deliberately: calm on land is calm on the
    water, and an intercept free to float would let the fit buy accuracy in the light-wind bulk
    by predicting a breeze when there is none.
    """
    p = [(x, y) for x, y in pairs
         if x is not None and y is not None and math.isfinite(x) and math.isfinite(y)]
    if len(p) < 30:
        return None
    if form == "multiplicative":
        den = sum(x * x for x, _y in p)
        if den <= 0:
            return None
        return ExposureFit(form, sum(x * y for x, y in p) / den, 0.0, len(p))
    if form == "additive":
        return ExposureFit(form, 1.0, sum(y - x for x, y in p) / len(p), len(p))
    if form == "affine":
        mx = sum(x for x, _y in p) / len(p)
        my = sum(y for _x, y in p) / len(p)
        den = sum((x - mx) ** 2 for x, _y in p)
        if den <= 0:
            return None
        b = sum((x - mx) * (y - my) for x, y in p) / den
        return ExposureFit(form, b, my - b * mx, len(p))
    raise ValueError(f"unknown form {form!r}")


def score(fit_: ExposureFit | None, pairs) -> dict | None:
    """MAE and bias of a fit on held-out pairs, plus the same for the uncorrected reading."""
    if fit_ is None:
        return None
    p = [(x, y) for x, y in pairs
         if x is not None and y is not None and math.isfinite(x) and math.isfinite(y)]
    if not p:
        return None
    corr = [fit_.apply(x) - y for x, y in p]
    raw = [x - y for x, y in p]
    return {"n_holdout": len(p),
            "corrected_mae_kn": round(sum(abs(v) for v in corr) / len(corr), 4),
            "corrected_bias_kn": round(sum(corr) / len(corr), 4),
            "uncorrected_mae_kn": round(sum(abs(v) for v in raw) / len(raw), 4),
            "uncorrected_bias_kn": round(sum(raw) / len(raw), 4)}


def circular_offset_deg(pairs) -> dict | None:
    """Mean signed lake-minus-land direction difference, computed on the circle.

    Arithmetic differencing is wrong here and quietly so: 350 deg and 10 deg differ by 20, not
    340, and a season of northerlies would otherwise produce a mean offset near 180.
    """
    sx = sy = 0.0
    n = 0
    for land, lake in pairs:
        if land is None or lake is None:
            continue
        d = math.radians(lake - land)
        sx += math.cos(d)
        sy += math.sin(d)
        n += 1
    if n < 30:
        return None
    mean = math.degrees(math.atan2(sy / n, sx / n))
    r = math.hypot(sx / n, sy / n)          # 1 = perfectly consistent, 0 = no preferred offset
    return {"n": n, "mean_offset_deg": round(mean, 2), "concentration": round(r, 4)}
