"""FVCOM sigma-layer -> fixed-depth temperature interpolation.

LSOFS is FVCOM: temperature lives on terrain-following sigma layers, not fixed
depths. Verified against a real file (lsofs.t00z.20260804.fields.n000.nc):

  * temp dims = (time, siglay, node); siglay = 20 layers.
  * siglay values are NEGATIVE, uniform per column: -0.025 (near surface) ...
    -0.975 (near bottom). They are fractions of the local water column.
  * node bathymetry `h` (m, positive down); free surface `zeta` (m).

Depth (m, positive down) of sigma layer k at node n, time t:
    z_k = -siglay[k] * (h + zeta)
Surface layer (-0.025) sits at ~2.5% of the column; bottom (-0.975) at ~97.5%.

To read temperature at a fixed target depth d (e.g. the operator's 2/6/10 m band)
we linearly interpolate temp between the two sigma layers that bracket d, and
clamp at the surface-most / bottom-most layer outside their span.

Pure numpy. No I/O. This is the piece the property tests pin (CLAUDE rule 2):
  - interpolated temp is bounded by the two bracketing layer temps;
  - a target depth below the deepest layer clamps to the bottom-layer temp;
  - a target depth above the shallowest layer clamps to the surface-layer temp.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ColumnResult:
    depths_m: np.ndarray  # requested target depths
    temp_c: np.ndarray  # interpolated temperature at each target depth
    clamped: np.ndarray  # bool: target was outside the layer span (extrapolation avoided)
    water_column_m: float  # total depth used (h + zeta)


def layer_depths(siglay: np.ndarray, h: float, zeta: float = 0.0) -> np.ndarray:
    """Depth (m, positive down) of each sigma layer for one node column.

    siglay: 1-D array of negative sigma fractions (surface -> bottom).
    Returns depths in the SAME order as siglay (surface-most first).
    """
    siglay = np.asarray(siglay, dtype=float)
    if np.any(siglay > 0):
        raise ValueError("siglay must be <= 0 (FVCOM convention: negative down)")
    total = float(h) + float(zeta)
    if total <= 0:
        raise ValueError(f"non-positive water column: h={h}, zeta={zeta}")
    return -siglay * total


def interp_column(
    temp_profile: np.ndarray,
    siglay: np.ndarray,
    h: float,
    target_depths_m,
    zeta: float = 0.0,
) -> ColumnResult:
    """Interpolate a single node's temperature profile onto fixed target depths.

    temp_profile: 1-D temp per sigma layer (surface-most first), length = len(siglay).
    Returns a ColumnResult; `clamped[i]` is True when target i fell outside the
    layer-depth span and was clamped to the nearest layer (no extrapolation).
    """
    temp_profile = np.asarray(temp_profile, dtype=float)
    siglay = np.asarray(siglay, dtype=float)
    targets = np.atleast_1d(np.asarray(target_depths_m, dtype=float))
    if temp_profile.shape != siglay.shape:
        raise ValueError("temp_profile and siglay must have identical shape")

    zc = layer_depths(siglay, h, zeta)  # depths, surface-most first (ascending depth)
    total = float(h) + float(zeta)

    # Ensure ascending depth order for np.interp (surface -> bottom).
    order = np.argsort(zc)
    zc_s = zc[order]
    tp_s = temp_profile[order]

    shallowest, deepest = zc_s[0], zc_s[-1]
    # np.interp already clamps to endpoint values outside [shallowest, deepest].
    temp = np.interp(targets, zc_s, tp_s)
    clamped = (targets < shallowest) | (targets > deepest)

    return ColumnResult(
        depths_m=targets,
        temp_c=temp,
        clamped=clamped,
        water_column_m=total,
    )
