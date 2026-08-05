"""Data-driven vertical temperature correction and isotherm-depth band.

The cross-shore product hinges on ONE number: the depth of the target isotherm
(e.g. 12 °C for lakers). Raw LSOFS gets that wrong because it runs warm below the
surface. The correction here is anchored at BOTH ends by observations, not a guess:

  * Surface (z=0): anchored to GLSEA satellite SST — real, local, daily. Measured
    LSOFS surface bias vs satellite is small (0–1 °C, sometimes slightly cool).
  * Subsurface (z≈z_ref): LSOFS runs +2…5 °C warm, measured live against NDBC
    subsurface thermistors (like-for-like depth, fresh records only). This is the
    uncertain leg — the buoys are basin-wide, not in Thunder Bay — so its spread is
    carried through as a BAND on the isotherm depth rather than hidden.

Correction c(z) tapers linearly from the surface anchor to the subsurface value and
holds constant below z_ref. corrected = raw + c(z). Because the subsurface bias has
real spread, `isotherm_band` returns (shallow, central, deep) isotherm depths so the
map can show honest uncertainty instead of false precision.

Pure and deterministic (no network); the live buoy/satellite measurement lives in
the caller. No LLM.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .cross_shore import isotherm_depth


@dataclass(frozen=True)
class BiasModel:
    """Depth-dependent LSOFS temperature bias (LSOFS minus truth, °C, positive=warm)."""
    surface_bias_c: float          # LSOFS_surf - GLSEA_sst (can be negative)
    subsurface_bias_c: float       # central LSOFS - buoy at z_ref (positive=warm)
    subsurface_bias_lo_c: float    # low end of measured subsurface bias
    subsurface_bias_hi_c: float    # high end of measured subsurface bias
    z_ref_m: float = 6.0
    n_buoys: int = 0

    def correction(self, z, which: str = "central"):
        """c(z) to ADD to raw LSOFS. Tapers surface->subsurface, flat below z_ref."""
        sub = {"central": self.subsurface_bias_c,
               "lo": self.subsurface_bias_lo_c,
               "hi": self.subsurface_bias_hi_c}[which]
        c_surf = -self.surface_bias_c   # remove surface bias
        c_sub = -sub                    # remove subsurface bias
        z = np.asarray(z, dtype=float)
        frac = np.clip(z / self.z_ref_m, 0.0, 1.0)
        return c_surf + (c_sub - c_surf) * frac


def summarize_subsurface_bias(samples: list[float]) -> tuple[float, float, float]:
    """(central, lo, hi) from per-buoy subsurface bias samples.

    Central = median (robust to a single outlier buoy); lo/hi = min/max of the
    samples, i.e. the observed spread, which becomes the isotherm band width.
    """
    if not samples:
        return (0.0, 0.0, 0.0)
    s = sorted(samples)
    central = float(np.median(s))
    return central, float(s[0]), float(s[-1])


def corrected_profile(depths, raw_temps, bias: BiasModel, which: str = "central"):
    z = np.asarray(depths, dtype=float)
    return list(np.asarray(raw_temps, dtype=float) + bias.correction(z, which))


def isotherm_band(depths, raw_temps, bias: BiasModel, target_c: float,
                  colder_is_target: bool = True) -> dict:
    """Isotherm depth under central + both bias extremes -> honest band.

    Warmer subsurface (hi bias -> larger negative correction) cools the column more
    and shoals the isotherm; cooler (lo) deepens it. Returns shallow/central/deep
    (sorted), with None entries where the band edge has no crossing.
    """
    zc = isotherm_depth(depths, corrected_profile(depths, raw_temps, bias, "central"),
                        target_c, colder_is_target)
    z_lo = isotherm_depth(depths, corrected_profile(depths, raw_temps, bias, "lo"),
                          target_c, colder_is_target)
    z_hi = isotherm_depth(depths, corrected_profile(depths, raw_temps, bias, "hi"),
                          target_c, colder_is_target)
    edges = [z for z in (z_lo, z_hi) if z is not None]
    shallow = min(edges) if edges else None
    deep = max(edges) if edges else None
    return {"central": zc, "shallow": shallow, "deep": deep,
            "target_c": target_c, "n_buoys": bias.n_buoys}
