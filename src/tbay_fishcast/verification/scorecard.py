"""Skill scorecard — the numbers the commissioning gates are read from (PLAN 5).

G1 = temperature MAE (this module, real). G2 = event POD/FAR (this module, real
contingency math). G3/G4 are computed by comparing these outputs against the
held-out-year thresholds; that comparison harness lands with the backfill data.

Truth-proxy caveat (Phase-0 limitation, documented): the only reachable water-temp
truths are GLSEA surface SST and the Slate Island buoy (~165 km E, 1 m). Both are
SURFACE references; the gates specify 6 m. Until an in-situ logger exists, G1/G2
are evaluated against surface proxies with an explicit depth caveat, NOT against
true 6 m observations. `depth_caveat` is carried on every result so no scorecard
is ever read as a clean 6 m verification.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ErrorStats:
    n: int
    mae: float
    bias: float  # mean(model - truth)
    rmse: float
    median_abs: float  # median |error| — robust to outliers
    p90_abs: float     # 90th-percentile |error| — tail behaviour
    pearson_r: float   # model-truth correlation (tracks the signal, not just level)
    depth_caveat: str


@dataclass(frozen=True)
class Contingency:
    hits: int          # event forecast & observed
    misses: int        # observed, not forecast
    false_alarms: int  # forecast, not observed
    correct_neg: int

    @property
    def pod(self) -> float:
        """Probability of detection = hits / (hits + misses)."""
        denom = self.hits + self.misses
        return float("nan") if denom == 0 else self.hits / denom

    @property
    def far(self) -> float:
        """False-alarm ratio = false_alarms / (hits + false_alarms)."""
        denom = self.hits + self.false_alarms
        return float("nan") if denom == 0 else self.false_alarms / denom


def temperature_error(
    model_c, truth_c, *, depth_caveat: str = "surface-proxy vs 6 m target"
) -> ErrorStats:
    """MAE / bias / RMSE of model vs truth, NaNs dropped pairwise (G1)."""
    m = np.asarray(model_c, dtype=float)
    o = np.asarray(truth_c, dtype=float)
    if m.shape != o.shape:
        raise ValueError("model and truth must have matching shape")
    valid = ~(np.isnan(m) | np.isnan(o))
    m, o = m[valid], o[valid]
    nan = float("nan")
    if m.size == 0:
        return ErrorStats(0, nan, nan, nan, nan, nan, nan, depth_caveat)
    diff = m - o
    abs_diff = np.abs(diff)
    # Pearson r needs variance in both series and >=2 points.
    if m.size >= 2 and np.std(m) > 0 and np.std(o) > 0:
        r = float(np.corrcoef(m, o)[0, 1])
    else:
        r = nan
    return ErrorStats(
        n=int(m.size),
        mae=float(np.mean(abs_diff)),
        bias=float(np.mean(diff)),
        rmse=float(np.sqrt(np.mean(diff**2))),
        median_abs=float(np.median(abs_diff)),
        p90_abs=float(np.percentile(abs_diff, 90)),
        pearson_r=r,
        depth_caveat=depth_caveat,
    )


def contingency(forecast_days: set, observed_days: set, all_days: set) -> Contingency:
    """Build a 2x2 event contingency table from day-sets (G2)."""
    forecast_days = set(forecast_days) & all_days
    observed_days = set(observed_days) & all_days
    hits = len(forecast_days & observed_days)
    misses = len(observed_days - forecast_days)
    false_alarms = len(forecast_days - observed_days)
    correct_neg = len(all_days - (forecast_days | observed_days))
    return Contingency(hits, misses, false_alarms, correct_neg)


def anomaly_correlation(model, truth) -> float:
    """Correlation of the DETRENDED series — timing skill, not just tracking the trend.

    Removes each series' own linear trend before correlating, so a model that only
    reproduces the seasonal warm-up scores ~0 here; it must get the wiggles
    (upwelling, turning points) right. Inputs are assumed time-ordered. NaN if <4
    points or a series is flat after detrending.
    """
    m = np.asarray(model, dtype=float)
    t = np.asarray(truth, dtype=float)
    if len(m) < 4 or len(m) != len(t):
        return float("nan")
    x = np.arange(len(m), dtype=float)
    md = m - np.polyval(np.polyfit(x, m, 1), x)
    td = t - np.polyval(np.polyfit(x, t, 1), x)
    if np.std(md) == 0 or np.std(td) == 0:
        return float("nan")
    return float(np.corrcoef(md, td)[0, 1])


def leave_one_out_mae(groups: dict) -> dict:
    """Additive-bias correction generalization across held-out groups (e.g. buoys).

    groups maps key -> (model_array, truth_array). For each key, fit the correction
    (mean model-minus-truth bias) on ALL OTHER keys, apply it to the held-out key,
    and return {key: held_out_MAE}. This is the transfer test for a site with no
    local truth (Thunder Bay): never train and score on the same location.
    """
    biases = {k: float(np.mean(np.asarray(mdl, float) - np.asarray(tr, float)))
              for k, (mdl, tr) in groups.items()}
    out = {}
    for held, (mdl, tr) in groups.items():
        others = [b for k, b in biases.items() if k != held]
        corr = float(np.mean(others)) if others else 0.0
        pred = np.asarray(mdl, float) - corr
        out[held] = float(np.mean(np.abs(pred - np.asarray(tr, float))))
    return out


def band_coverage(model, truth, lo_bias: float, hi_bias: float) -> float:
    """Fraction of truth points inside the corrected uncertainty band.

    The band removes a warm bias between lo_bias and hi_bias: corrected temperature
    lies in [model - hi_bias, model - lo_bias]. Returns the share of truth values
    that fall inside — a calibration check (should be ~0.68 for a ±1σ band).
    """
    m = np.asarray(model, dtype=float)
    t = np.asarray(truth, dtype=float)
    if len(m) == 0:
        return float("nan")
    inside = (t >= m - hi_bias) & (t <= m - lo_bias)
    return float(np.mean(inside))
