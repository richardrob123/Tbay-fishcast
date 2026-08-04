"""Wedderburn number — wind-driven upwelling susceptibility (PLAN task 4 physics).

W = g' h² / (u*² L)
  g'  reduced gravity across the thermocline  [m s-2]
  h   mixed-layer (epilimnion) depth          [m]
  u*  water-side friction velocity            [m s-1]
  L   basin fetch                             [m]

W < 1  => wind stress overwhelms stratification => tilt/upwelling favorable.
Seed anchors: W~1 near 12 kt sustained at h=10 m, rising to ~17 kt at h=14 m;
fetch ~24-25 km. Fish physics: W-quadrant wind tilts the thermocline and pushes
cold hypolimnion water up the city/north shore.

Pure and deterministic — the CALCULATION needs no network. Live wind to drive it
comes from ERA5 (currently egress-blocked). Constants below are PROVISIONAL and
must be calibrated so W(12 kt, h=10 m) ~ 1 against the ERA5/LSOFS record before any
threshold is trusted (ADR-004).
"""
from __future__ import annotations

import math

RHO_AIR = 1.22        # kg m-3
RHO_WATER = 1000.0    # kg m-3
G = 9.81              # m s-2
CD_AIR = 1.3e-3       # neutral 10 m drag coefficient
FETCH_M = 24_500.0    # Thunder Bay fetch, seed corpus
KN_TO_MS = 0.514444

# Provisional thermocline density contrast for a stratified August column.
# ~ρ(4°C) vs ρ(18°C): Δρ/ρ ≈ 1.5e-3 -> g' ≈ 0.0147 m s-2.
DEFAULT_G_PRIME = 0.0147


def friction_velocity_water(wind_speed_ms: float, cd: float = CD_AIR) -> float:
    """Water-side u* from 10 m wind: τ = ρ_air Cd U²; u* = sqrt(τ/ρ_water)."""
    tau = RHO_AIR * cd * wind_speed_ms**2
    return math.sqrt(tau / RHO_WATER)


def wedderburn_number(
    wind_speed_ms: float,
    mixed_layer_m: float,
    *,
    g_prime: float = DEFAULT_G_PRIME,
    fetch_m: float = FETCH_M,
) -> float:
    """Compute W. Returns +inf for calm wind (no tilt)."""
    if wind_speed_ms <= 0:
        return float("inf")
    if mixed_layer_m <= 0:
        raise ValueError("mixed_layer_m must be positive")
    ustar = friction_velocity_water(wind_speed_ms)
    return (g_prime * mixed_layer_m**2) / (ustar**2 * fetch_m)


def upwelling_favorable(
    wind_speed_kn: float,
    mixed_layer_m: float,
    *,
    threshold: float = 1.0,
    **kw,
) -> bool:
    """True when W <= threshold (stratification overwhelmed => upwelling likely)."""
    w = wedderburn_number(wind_speed_kn * KN_TO_MS, mixed_layer_m, **kw)
    return w <= threshold
