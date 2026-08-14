"""Does LSOFS get Thunder Bay's surface temperature right — and does it hold up during upwelling?
(ADR-051)

Reads data/surface_gate_log.csv and writes data/calib/surface_skill.json.

Two questions, and the second is the sharper one:

  1. SKILL. Per lead, MAE(model) / MAE(best cheap baseline), with the same discipline as the
     subsurface gate: paired samples, the HARDER of satellite persistence and satellite
     climatology per sample, a block bootstrap whose block length is measured from the error's own
     autocorrelation, and a day-level sign test that assumes nothing about correlation at all.

  2. REGIME. The map's headline mechanism is west wind -> upwelling -> cold water shoals -> fish
     become reachable. So "is the model good on average" is the wrong question. Errors are
     stratified by the UPWELLING PHASE the product itself computes, because a model that is fine
     in neutral conditions and fails during setup, peak and relaxation fails precisely where the
     product stakes its claim — and an average over mostly-neutral days would hide that
     completely.

    python scripts/analyze_surface_skill.py
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LOG = ROOT / "data" / "surface_gate_log.csv"
OUT = ROOT / "data" / "calib" / "surface_skill.json"


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def main(argv) -> int:
    from tbay_fishcast.features import site_validity, thermal_skill as ts

    ap = argparse.ArgumentParser()
    ap.add_argument("--min-days", type=int, default=10)
    a = ap.parse_args(argv)
    if not LOG.exists():
        print(f"no log at {LOG}")
        return 1

    recs = []
    for r in csv.DictReader(LOG.open()):
        sat, mod = _f(r["sat_c"]), _f(r["model_c"])
        if sat is None or mod is None:
            continue
        p, c = _f(r["persist_sat_c"]), _f(r["clim_sat_c"])
        base, which = ts.best_baseline(None if p is None else p - sat,
                                       None if c is None else c - sat)
        recs.append({"day": r["issue_date"], "valid": r["valid_date"],
                     "lead_h": int(r["lead_h"]), "err_c": mod - sat,
                     "persist_err": None if p is None else p - sat,
                     "clim_err": None if c is None else c - sat,
                     "base_err": base, "base_which": which,
                     "phase": (r.get("phase") or "unknown"),
                     "sat_c": sat, "model_c": mod})
    if not recs:
        print("no usable rows")
        return 1

    days = sorted({r["day"] for r in recs})
    leads = sorted({r["lead_h"] for r in recs})
    print(f"{len(recs)} paired samples · {len(days)} issue days ({days[0]} .. {days[-1]})")

    # SITE VALIDITY FIRST (ADR-052). Here the model-vs-satellite comparison IS the measurement, so
    # this reports rather than gates — but it is run and published in the same shape as the
    # subsurface gate so the two are read side by side.
    nowcast = [(r["model_c"], None, r["sat_c"]) for r in recs if r["lead_h"] == 0]
    site = site_validity.check(nowcast)
    print(f"  site check: {site.reason}")

    err_by_day = defaultdict(list)
    for r in recs:
        err_by_day[r["day"]].append(r["err_c"])
    block = ts.decorrelation_days(err_by_day)
    print(f"  measured error decorrelation: {block} day(s)")

    per_lead = {}
    for L in leads:
        sub = [r for r in recs if r["lead_h"] == L]
        pairs = [(r["day"], r["err_c"], r["base_err"]) for r in sub if r["base_err"] is not None]
        boot = ts.block_bootstrap_ratio(pairs, block_days=block)
        sign = ts.sign_test_days(pairs)
        verdict = boot["verdict"]
        if boot["n_days"] < a.min_days:
            verdict = f"insufficient sample ({boot['n_days']} independent days)"
        per_lead[str(L)] = {
            "n": len(sub), "n_days": boot["n_days"],
            "model_mae_c": round(ts.mae([r["err_c"] for r in sub]), 4),
            "model_bias_c": round(ts.bias([r["err_c"] for r in sub]), 4),
            "persist_mae_c": round(ts.mae([r["persist_err"] for r in sub]), 4),
            "clim_mae_c": round(ts.mae([r["clim_err"] for r in sub]), 4),
            "skill_ratio": boot["ratio"], "ci95": [boot["lo"], boot["hi"]],
            "n_blocks": boot.get("n_blocks"),
            "interval_quality": boot.get("interval_quality"),
            "days_model_better": sign["wins"], "days_model_worse": sign["losses"],
            "sign_test_p": sign["p_two_sided"], "verdict": verdict,
            "demote": bool(boot["lo"] is not None and boot["lo"] > 1.0
                           and boot["n_days"] >= a.min_days)}
        print(f"  lead {L:3d} h  n={len(sub):4d} ({boot['n_days']:3d} d)  "
              f"model {per_lead[str(L)]['model_mae_c']:.3f} C "
              f"(bias {per_lead[str(L)]['model_bias_c']:+.2f})  "
              f"persist {per_lead[str(L)]['persist_mae_c']:.3f}  "
              f"clim {per_lead[str(L)]['clim_mae_c']:.3f}  "
              f"ratio {boot['ratio']} CI [{boot['lo']}, {boot['hi']}] -> {verdict}")

    # THE REGIME CUT — the question the average cannot answer.
    print("\n  === ERROR BY UPWELLING PHASE (the map's headline mechanism) ===")
    by_phase = defaultdict(list)
    for r in recs:
        if r["lead_h"] > 0:
            by_phase[r["phase"]].append(r)
    phases = {}
    for ph, sub in sorted(by_phase.items(), key=lambda kv: -len(kv[1])):
        pairs = [(r["day"], r["err_c"], r["base_err"]) for r in sub if r["base_err"] is not None]
        boot = ts.block_bootstrap_ratio(pairs, block_days=block)
        phases[ph] = {
            "n": len(sub), "n_days": boot["n_days"],
            "model_mae_c": round(ts.mae([r["err_c"] for r in sub]), 4),
            "model_bias_c": round(ts.bias([r["err_c"] for r in sub]), 4),
            "baseline_mae_c": round(ts.mae([r["base_err"] for r in sub]), 4),
            "skill_ratio": boot["ratio"], "ci95": [boot["lo"], boot["hi"]],
            "verdict": boot["verdict"]}
        print(f"    {ph:12s} n={len(sub):4d} ({boot['n_days']:3d} d)  "
              f"model {phases[ph]['model_mae_c']:.3f} C (bias {phases[ph]['model_bias_c']:+.2f})  "
              f"baseline {phases[ph]['baseline_mae_c']:.3f}  "
              f"ratio {boot['ratio']} CI [{boot['lo']}, {boot['hi']}]")

    # Does the error DEPEND on the regime? Compare the upwelling-active phases against neutral.
    active = [r["err_c"] for r in recs
              if r["lead_h"] > 0 and r["phase"] in ("setup", "peak", "relaxation")]
    neutral = [r["err_c"] for r in recs if r["lead_h"] > 0 and r["phase"] == "neutral"]
    regime = None
    if len(active) >= 30 and len(neutral) >= 30:
        ma, mn = ts.mae(active), ts.mae(neutral)
        regime = {"upwelling_mae_c": round(ma, 4), "neutral_mae_c": round(mn, 4),
                  "ratio": round(ma / mn, 3) if mn > 0 else None,
                  "n_upwelling": len(active), "n_neutral": len(neutral)}
        print(f"\n    upwelling-active MAE {ma:.3f} C vs neutral {mn:.3f} C "
              f"-> {ma / mn:.2f}x" if mn > 0 else "")

    # ANOMALY SKILL + CALIBRATION — the fairest cut, and the one that changed the diagnosis.
    #
    # Two confounds make the raw comparison unfair to the model. (a) Both baselines are built FROM
    # GLSEA, so they inherit its smoothness and carry no cross-dataset representativeness error,
    # while the model has to bridge model-space to satellite-space. (b) A constant offset in the
    # satellite at this pixel inflates the MODEL's error and leaves the baselines' untouched.
    # Stripping the seasonal cycle from both sides removes both, and asks the only question that
    # actually matters for a forecast: when the lake is warmer or colder than normal, does the
    # model know?
    #
    # It does. The failure is CALIBRATION, not information — so the fitted slope is reported, and
    # it is fitted on the first half of the season and scored on the held-out remainder (rule 6).
    def _corr(x, y):
        if len(x) < 3:
            return None
        mx, my = sum(x) / len(x), sum(y) / len(y)
        den = (sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y)) ** 0.5
        return (sum((a - mx) * (b - my) for a, b in zip(x, y)) / den) if den else None

    def _sd(v):
        if len(v) < 2:
            return None
        m = sum(v) / len(v)
        return (sum((a - m) ** 2 for a in v) / len(v)) ** 0.5

    cut = days[len(days) // 2]
    anom = {}
    for L in leads:
        sub = [r for r in recs if r["lead_h"] == L and r["clim_err"] is not None]
        if len(sub) < 30:
            continue
        mx = [r["model_c"] - (r["sat_c"] + r["clim_err"]) for r in sub]   # model - climatology
        ox = [-r["clim_err"] for r in sub]                                # obs   - climatology
        tr = [(m, o, r) for m, o, r in zip(mx, ox, sub) if r["day"] <= cut]
        te = [(m, o, r) for m, o, r in zip(mx, ox, sub) if r["day"] > cut]
        entry = {"n": len(sub), "anomaly_corr": None, "model_anom_sd": _sd(mx),
                 "obs_anom_sd": _sd(ox), "calibration_slope": None,
                 "raw_mae_holdout_c": None, "calibrated_mae_holdout_c": None,
                 "persist_mae_holdout_c": None, "clim_mae_holdout_c": None}
        c = _corr(mx, ox)
        entry["anomaly_corr"] = round(c, 4) if c is not None else None
        for k in ("model_anom_sd", "obs_anom_sd"):
            entry[k] = round(entry[k], 4) if entry[k] is not None else None
        if len(tr) >= 20 and len(te) >= 20:
            tmx = [a for a, _b, _r in tr]
            tox = [b for _a, b, _r in tr]
            m_ = sum(tmx) / len(tmx)
            denom = sum((a - m_) ** 2 for a in tmx)
            if denom > 0:
                b_ = sum((a - m_) * (o - sum(tox) / len(tox)) for a, o in zip(tmx, tox)) / denom
                a_ = sum(tox) / len(tox) - b_ * m_
                entry["calibration_slope"] = round(b_, 4)
                entry["calibration_intercept"] = round(a_, 4)
                raw = [r["err_c"] for _m, _o, r in te]
                cal = [a_ + b_ * m - o for m, o, _r in te]
                pe = [r["persist_err"] for _m, _o, r in te if r["persist_err"] is not None]
                ce = [r["clim_err"] for _m, _o, r in te]
                entry["raw_mae_holdout_c"] = round(ts.mae(raw), 4)
                entry["calibrated_mae_holdout_c"] = round(ts.mae(cal), 4)
                entry["persist_mae_holdout_c"] = round(ts.mae(pe), 4)
                entry["clim_mae_holdout_c"] = round(ts.mae(ce), 4)
        anom[str(L)] = entry
    print("\n  === ANOMALY SKILL (seasonal cycle removed from BOTH sides) ===")
    for L, e in anom.items():
        print(f"    lead {L:>3s} h  corr {e['anomaly_corr']}  "
              f"model anom sd {e['model_anom_sd']} vs obs {e['obs_anom_sd']}  "
              f"slope {e['calibration_slope']}  "
              f"holdout raw {e['raw_mae_holdout_c']} -> calibrated "
              f"{e['calibrated_mae_holdout_c']} (persist {e['persist_mae_holdout_c']}, "
              f"clim {e['clim_mae_holdout_c']})")

    # ADR-053: the REFERENCE is disqualified for this job, so no skill verdict is issued at all.
    # Measured against a real thermistor at the same place, GLSEA's day-to-day change sd is
    # 0.383 C where the water's is 1.930 C — five times smoother. Persisting it scored 0.295 C,
    # which is simply GLSEA's own mean day-to-day change (0.294 C). That is not a forecast bar.
    #
    # The two sd's below are a RECORDED MEASUREMENT, not a constant to be edited: Thunder Bay has
    # no in-situ thermistor, so the disqualification is inherited from the nearest place the
    # reference product COULD be measured against real water. What is not hardcoded is the
    # verdict — the ratio is recomputed here and judged against the live bar in site_validity, so
    # loosening that bar can never leave this file asserting a disqualification the guard no
    # longer agrees with (or, worse, the reverse).
    REF_SD_C, INSITU_SD_C = 0.383, 1.930
    _ratio = REF_SD_C / INSITU_SD_C
    REFERENCE_DISQUALIFIED = {
        "reference": "GLSEA/ACSPO satellite SST",
        "reference_daily_change_sd_c": REF_SD_C,
        "insitu_daily_change_sd_c": INSITU_SD_C,
        "variability_ratio": round(_ratio, 3),
        "variability_ratio_bar": site_validity.VARIABILITY_RATIO_MIN,
        "disqualified": _ratio < site_validity.VARIABILITY_RATIO_MIN,
        "measured_at": "LLO1 thermistor vs GLSEA at the same pixel, 99 paired days, 2025",
        "inherited_because": ("Thunder Bay has no in-situ subsurface sensor, so the reference "
                              "product is judged where it can be judged and the verdict carried "
                              "here — it is a property of the ANALYSIS, not of the site"),
        "usable_for": ["mean bias", "seasonal cycle"],
        "NOT_usable_for": ["forecast skill baselines", "variance/dispersion comparisons"],
        "reason": ("a persistence baseline built on a temporally relaxed analysis scores the "
                   "analysis's own smoothness rather than forecast difficulty, and its damped "
                   "variance makes a physical model look over-dispersed"),
    }
    if not REFERENCE_DISQUALIFIED["disqualified"]:
        # Fail loudly rather than silently start issuing skill verdicts off a bar someone
        # widened. Rule 5: a check that quietly turns itself off is worse than no check.
        raise SystemExit(
            f"reference variability ratio {_ratio:.3f} now passes the "
            f"{site_validity.VARIABILITY_RATIO_MIN} bar — ADR-053's withheld skill verdict was "
            f"written against a failing ratio. Re-measure the reference and revise ADR-053 "
            f"deliberately; do not let this script start issuing skill verdicts by default.")
    demote = []          # withheld: see REFERENCE_DISQUALIFIED
    result = {
        "source": str(LOG.relative_to(ROOT)),
        "built_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "site": "Thunder Bay (LSOFS station 10050, 2.3 km off the waterfront)",
        "truth": "GLSEA/ACSPO satellite SST",
        "n": len(recs), "n_days": len(days), "window": [days[0], days[-1]],
        "block_days_measured": block,
        "site_check": site.as_dict(),
        "per_lead": per_lead,
        "by_upwelling_phase": phases,
        # HONEST NULL: the regime question is UNANSWERED, not answered. Only a handful of samples
        # land in setup/peak/relaxation because the phase classifier almost never fires on
        # reanalysis 10 m wind at a coastal grid point. Reading a verdict off n=3 would be exactly
        # the noise-chasing the demotion rule exists to prevent.
        "phase_coverage_note": (
            "upwelling phases are too rare in this sample to support a verdict; the stratification "
            "is reported for transparency, not conclusion. Needs over-lake wind or a lower "
            "sustained-blow threshold before the regime question can be answered."),
        "anomaly_skill": anom,
        "regime_dependence": regime,
        "demote_leads": demote,
        "reference_disqualified": REFERENCE_DISQUALIFIED,
        "verdict": ("NO SKILL VERDICT. The reference cannot support one — see "
                    "reference_disqualified. The per-lead numbers below are retained for the "
                    "MEAN BIAS they do support, and must not be read as forecast skill."),
        "scope": ("SURFACE ONLY. GLSEA cannot see the isotherm DEPTH, which is the product's "
                  "actual claim, so a good result here is necessary and not sufficient. The "
                  "subsurface profile at Thunder Bay remains unvalidated and needs a local "
                  "measurement (Bare Point intake, task #8)."),
        "method": ("Raw LSOFS surface vs satellite — NOT the bias-corrected product, which is "
                   "anchored to GLSEA and would be scored against its own input. Baselines are "
                   "satellite persistence (lagged one day, which is what an operational forecast "
                   "could actually have had) and other-year satellite climatology, harder of the "
                   "two per sample."),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    print(f"\n{result['verdict']}")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
