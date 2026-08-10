"""Cross-shore transect — the actual product (operator's framing, 2026-08-05).

Not "temperature at a fixed depth." The question is: WHERE (distance from shore) does the
target species' temperature band sit today, and is that within casting range?

Compose two things:
  1. LSOFS bias-corrected temperature PROFILE (temp vs depth) at the station's near node.
  2. Per-spot BATHYMETRY (depth vs distance from shore) — a contour map / soundings.

From the profile, find the depth of the target isotherm (e.g. the 12 °C laker ceiling).
From the bathymetry, find the distance from shore where the bottom reaches that depth —
that is where the target-temperature water meets the fishable slope. If that distance is
inside cast range, the fish are reachable from shore. Upwelling shoals the cold water, so
the distance shrinks and the spot flips "in range" — exactly the decision to flag.

Proxy, not survey: LSOFS (200 m–2.5 km cells) can't resolve structure inside the cast zone,
so this assumes the temp-vs-depth profile is ~horizontally uniform over the near-shore
stretch. Good for "shallow & close vs deep & far", not a metre-accurate map.

Pure, deterministic, unit-tested. No LLM.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Transect:
    target_c: float
    colder_is_target: bool
    isotherm_depth_m: float | None      # depth where the profile crosses target_c
    distance_from_shore_m: float | None  # where the bottom reaches that depth
    in_cast_range: bool
    cast_range_m: float
    note: str


def isotherm_crossing(depths, temps, target_c: float, colder_is_target: bool = True):
    """(depth, status) — the same search as :func:`isotherm_depth`, but SAYING when it was censored.

    WHY THIS EXISTS (ADR-048). ``isotherm_depth`` returns the TOP SENSOR DEPTH when the whole
    column is already at/below the target. For the map that is the right answer and deliberately
    so — "the cold water reaches the surface" is a real, useful statement about upwelling. As a
    VALIDATION REFERENCE it is a fabrication: it is not an observed isotherm depth, it is the
    bound "the isotherm is at or above the shallowest thermistor", and differencing a forecast
    against it measures the distance to the top of the chain rather than to any isotherm.

    This is not hypothetical. Every 2026-08 forecast-gate row from the Duluth LLO1 chain read
    obs_iso_m = 3.00 — exactly its shallowest sensor — because its 3-38 m column runs 4.0-8.2 °C
    in August and never reaches 12 °C. 30 of 35 accumulated rows were that bound, and the pooled
    MAE the map showed as a +/- metres band was computed from them. Note the asymmetry that hid
    it: a column too WARM to cross the target already returned None and was skipped, so only the
    too-cold direction leaked through.

    status is one of:
      "crossing"     — the profile genuinely crosses target_c; depth is a measurement
      "above_top"    — the whole column is already past the target; the isotherm is at or above
                       depths[0]. Depth is returned (for the map) but it is a BOUND, not a datum.
      "below_bottom" — the column never reaches the target within the profiled range; depth None
    """
    z = np.asarray(depths, dtype=float)
    t = np.asarray(temps, dtype=float)
    if z.size == 0 or t.size == 0:
        return None, "below_bottom"
    order = np.argsort(z)
    z, t = z[order], t[order]
    for i in range(len(z) - 1):
        t0, t1 = t[i], t[i + 1]
        if (t0 - target_c) == 0:
            return float(z[i]), "crossing"
        if (t1 - target_c) == 0:
            return float(z[i + 1]), "crossing"
        if (t0 - target_c) * (t1 - target_c) < 0:
            frac = (target_c - t0) / (t1 - t0)
            return float(z[i] + frac * (z[i + 1] - z[i])), "crossing"
    if colder_is_target:
        if np.min(t) > target_c:
            return None, "below_bottom"
        return float(z[0]), "above_top"
    if np.max(t) < target_c:
        return None, "below_bottom"
    return float(z[0]), "above_top"


def isotherm_depth(depths, temps, target_c: float, colder_is_target: bool = True) -> float | None:
    """Shallowest depth at which the profile crosses `target_c`.

    depths ascending (surface->down), temps matching. `colder_is_target=True` (lakers,
    cold-water) finds where temp drops THROUGH target going down; False (warm-water,
    walleye) finds where it rises through / the band's top. Returns None if the column never
    reaches the target, and the TOP DEPTH if the whole column is already past it (strong
    upwelling: the target water reaches the surface, so the isotherm is at the top, not the
    bottom) — deliberate for the map, and a censored BOUND rather than a measurement.

    Callers that are validating a forecast must use :func:`isotherm_crossing` and keep only
    ``status == "crossing"``; see ADR-048 for the 30 fabricated gate rows this distinction
    exists to prevent.
    """
    return isotherm_crossing(depths, temps, target_c, colder_is_target)[0]


def depth_to_distance(bathy_dist, bathy_depth, target_depth_m: float) -> float | None:
    """Distance from shore where the bottom first reaches `target_depth_m`.

    bathy_* are matched arrays, distance ascending, depth generally increasing offshore.
    Returns None if the bottom never gets that deep within the mapped range.
    """
    d = np.asarray(bathy_dist, dtype=float)
    h = np.asarray(bathy_depth, dtype=float)
    order = np.argsort(d)
    d, h = d[order], h[order]
    if target_depth_m <= h[0]:
        return float(d[0])
    for i in range(len(d) - 1):
        if (h[i] - target_depth_m) * (h[i + 1] - target_depth_m) <= 0 and h[i + 1] != h[i]:
            frac = (target_depth_m - h[i]) / (h[i + 1] - h[i])
            return float(d[i] + frac * (d[i + 1] - d[i]))
    return None  # bottom never reaches that depth in range


def evaluate(depths, temps, bathy_dist, bathy_depth, *, target_c: float,
             cast_range_m: float, colder_is_target: bool = True) -> Transect:
    zt = isotherm_depth(depths, temps, target_c, colder_is_target)
    if zt is None:
        return Transect(target_c, colder_is_target, None, None, False, cast_range_m,
                        note="target band not present in the water column")
    dist = depth_to_distance(bathy_dist, bathy_depth, zt)
    if dist is None:
        return Transect(target_c, colder_is_target, zt, None, False, cast_range_m,
                        note=f"{target_c:.0f}C at {zt:.1f} m, but bottom never that deep in mapped range")
    in_range = dist <= cast_range_m
    return Transect(target_c, colder_is_target, zt, dist, in_range, cast_range_m,
                    note=f"{target_c:.0f}C water meets bottom at {dist:.0f} m from shore "
                         f"({'IN' if in_range else 'out of'} cast range {cast_range_m:.0f} m)")
