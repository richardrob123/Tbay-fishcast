"""Forecast skill on TEMPERATURE, with honest uncertainty on the skill itself (ADR-049).

WHY TEMPERATURE AND NOT ISOTHERM DEPTH. The gate this replaces scored the depth of the 12 C
isotherm. That quantity is derived, censored, and — the part that matters most — its UNITS FLOAT.
Sensitivity is dz = dT / |dT/dz|, so the same 0.5 C model error is 10 cm across a sharp
thermocline and 30 m in a weakly stratified column. A "mean absolute error in metres" pooled
across days is therefore a number whose meaning rescales with whatever stratification happened to
be present — and it is biased OPTIMISTIC, because the days that produce a crossing at all are the
sharply stratified ones where the conversion factor is smallest. Scoring in C fixes all three:
never censored, stable units, ~10 samples per profile instead of at most one.

The metres the map actually needs come back in :func:`isotherm_depth_sigma`, computed per forecast
from that day's own gradient rather than pooled across days.

THE FOUR THINGS THAT MAKE A SKILL NUMBER TRUSTWORTHY, all implemented here:

  1. PAIRED SAMPLES. Forecast and baseline are scored on exactly the same (day, lead, depth)
     rows; a row missing either side is dropped from both. Comparing a pooled MAE against a
     differently-pooled MAE is the commonest way to manufacture skill.
  2. THE HARDER BASELINE. Persistence is hard at short lead, climatology at long lead. Scoring
     against whichever is WORSE lets you claim skill by choosing the weak reference, so the
     no-skill reference is the better of the two on each sample.
  3. INDEPENDENT SAMPLE SIZE. Five leads x ten depths on one day is 50 rows and roughly ONE
     independent observation, and consecutive days at a mooring stay correlated for days. The
     bootstrap resamples BLOCKS OF DAYS, with the block length measured from the error series'
     own autocorrelation rather than chosen.
  4. AN INTERVAL, NOT A POINT. A skill ratio of 0.9 on seven independent days is indistinguishable
     from 1.1. Benching a layer under the demotion rule (ADR-006) requires the interval to exclude
     1.0 — otherwise the rule fires on noise, which is worse than not having the rule.

Pure and deterministic: no network, no LLM, seeded bootstrap.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict

BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 20260810


def mae(v) -> float:
    v = [x for x in v if x is not None and math.isfinite(x)]
    return sum(abs(x) for x in v) / len(v) if v else float("nan")


def rmse(v) -> float:
    v = [x for x in v if x is not None and math.isfinite(x)]
    return math.sqrt(sum(x * x for x in v) / len(v)) if v else float("nan")


def bias(v) -> float:
    v = [x for x in v if x is not None and math.isfinite(x)]
    return sum(v) / len(v) if v else float("nan")


def autocorr(series) -> list[float]:
    """ACF of a mean-centred series, lags 0..n//2. Used to MEASURE the block length."""
    x = [v for v in series if v is not None and math.isfinite(v)]
    n = len(x)
    if n < 4:
        return [1.0]
    m = sum(x) / n
    d = [v - m for v in x]
    c0 = sum(v * v for v in d) / n
    if c0 <= 0:
        return [1.0]
    return [sum(d[i] * d[i + k] for i in range(n - k)) / n / c0
            for k in range(0, max(2, n // 2))]


def decorrelation_days(err_by_day: dict, max_block: int = 10) -> int:
    """Block length for the bootstrap: the first lag at which the daily error ACF falls below 1/e.

    Chosen from the data rather than asserted. On this lake the physical expectation is a few
    days — the seiche is ~40 h and synoptic forcing runs 2-5 d — so a result far outside that is
    itself worth noticing. Falls back to 1 when the series is too short to estimate anything,
    which is the ANTI-conservative direction, so the returned value is reported alongside the CI.
    """
    days = sorted(err_by_day)
    series = [bias(err_by_day[d]) for d in days]
    acf = autocorr(series)
    thresh = 1.0 / math.e
    for k in range(1, len(acf)):
        if acf[k] < thresh:
            return max(1, min(max_block, k))
    return max(1, min(max_block, len(acf) - 1)) if len(acf) > 1 else 1


def _blocks(days, block):
    return [days[i:i + block] for i in range(0, len(days), block)] or [[]]


MIN_BLOCKS = 3      # below this a percentile interval is an artefact of the resampling grid


def sign_test_days(pairs):
    """Non-parametric day-level comparison: on how many DAYS is the forecast better?

    THE MOST ROBUST STATEMENT AVAILABLE, and the reason it is here: the bootstrap interval assumes
    the block length captures the error's memory, and a systematic model bias can stay correlated
    for longer than any block we can estimate. A sign test over days assumes nothing about
    magnitude or correlation structure — it asks only which side won each day. When the two
    disagree, believe this one.

    Returns ``{wins, losses, ties, n_days, win_rate, p_two_sided}``.
    """
    from math import comb

    by_day = defaultdict(lambda: ([], []))
    for d, fe, be in pairs:
        if fe is None or be is None or not (math.isfinite(fe) and math.isfinite(be)):
            continue
        by_day[d][0].append(abs(fe))
        by_day[d][1].append(abs(be))
    wins = losses = ties = 0
    for d, (f, b) in by_day.items():
        mf, mb = mae(f), mae(b)
        if not (math.isfinite(mf) and math.isfinite(mb)):
            continue
        wins += mf < mb
        losses += mf > mb
        ties += mf == mb
    n = wins + losses
    if n == 0:
        return {"wins": wins, "losses": losses, "ties": ties, "n_days": len(by_day),
                "win_rate": None, "p_two_sided": None}
    k = min(wins, losses)
    p = min(1.0, 2.0 * sum(comb(n, i) for i in range(k + 1)) / (2.0 ** n))
    return {"wins": wins, "losses": losses, "ties": ties, "n_days": len(by_day),
            "win_rate": round(wins / n, 4), "p_two_sided": p}


def block_bootstrap_ratio(pairs, *, block_days: int, n_boot: int = BOOTSTRAP_N,
                          seed: int = BOOTSTRAP_SEED):
    """CI on MAE(forecast)/MAE(baseline) by resampling contiguous BLOCKS OF DAYS.

    ``pairs`` is [(day_key, fcst_err, base_err)]. Returns
    ``{ratio, lo, hi, n, n_days, block_days, beats, verdict}`` where `beats` is True only when the
    whole interval sits below 1.0 — the ADR-006 bar — and `verdict` says which way the evidence
    points, including "inconclusive", which is the honest answer most of the time at small n.
    """
    by_day = defaultdict(list)
    for d, fe, be in pairs:
        if fe is None or be is None or not (math.isfinite(fe) and math.isfinite(be)):
            continue
        by_day[d].append((fe, be))
    days = sorted(by_day)
    n = sum(len(v) for v in by_day.values())
    if not days or n == 0:
        return {"ratio": None, "lo": None, "hi": None, "n": 0, "n_days": 0,
                "block_days": block_days, "beats": False, "verdict": "no sample"}

    def ratio_of(daylist):
        f = [x for d in daylist for x, _ in by_day[d]]
        b = [y for d in daylist for _, y in by_day[d]]
        if not f:
            return None
        mb = mae(b)
        return (mae(f) / mb) if mb > 0 else None

    point = ratio_of(days)
    blocks = _blocks(days, max(1, block_days))
    rng = random.Random(seed)
    draws = []
    for _ in range(n_boot):
        pick = []
        while len(pick) < len(days) and blocks:
            pick.extend(blocks[rng.randrange(len(blocks))])
        r = ratio_of(pick[:len(days)])
        if r is not None and math.isfinite(r):
            draws.append(r)
    draws.sort()
    n_blocks = len(blocks)
    # DEGENERATE-BOOTSTRAP GUARD, and it was a real bug caught by its own test. With ONE block
    # every resample is the same sample, so every draw is identical, the interval collapses to a
    # point, and a point below 1.0 was being reported as a proven win — false certainty from 500
    # rows that carried one day of information. Refuse an interval when there are too few blocks
    # or when the draws carry no spread at all.
    spread = (draws[-1] - draws[0]) if draws else 0.0
    degenerate = n_blocks < MIN_BLOCKS or spread <= 0.0
    if degenerate:
        return {"ratio": round(point, 4) if point is not None else None,
                "lo": None, "hi": None, "n": n, "n_days": len(days),
                "block_days": block_days, "n_blocks": n_blocks,
                "interval_quality": "none", "beats": False,
                "verdict": f"insufficient independent blocks ({n_blocks})"}
    lo = draws[int(0.025 * (len(draws) - 1))]
    hi = draws[int(0.975 * (len(draws) - 1))]
    beats = bool(hi < 1.0)
    loses = bool(lo > 1.0)
    verdict = ("beats the baseline" if beats else
               "NO BETTER than the baseline" if loses else "inconclusive")
    return {"ratio": round(point, 4) if point is not None else None,
            "lo": round(lo, 4) if lo is not None else None,
            "hi": round(hi, 4) if hi is not None else None,
            "n": n, "n_days": len(days), "block_days": block_days, "n_blocks": n_blocks,
            # A 2.5% tail needs ~40 blocks to be resolved rather than approximated. Say which
            # this is instead of letting a bracket imply a precision it does not have.
            "interval_quality": "resolved" if n_blocks >= 40 else "approximate",
            "beats": beats, "verdict": verdict}


def best_baseline(persist_err, clim_err):
    """The HARDER of the two references, per sample.

    Persistence is hard at short lead and climatology at long lead. Scoring against whichever
    happens to be worse on a given sample is how a forecast is made to look skilful; taking the
    smaller error makes the bar the best cheap alternative available at that moment. Where only
    one exists it is used, and the caller is told how often that happened.
    """
    if persist_err is None or not math.isfinite(persist_err):
        return clim_err, "climatology"
    if clim_err is None or not math.isfinite(clim_err):
        return persist_err, "persistence"
    return ((persist_err, "persistence") if abs(persist_err) <= abs(clim_err)
            else (clim_err, "climatology"))


def sigma_by_lead_depth(rows, *, depth_bins=None):
    """sigma_T (RMSE, C) per (lead, depth bin) — the input to the metres conversion.

    RMSE rather than MAE on purpose: this number is used as a Gaussian-ish scale in
    :func:`isotherm_depth_sigma`, and MAE would understate a distribution with tails.
    """
    depth_bins = depth_bins or [0, 5, 10, 15, 20, 30, 45, 1000]
    out = defaultdict(list)
    for r in rows:
        e, L, z = r.get("err_c"), r.get("lead_h"), r.get("depth_m")
        if e is None or L is None or z is None or not math.isfinite(e):
            continue
        b = next((i for i in range(len(depth_bins) - 1)
                  if depth_bins[i] <= z < depth_bins[i + 1]), None)
        if b is not None:
            out[(int(L), b)].append(e)
    return {f"{L}|{b}": {"lead_h": L, "depth_lo": depth_bins[b], "depth_hi": depth_bins[b + 1],
                         "n": len(v), "rmse_c": round(rmse(v), 4),
                         "bias_c": round(bias(v), 4)}
            for (L, b), v in sorted(out.items())}


def local_gradient(depths, temps, at_depth_m: float) -> float | None:
    """|dT/dz| (C per metre) around a depth, from the bracketing layers of THIS profile."""
    if not depths or len(depths) != len(temps) or len(depths) < 2:
        return None
    pairs = sorted(zip(depths, temps))
    z = [p[0] for p in pairs]
    t = [p[1] for p in pairs]
    if at_depth_m < z[0] or at_depth_m > z[-1]:
        return None
    for i in range(len(z) - 1):
        if z[i] <= at_depth_m <= z[i + 1] and z[i + 1] > z[i]:
            return abs((t[i + 1] - t[i]) / (z[i + 1] - z[i]))
    return None


def depth_sigma_from_gradient(sigma_t_c: float | None, gradient_c_per_m: float | None,
                             max_m: float | None = None):
    """sigma_T / |dT/dz| -> depth uncertainty, given a gradient already measured on the profile.

    Returns ``(sigma_z_m, reason)``; sigma_z is None whenever the answer would be a number the
    map should not show — no measured sigma_T, a mixed column, or a band so wide it exceeds what
    a shore cast can reach, where "not constrained today" is both true and more useful than a
    figure that spans the whole fishable column.
    """
    if sigma_t_c is None or gradient_c_per_m is None:
        return None, "no measured temperature error for this lead/depth"
    if gradient_c_per_m <= 0 or not math.isfinite(gradient_c_per_m):
        return None, "column is mixed here — the depth of the cold water is not constrained"
    sz = sigma_t_c / gradient_c_per_m
    if max_m is not None and sz > max_m:
        return None, (f"weak stratification ({gradient_c_per_m:.2f} C/m) — the depth of the cold "
                      f"water is not constrained to better than {max_m:.0f} m today")
    return sz, "ok"


def isotherm_depth_sigma(depths, temps, iso_depth_m: float, sigma_t_c: float,
                         *, max_m: float | None = None):
    """Convert a TEMPERATURE error into an isotherm-DEPTH uncertainty, on this profile.

    dz = sigma_T / |dT/dz| at the isotherm. This is the whole point of scoring in C: the band is
    computed where it is used, from the stratification actually present, instead of pooling a
    quantity whose units move. On a sharply stratified day it is tight; on a weakly stratified one
    it is enormous — which is TRUE, and is the single most useful thing a shore angler can be told
    about where the cold water is.

    Returns ``(sigma_z_m, gradient_c_per_m, note)``. A vanishing gradient yields None rather than
    a huge number: "not constrained today" is a statement the map can make honestly, and a 400 m
    band on a 200 m lake is not.
    """
    g = local_gradient(depths, temps, iso_depth_m)
    if g is None:
        return None, None, "isotherm outside the profiled range"
    if g <= 0 or not math.isfinite(g):
        return None, g, "column is mixed here — isotherm depth is not constrained"
    sz = sigma_t_c / g
    if max_m is not None and sz > max_m:
        return None, g, (f"weak stratification ({g:.3f} C/m) — depth uncertainty exceeds "
                         f"{max_m:.0f} m, so no band is claimed")
    return sz, g, "ok"
