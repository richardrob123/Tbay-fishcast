"""Bias correction — calibrate LSOFS to in-situ truth (buoy validation finding).

LSOFS carries a systematic warm bias at mid-depth (+2.4…3.6 °C, replicated across
independent Superior buoys — docs/BUOY_VALIDATION.md). The error is bias-dominated, so a
single additive offset per station removes most of it. A constant offset does NOT change
the anomaly/timing signal (the actionable part) — it only fixes the absolute level.

The offset MUST be fit against co-located in-situ observations (a buoy or logger). It is
therefore station-specific and unknown until such data exists — Thunder Bay stations carry
`None` until their logger is deployed (never a guessed value silently applied).

Pure, deterministic. No LLM.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class BiasCorrection:
    """Additive offset: corrected = raw + offset_c, where offset_c = mean(obs − raw)."""
    offset_c: float
    n_fit: int
    fit_note: str = ""

    @classmethod
    def fit(cls, raw, obs, fit_note: str = "") -> "BiasCorrection":
        raw = np.asarray(raw, dtype=float)
        obs = np.asarray(obs, dtype=float)
        if raw.shape != obs.shape:
            raise ValueError("raw and obs must have the same shape")
        valid = ~(np.isnan(raw) | np.isnan(obs))
        if valid.sum() == 0:
            raise ValueError("no valid paired samples to fit")
        return cls(offset_c=float(np.mean(obs[valid] - raw[valid])),
                   n_fit=int(valid.sum()), fit_note=fit_note)

    def apply(self, raw):
        return np.asarray(raw, dtype=float) + self.offset_c
