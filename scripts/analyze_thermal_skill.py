"""Does the thermal forecast beat the cheap alternative? (ADR-049 / the ADR-006 demotion bar)

Reads data/thermal_gate_log.csv and writes data/calib/thermal_skill.json:

  * per-lead skill ratio MAE(forecast)/MAE(best cheap baseline), with a BLOCK BOOTSTRAP interval
    whose block length is measured from the error series' own autocorrelation;
  * sigma_T(lead, depth band) — the temperature error the map converts into a depth band at the
    point of use, using each day's own stratification (features/thermal_skill.isotherm_depth_sigma);
  * a demotion verdict per lead, which is only ever "bench" when the interval EXCLUDES 1.0.

The last point is the one that keeps the demotion rule honest. A point ratio of 0.9 on seven
independent days is indistinguishable from 1.1; acting on it would be tuning on noise, which is
the failure the demotion rule exists to prevent rather than to cause.

    python scripts/analyze_thermal_skill.py [--min-days 10]
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

LOG = ROOT / "data" / "thermal_gate_log.csv"
OUT = ROOT / "data" / "calib" / "thermal_skill.json"


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def main(argv) -> int:
    from tbay_fishcast.features import thermal_skill as ts

    ap = argparse.ArgumentParser()
    ap.add_argument("--min-days", type=int, default=10,
                    help="independent days below which no verdict is issued at all")
    a = ap.parse_args(argv)

    if not LOG.exists():
        print(f"no log at {LOG}")
        return 1
    rows = list(csv.DictReader(LOG.open()))
    recs = []
    for r in rows:
        o, f, p, c = (_f(r["obs_c"]), _f(r["fcst_c"]),
                      _f(r["persist_obs_c"]), _f(r["clim_c"]))
        if o is None or f is None:
            continue
        base_err, which = ts.best_baseline(None if p is None else p - o,
                                           None if c is None else c - o)
        recs.append({"day": r["issue_date"], "lead_h": int(r["lead_h"]),
                     "depth_m": _f(r["depth_m"]), "err_c": f - o,
                     "persist_err": None if p is None else p - o,
                     "clim_err": None if c is None else c - o,
                     "base_err": base_err, "base_which": which,
                     "chain": r["chain"]})
    if not recs:
        print("no usable rows")
        return 1

    # MISSING-NOT-AT-RANDOM GUARD. A sensor is only scored where it falls inside the model's
    # sigma-layer range, and the top layer's depth moves with the surface elevation zeta. So the
    # shallowest sensor (1.0 m against a ~1.33 m top layer) is admitted ONLY on days when zeta
    # happened to be low — about 1% of them — and those days are not a random sample of the
    # season. Left in, that cell reported a +5 to +6 C bias on n=11-15 and looked like a finding.
    # Any (lead, depth) cell present on fewer than half the issue days is dropped, and the drop
    # is reported rather than done quietly.
    all_days = {r["day"] for r in recs}
    cell_days = defaultdict(set)
    for r in recs:
        cell_days[(r["lead_h"], r["depth_m"])].add(r["day"])
    thin = {c for c, ds in cell_days.items() if len(ds) < 0.5 * len(all_days)}
    if thin:
        dropped = [r for r in recs if (r["lead_h"], r["depth_m"]) in thin]
        depths = sorted({c[1] for c in thin})
        print(f"  MNAR guard: dropped {len(dropped)} rows at depths {depths} — present on "
              f"<50% of issue days, so their presence is conditioned on the model's own surface "
              f"elevation rather than on anything about the forecast")
        recs = [r for r in recs if (r["lead_h"], r["depth_m"]) not in thin]

    days = sorted({r["day"] for r in recs})
    leads = sorted({r["lead_h"] for r in recs})
    # Lead 0 is the NOWCAST — the model's own analysis at issue time. It is reported but never
    # counted as a forecast: it is the diagnostic that separates "cannot forecast" from "the
    # state is wrong here", and scoring an analysis against a persistence baseline built from the
    # same instant would be meaningless.
    fcst_leads = [L for L in leads if L > 0]
    print(f"{len(recs)} paired samples · {len(days)} issue days "
          f"({days[0]} .. {days[-1]}) · leads {leads}")
    which = defaultdict(int)
    for r in recs:
        which[r["base_which"]] += 1
    print(f"  baseline chosen per sample (the HARDER of the two): {dict(which)}")

    # Block length is MEASURED, not chosen: the first lag at which the daily mean error's
    # autocorrelation falls below 1/e. On this lake a few days is the physical expectation
    # (seiche ~40 h, synoptic 2-5 d), so a wildly different answer is itself informative.
    err_by_day = defaultdict(list)
    for r in recs:
        err_by_day[r["day"]].append(r["err_c"])
    block = ts.decorrelation_days(err_by_day)
    print(f"  measured error decorrelation: {block} day(s) -> bootstrap block length")

    per_lead = {}
    for L in leads:
        sub = [r for r in recs if r["lead_h"] == L]
        pairs = [(r["day"], r["err_c"], r["base_err"]) for r in sub
                 if r["base_err"] is not None]
        boot = ts.block_bootstrap_ratio(pairs, block_days=block)
        sign = ts.sign_test_days(pairs)
        n_days = boot["n_days"]
        verdict = boot["verdict"]
        if n_days < a.min_days:
            verdict = f"insufficient sample ({n_days} independent days)"
        per_lead[str(L)] = {
            "n": len(sub), "n_days": n_days,
            "fcst_mae_c": round(ts.mae([r["err_c"] for r in sub]), 4),
            "fcst_bias_c": round(ts.bias([r["err_c"] for r in sub]), 4),
            "fcst_rmse_c": round(ts.rmse([r["err_c"] for r in sub]), 4),
            "persist_mae_c": round(ts.mae([r["persist_err"] for r in sub]), 4),
            "clim_mae_c": round(ts.mae([r["clim_err"] for r in sub]), 4),
            "baseline_mae_c": round(ts.mae([r["base_err"] for r in sub]), 4),
            "skill_ratio": boot["ratio"], "ci95": [boot["lo"], boot["hi"]],
            "n_blocks": boot.get("n_blocks"),
            "interval_quality": boot.get("interval_quality"),
            # The day-level sign test assumes nothing about magnitude or how long the error stays
            # correlated. Where it and the bootstrap disagree, this is the one to believe.
            "days_forecast_better": sign["wins"], "days_forecast_worse": sign["losses"],
            "sign_test_p": sign["p_two_sided"],
            "verdict": verdict,
            # ADR-006: bench a lead only when the INTERVAL says it adds nothing, never on a
            # point estimate. "Inconclusive" is a real answer and the commonest honest one.
            "demote": bool(boot["lo"] is not None and boot["lo"] > 1.0
                           and n_days >= a.min_days),
        }
        print(f"  lead {L:3d} h  n={len(sub):5d} ({n_days:3d} d)  "
              f"fcst {per_lead[str(L)]['fcst_mae_c']:.3f} C  "
              f"persist {per_lead[str(L)]['persist_mae_c']:.3f}  "
              f"clim {per_lead[str(L)]['clim_mae_c']:.3f}  "
              f"ratio {boot['ratio']} CI [{boot['lo']}, {boot['hi']}] "
              f"({boot.get('n_blocks')} blocks, {boot.get('interval_quality')})  -> {verdict}\n"
              f"           day-level: forecast better on {sign['wins']}/"
              f"{sign['wins'] + sign['losses']} days"
              + (f", sign-test p={sign['p_two_sided']:.3g}"
                 if sign['p_two_sided'] is not None else ""))

    sigma = ts.sigma_by_lead_depth(recs)
    print("\n  sigma_T by lead and depth band (RMSE C) — the input to the map's depth band:")
    for k, v in sigma.items():
        print(f"    lead {v['lead_h']:3d} h  {v['depth_lo']:>3.0f}-{v['depth_hi']:<4.0f} m  "
              f"n={v['n']:5d}  rmse={v['rmse_c']:.3f}  bias={v['bias_c']:+.3f}")

    demote = [L for L, v in per_lead.items() if v["demote"] and int(L) > 0]
    now = per_lead.get("0")
    far = per_lead.get(str(max(fcst_leads))) if fcst_leads else None
    state_not_forecast = bool(
        now and far and now["fcst_mae_c"] > 0.75 * far["fcst_mae_c"])
    all_lose = len(demote) == len(fcst_leads) and demote
    result = {
        "source": str(LOG.relative_to(ROOT)),
        "built_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "n": len(recs), "n_days": len(days),
        "window": [days[0], days[-1]],
        "chains": sorted({r["chain"] for r in recs}),
        "block_days_measured": block,
        "baseline": "per-sample harder of OBSERVED persistence and other-year climatology",
        "per_lead": per_lead,
        "sigma_t": sigma,
        "demote_leads": demote,
        "nowcast_mae_c": (now or {}).get("fcst_mae_c"),
        "error_is_model_state_not_forecast_decay": state_not_forecast,
        "verdict": ("EVERY forecast lead fails the ADR-006 bar" if all_lose else
                    f"leads {demote} fail the ADR-006 bar" if demote else
                    "no lead is shown to add nothing"),
        "method": ("Scored in C at the observed sensor depths (never censored, stable units). "
                   "Paired samples; the baseline is the harder of observed persistence and "
                   "other-year climatology per sample; the CI is a block bootstrap whose block "
                   "length is the measured error decorrelation. A lead is benched only when the "
                   "interval EXCLUDES 1.0."),
        "caveat": ("Scored at the Duluth LLO1 mooring (LSOFS station 45027, 0.11 km away), which "
                   "is 271 km from Thunder Bay on an upwelling-dominated coast. This measures the "
                   "model's OFFSHORE column there; transfer to the Thunder Bay nearshore is an "
                   "assumption, not a measurement, and no subsurface profile exists nearer."),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    print(f"\n{result['verdict']}")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
