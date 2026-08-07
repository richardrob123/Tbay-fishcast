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
    to 0 at the edges of the preferred range. This is the map's graded zones.
  * upwelling_favorability — a CONTINUOUS response of upwelling strength to wind speed,
    replacing the arbitrary binary ≥13 kt cutoff. Physics-backed (Wedderburn control; Li et
    al. 2021 wind-vs-cooling r=-0.87). Its centre/width are CALIBRATED to our own observed
    wind↔nearshore-cooling record where available (scripts/calibrate_upwelling.py), not picked.

The upwelling PHASE (features/upwelling_phase.py) stays a SEPARATE, physically-grounded
temporal signal — not multiplied into the spatial score, because how strongly a phase moves
the actual bite is a catch-outcome question we have no data to answer yet. The fusion of these
signals into one probability is deferred to when the pre-registered field-session logs exist,
so it can be FIT and temporal-split validated instead of guessed (CLAUDE rule 7).

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
