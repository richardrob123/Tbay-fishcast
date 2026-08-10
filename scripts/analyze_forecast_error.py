"""Measure the LSOFS thermal-forecast error vs lead — from the ACCUMULATED gate log, not a
guess. Answers one question the map needs: does the isotherm-depth (thermal-field position)
error grow with forecast lead, or is it roughly flat?

Input : data/forecast_gate_log.csv  (issue-time GLSEA-anchored forecast vs later observed
        isotherm depth; abs_err_m is the bias-CORRECTED error — what the map actually ships.)
Output: data/calib/forecast_lead_error.json  (pooled + per-lead MAE, a lead-trend test, and
        an honest caveat), committed as provenance for the uncertainty the UI displays.

Why this exists: the UI briefly faded far-lead thermal zones on a made-up `1 - lead/240`
decay. This script checks that assumption against the record. Rule 6/13: never present a
lead-decay we can't measure; report the number we HAVE with its (small) sample honestly.

Deterministic, offline, pure stdlib. No LLM.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "data" / "forecast_gate_log.csv"
OUT = ROOT / "data" / "calib" / "forecast_lead_error.json"


def _mae(xs):
    return sum(xs) / len(xs) if xs else None


def _p90(xs):
    if not xs:
        return None
    v = sorted(xs)
    return v[min(len(v) - 1, int(round(0.9 * (len(v) - 1))))]


def _linfit_slope(xy):
    """OLS slope of err vs lead (m per hour). A near-zero slope => no lead trend."""
    n = len(xy)
    if n < 2:
        return 0.0
    sx = sum(x for x, _ in xy)
    sy = sum(y for _, y in xy)
    sxx = sum(x * x for x, _ in xy)
    sxy = sum(x * y for x, y in xy)
    denom = n * sxx - sx * sx
    return (n * sxy - sx * sy) / denom if denom else 0.0


def main() -> int:
    if not LOG.exists():
        print(f"no gate log at {LOG}")
        return 1
    rows = list(csv.DictReader(LOG.open()))
    # ADR-048 QUARANTINE. Rows written before the censoring guard cannot be assumed to be
    # measurements. Two cases, and both are decidable from the CSV alone:
    #   * obs_status == "crossing"  -> written after the guard; a real crossing by construction.
    #   * obs_status blank          -> pre-guard. Quarantine the chain when its observed isotherm
    #                                  is CONSTANT ACROSS TWO OR MORE DISTINCT VALID DATES. That
    #                                  is the pinned-reference signature: when the whole column
    #                                  sits past the target, isotherm_depth returns depths[0]
    #                                  exactly, so every day reports the identical value — which
    #                                  is what LLO1 did (obs_iso_m = 3.00 = its shallowest
    #                                  thermistor, six days running).
    # Across DATES, not across rows: one valid date fans out into five lead rows that necessarily
    # share an observation, so a per-row constancy test would quarantine any chain with a single
    # logged day. It did, on the first version of this code — 45216 has one date, and its 8.25 m
    # sits between sensors, i.e. an interpolated crossing rather than a bound. With one date there
    # is no evidence of pinning either way, so the row survives here and is caught downstream by
    # the sample-size bar instead of by a rule reaching past its evidence.
    by_chain_obs = defaultdict(lambda: defaultdict(set))
    for r in rows:
        if not (r.get("obs_status") or "").strip():
            by_chain_obs[r.get("chain")][r.get("obs_iso_m")].add(r.get("valid_date"))
    quarantined = sorted(c for c, byobs in by_chain_obs.items()
                         if len(byobs) == 1
                         and len(next(iter(byobs.values()))) >= 2)
    rows_before = len(rows)
    rows = [r for r in rows
            if (r.get("obs_status") or "").strip() == "crossing"
            or r.get("chain") not in quarantined]
    if quarantined:
        print(f"ADR-048 quarantine: dropped {rows_before - len(rows)} pre-guard rows from "
              f"chain(s) {quarantined} — a constant observed isotherm depth is the top-sensor "
              f"bound, not a measurement")
    recs = []
    pairs = []          # (lead, fcst_err, persist_err) where BOTH exist — the skill sample
    for r in rows:
        try:
            recs.append((int(r["lead_h"]), float(r["abs_err_m"]), r["chain"], r["obs_iso_m"]))
        except (KeyError, ValueError):
            continue
        try:
            pairs.append((int(r["lead_h"]), float(r["abs_err_m"]),
                          float(r["persist_abs_err_m"])))
        except (KeyError, ValueError):
            pass
    if not recs:
        # WRITE THE EMPTY RESULT, do not bail. Returning early leaves the PREVIOUS
        # forecast_lead_error.json in place — which, the day this quarantine first ran, was the
        # fabricated 1.388 m band. A stale file that still says "measured" is worse than no file:
        # the map would go on publishing it with nothing to indicate the sample had vanished.
        reason = (f"no usable rows after the ADR-048 censoring quarantine "
                  f"(chains {quarantined} dropped: a constant observed isotherm depth is the "
                  f"top-sensor bound, not a measurement)" if quarantined
                  else "no usable rows (need lead_h, abs_err_m)")
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({
            "source": str(LOG.relative_to(ROOT)), "n": 0, "chains": [],
            "chains_with_moving_obs": [], "n_effective_chains": 0,
            "pooled_mae_m": None, "band_blocked_reason": reason, "pooled_p90_m": None,
            "per_lead": {}, "skill_vs_persistence": None,
            "skill_blocked_reason": reason, "quarantined_chains": quarantined,
            "lead_slope_m_per_h": None, "lead_trend_detected": False,
            "note": f"NO NUMERIC BAND: {reason}.",
        }, indent=2) + "\n")
        print(reason)
        print(f"wrote {OUT.relative_to(ROOT)} (empty)")
        return 0

    by_lead = defaultdict(list)
    for lead, err, _c, _o in recs:
        by_lead[lead].append(err)
    per_lead = {
        str(k): {"n": len(v), "mae_m": round(_mae(v), 3), "p90_m": round(_p90(v), 3)}
        for k, v in sorted(by_lead.items())
    }
    all_err = [e for _, e, _, _ in recs]
    slope = _linfit_slope([(lead, err) for lead, err, _, _ in recs])

    # Honesty on n_effective: the gate compares against each chain's observed isotherm depth;
    # if a chain's obs is a single constant value it pins the reference (measures absolute model
    # error, not skill across varying stratification). Count chains with a MOVING obs.
    obs_by_chain = defaultdict(set)
    for _l, _e, c, o in recs:
        obs_by_chain[c].add(o)
    moving = sorted(c for c, s in obs_by_chain.items() if len(s) > 1)
    chains = sorted(obs_by_chain)

    # Trend verdict: a lead trend is only "real" if the slope moves the error by more than the
    # per-lead scatter across the observed lead span. Compare |slope|*span to the spread of the
    # per-lead means. Small sample => we DECLINE to claim a trend unless it's unambiguous.
    leads = sorted(by_lead)
    span_h = (leads[-1] - leads[0]) if len(leads) > 1 else 0
    lead_means = [by_lead[k] and _mae(by_lead[k]) for k in leads]
    mean_spread = (max(lead_means) - min(lead_means)) if lead_means else 0.0
    trend_move = abs(slope) * span_h
    has_trend = bool(span_h and trend_move > mean_spread and len(all_err) >= 40)

    # THE PUBLISHED BAND IS GATED THE SAME WAY AS THE SKILL BLOCK, and for the same reason. After
    # the ADR-048 quarantine the usable sample can collapse to a handful of rows from a single day
    # at a single mooring, and a mean of five numbers rendered as "+/- 1.4 m isotherm depth" on the
    # map is not a measurement — it is the shape of one. Below the bar we publish null and the map
    # shows no numeric band, which is the honest state (rule 5: staleness and thinness are loud).
    MIN_BAND_N = 20
    pooled = round(_mae(all_err), 3) if len(all_err) >= MIN_BAND_N else None
    band_blocked = (None if pooled is not None else
                    f"only {len(all_err)} usable rows after the ADR-048 censoring quarantine "
                    f"(need {MIN_BAND_N}) — no numeric band is published")

    # SKILL vs the PERSISTENCE baseline (hindcast rows carry persist_abs_err_m): per lead,
    # MAE(forecast) / MAE(persistence) on the matched sample. Ratio < 1 = the forecast beats
    # "assume no change" at that lead; >= 1 at a lead = that lead adds nothing and is a
    # demotion candidate (ADR-006). Only reported when a real sample exists — never guessed.
    # THE SKILL BLOCK IS GATED TWICE, and the second gate is the one that matters. A ratio needs
    # a sample (>= 20 pairs) AND a reference that MOVES: with a pinned observed isotherm the
    # forecast is being scored against a constant, so a sub-1.0 ratio measures nothing and would
    # read as a passed ADR-006 demotion bar. Publishing null with a stated reason beats publishing
    # a number that looks like an answer.
    skill = None
    skill_blocked = None
    if len(pairs) >= 20 and not moving:
        skill_blocked = ("observed reference does not move (0 chains with a varying observed "
                         "isotherm) — a skill ratio against a constant is not skill")
    if len(pairs) >= 20 and moving:
        by = defaultdict(lambda: ([], []))
        for L, fe, pe in pairs:
            by[L][0].append(fe); by[L][1].append(pe)
        skill = {
            "n_pairs": len(pairs),
            "per_lead": {str(L): {"n": len(f), "fcst_mae_m": round(_mae(f), 3),
                                   "persist_mae_m": round(_mae(pv), 3),
                                   "skill_ratio": round(_mae(f) / _mae(pv), 3) if _mae(pv) > 0 else None,
                                   "beats_persistence": _mae(f) < _mae(pv)}
                         for L, (f, pv) in sorted(by.items())},
        }

    result = {
        "source": str(LOG.relative_to(ROOT)),
        "n": len(all_err),
        "chains": chains,
        "chains_with_moving_obs": moving,
        "n_effective_chains": len(moving),
        "pooled_mae_m": pooled,
        "band_blocked_reason": band_blocked,
        "pooled_p90_m": round(_p90(all_err), 3) if pooled is not None else None,
        "per_lead": per_lead,
        "skill_vs_persistence": skill,
        "skill_blocked_reason": skill_blocked,
        "quarantined_chains": quarantined,
        "lead_slope_m_per_h": round(slope, 5),
        "lead_trend_detected": has_trend,
        "note": (
            f"Isotherm-depth (thermal-field position) error, bias-corrected, from the "
            f"accumulated forecast gate. "
            + (f"NO NUMERIC BAND: {band_blocked}. " if band_blocked else "")
            + (f"Pooled MAE {pooled} m over leads {leads[0]}-{leads[-1]} h. " if pooled is not None else "")
            + f"Lead trend {'DETECTED' if has_trend else 'NOT detected'} "
            f"(OLS slope {slope:+.4f} m/h; per-lead means span {mean_spread:.2f} m). "
            f"CAVEAT: n={len(all_err)} usable rows"
            + (f" (chains {quarantined} quarantined under ADR-048 — a constant observed isotherm "
               f"depth is the top-sensor bound, not a measurement)" if quarantined else "")
            + f", {len(moving)} of {len(chains)} chains have a moving observed reference — treat "
            f"as a provisional ABSOLUTE-error figure, not skill. "
            f"Because no lead trend is measurable, the map must NOT fabricate a growing-with-lead "
            f"thermal-zone decay; timing uncertainty lives in the phase banner instead."
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    print(f"\nwrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
