"""Graded, data-backed suitability primitives — NO arbitrary cross-variable fusion.

A hard "bottom temp in range: yes/no" blob is low quality, and so is fusing temperature,
upwelling phase, front proximity and structure into one score with made-up weights: without
catch/field-session outcomes to fit against, those weights are guesses. This project's rules
forbid that (CLAUDE rules 6-8: never tune on data you report skill for; demote a layer that
can't beat climatology; calibrated probabilities with honest intervals, not certainty).

So this module provides only the pieces that ARE grounded in data, each graded (no binary
cliffs) and each traceable to a source:

  * thermal_suitability — grades ONE variable (modeled bias-corrected bottom temperature)
    through a species' published thermal-preference curve. Data-backed by the fish-thermal
    literature (docs/FISH_BEHAVIOR_REVIEW.md, tier T3): 1.0 across the optimal core, tapering
    to 0 at the edges of the preferred range.
  * bathymetric_structure / bathymetric_relief — the physical break / drop-off / shoal edge,
    the slope and local relief of the CHS NONNA soundings. DIRECTLY MEASURED bathymetry — the
    highest-confidence location signal, and (per ADR-029) the ONLY edge the current build uses.
  * thermal_front_gradient — the thermal edge, |∇T| of the modeled bottom-temperature field.
    A tested primitive, but NOT wired into the current build: ADR-028/029 found the LSOFS field
    too coarse to resolve real fronts (Landsat couldn't validate it) and largely redundant with
    depth structure, so it over-called prime and was dropped in favour of the measured edge.
    Retained here as an available primitive for when a finer SST source makes it validatable.
  * upwelling_favorability — a CONTINUOUS response of upwelling strength to wind speed,
    replacing the arbitrary binary ≥13 kt cutoff. Physics-backed (Wedderburn control; Li et
    al. 2021 wind-vs-cooling r=-0.87). Its centre/width are the physics PRIOR;
    scripts/calibrate_upwelling.py refused the observed fit (non-discriminating), so the prior
    stands and is labelled as such — not a validated calibration.

WHERE-THE-FISH-ARE combination (the build): thermal_suitability (where they hold) ∩ a MEASURED
bathymetric edge (bathymetric_structure OR bathymetric_relief, against ABSOLUTE data-derived bars
— NOT a self-calibrating per-scene threshold; ADR-029). Combined by CONJUNCTION, which needs no
invented weights: the prime zone is where a species is both in its optimal temperature AND on a
real measured break. A weighted sum with fitted coefficients is a different thing and is NOT done
here — that needs catch outcomes.

The upwelling PHASE (features/upwelling_phase.py) stays a SEPARATE, physically-grounded
TIMING signal — not multiplied into the spatial where-they-are field, because how strongly a
phase moves the bite is a catch-outcome question we have no data to answer yet. That weighting
is deferred to when the pre-registered field-session logs exist, so it can be FIT and
temporal-split validated instead of guessed (CLAUDE rule 7).

Pure functions, no I/O, no LLM (ADR-001, CLAUDE rule 2).
"""
from __future__ import annotations

import numpy as np

# Continuous upwelling response to wind speed. Defaults sit at the low-middle of the
# documented Wedderburn upwelling range (~12-17 kt sustained, mixed-layer-depth dependent).
# These are the PRIOR; calibrate_upwelling.py overwrites them from observed cooling events when
# enough are logged, and pins the fitted values here with provenance.
FAVOR_S50_KN = 13.0     # speed at which favorability = 0.5
FAVOR_WIDTH_KN = 3.0    # logistic width (kt)


def upwelling_favorability(speed_kn, s50: float = FAVOR_S50_KN,
                           width: float = FAVOR_WIDTH_KN):
    """Continuous 0..1 upwelling-favorability of a (favorable-direction) wind speed.

    Logistic in speed: ~0 at calm, 0.5 at `s50`, saturating for strong blows — so a persistent
    moderate west wind reads as moderate, not the flat 0 a hard cutoff produces. Direction is
    the caller's job (multiply by an in-sector mask). This is a physics prior calibratable to
    observed wind↔cooling; it is NOT fused with temperature into a single "catch" number.
    """
    x = (np.asarray(speed_kn, dtype=float) - s50) / width
    return 1.0 / (1.0 + np.exp(-x))


def thermal_front_gradient(bottom_c, res_m: float):
    """Thermal-front strength: the spatial gradient magnitude |∇T| of the bottom-temperature
    field, in °C per metre. Where bottom temperature changes fastest is a thermal edge / depth
    break — the structure fish hold on and feed along (telemetry: salmonids select thermal
    fronts). Derived from the modelled field itself (data), not a picked constant.

    NaN off-water propagates one pixel into the water edge (numpy gradient), which conveniently
    keeps the shoreline rim from registering as a false front. Returns NaN where not computable.
    """
    b = np.asarray(bottom_c, dtype=float)
    gy, gx = np.gradient(b, res_m)
    return np.hypot(gx, gy)


def bathymetric_structure(depth_m, res_m: float):
    """Bathymetric structure strength: the bottom SLOPE magnitude |∇depth| (rise/run,
    dimensionless). Where the bottom drops off fastest is a break / ledge / shoal edge —
    the physical structure shore fish relate to independent of temperature (a rocky point,
    a drop-off, the lip of a hump). Directly measured (CHS NONNA-10 soundings), so it is a
    higher-confidence location signal than the modelled thermal front.

    Steep = structure; a flat sand/mud shelf reads ~0. NaN off-water propagates one pixel in
    (numpy gradient), keeping the shoreline rim from registering as false structure.
    """
    d = np.asarray(depth_m, dtype=float)
    gy, gx = np.gradient(d, res_m)
    return np.hypot(gx, gy)


def bathymetric_relief(depth_m, res_m: float, radius_m: float = 60.0):
    """Local relief (m): how much SHALLOWER a pixel is than its surroundings — positive over
    the flat top of a shoal, the crest of a reef, or the tip of a point, which the slope term
    MISSES (they are locally flat). = neighbourhood-mean depth − depth, so a hump reads +, a
    hole reads −. Neighbourhood is a ~radius_m box; NaN-aware (normalized box filter over the
    water mask, so land doesn't drag the mean). Directly measured from NONNA soundings."""
    from scipy.ndimage import uniform_filter
    d = np.asarray(depth_m, dtype=float)
    mask = np.isfinite(d).astype(float)
    filled = np.where(np.isfinite(d), d, 0.0)
    size = max(3, int(round(2 * radius_m / max(res_m, 1e-6))) | 1)   # odd box ~2*radius wide
    num = uniform_filter(filled, size=size, mode="nearest")
    den = uniform_filter(mask, size=size, mode="nearest")
    with np.errstate(invalid="ignore", divide="ignore"):
        local_mean = np.where(den > 0, num / den, np.nan)
    relief = local_mean - d                                          # + where shallower than around
    return np.where(np.isfinite(d), relief, np.nan)


def thermal_suitability(bottom_c, range_c, optimal_c=None):
    """Graded 0..1 thermal suitability of a bottom temperature for a species.

    1.0 across the optimal core `optimal_c=(cold,warm)`, linearly tapering to 0 at the edges
    of the preferred `range_c=(cold,warm)`, 0 outside. `optimal_c` defaults to the full range.
    Works on scalars or arrays; NaN (non-water / no model) maps to 0. The range/optimal come
    from published thermal preferences (stations.yaml `species:`, tier T3) — this grades one
    measured variable through a literature curve, not a fabricated multi-signal weighting.
    """
    b = np.asarray(bottom_c, dtype=float)
    r_cold, r_warm = float(range_c[0]), float(range_c[1])
    o_cold, o_warm = (float(optimal_c[0]), float(optimal_c[1])) if optimal_c else (r_cold, r_warm)

    s = np.zeros(b.shape, dtype=float)
    with np.errstate(invalid="ignore"):
        if o_cold > r_cold:                                   # cold margin r_cold..o_cold -> 0..1
            m = (b >= r_cold) & (b < o_cold)
            s = np.where(m, (b - r_cold) / (o_cold - r_cold), s)
        s = np.where((b >= o_cold) & (b <= o_warm), 1.0, s)   # optimal plateau
        if r_warm > o_warm:                                   # warm margin o_warm..r_warm -> 1..0
            m = (b > o_warm) & (b <= r_warm)
            s = np.where(m, (r_warm - b) / (r_warm - o_warm), s)
        s = np.where(np.isnan(b), 0.0, s)
    return np.clip(s, 0.0, 1.0)
