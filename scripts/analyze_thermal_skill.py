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
    from tbay_fishcast.features import site_validity, thermal_skill as ts

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
        recs.append({"day": r["issue_date"], "valid": r["valid_utc"][:10],
                     "sat_c": _f(r.get("sat_c")), "obs_c": o, "fcst_c": f,
                     "lead_h": int(r["lead_h"]),
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
    all_recs = list(recs)          # pre-MNAR, for the site check (see below)
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

    # ==== THE GUARDS, IN THE PATH THIS TIME (ADR-054) ====
    # ADR-052 and ADR-053 were both written after being wrong, and both were then wired only into
    # the analyzer that did not need them. A guard outside the path it protects is documentation.
    # These two now run here, on the gate that produced the wrong verdict in the first place, and
    # they GATE it rather than annotating it.
    #
    # (1) SITE: is the model grossly out of line with an independent third party HERE? At LLO1 it
    #     is — the satellite tracks the buoy, not the model — so this site measures that node and
    #     cannot ground a claim about the product.
    # (2) REFERENCE: is the truth actually as variable as the water? Here it IS, because the
    #     reference is a real thermistor rather than a smoothed analysis. Running it proves the
    #     check discriminates instead of only ever refusing.
    # Use the shallowest observation the LOG has, not the shallowest that survived the MNAR
    # filter: that filter exists to keep the skill statistics honest and has nothing to say about
    # whether a sensor can be compared with a satellite.
    shallow = min((r["depth_m"] for r in all_recs if r["depth_m"] is not None), default=None)
    surf = [r for r in all_recs if r["lead_h"] == 0 and r["depth_m"] == shallow]
    site = site_validity.check([(r["fcst_c"], r["obs_c"], r["sat_c"]) for r in surf],
                               obs_depth_m=shallow)
    print(f"\n  SITE CHECK ({len(surf)} days at {shallow} m): {site.reason}")

    obs_daily = {r["valid"]: r["obs_c"] for r in surf if r["obs_c"] is not None}
    sat_daily = {r["valid"]: r["sat_c"] for r in surf if r["sat_c"] is not None}
    ref = site_validity.reference_variability(sat_daily, obs_daily)
    print(f"  REFERENCE CHECK: {ref['reason']}")

    # Two different lists, and conflating them erased the finding. `failed_here` is what was
    # MEASURED at this node and must survive; `demote` is what the product should ACT on, which a
    # site that cannot represent the product may not populate. The first version zeroed the
    # measurement before computing the verdict, so a node where every lead failed reported "no
    # lead is shown to add nothing" — over-correction is its own kind of dishonesty.
    failed_here = [L for L, v in per_lead.items() if v["demote"] and int(L) > 0]
    demote = failed_here if site.usable else []
    now = per_lead.get("0")
    far = per_lead.get(str(max(fcst_leads))) if fcst_leads else None
    state_not_forecast = bool(
        now and far and now["fcst_mae_c"] > 0.75 * far["fcst_mae_c"])
    all_lose = len(failed_here) == len(fcst_leads) and failed_here
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
        "failed_at_this_node": failed_here,
        "site_check": site.as_dict(),
        "reference_check": ref,
        "nowcast_mae_c": (now or {}).get("fcst_mae_c"),
        "error_is_model_state_not_forecast_decay": state_not_forecast,
        # SITE-SCOPED, and this correction matters more than the number it qualifies. The first
        # version of this line read "EVERY forecast lead fails the ADR-006 bar" full stop, which
        # generalised a measurement at ONE mooring into a verdict on the product. It does not
        # survive a look at the rest of the lake: on 2025-08-12 the same model hour puts the deep
        # offshore stations at a textbook 17.6-21.7 C surface over a 3.97-3.99 C hypolimnion and
        # Thunder Bay at 19.5 C, while LLO1 alone sits at 8.6 C. Independent satellite SST
        # (GLSEA/ACSPO, 0.43 km away) tracks the BUOY, not the model — 21.3 vs 9.5 C on 2025-07-28.
        # So what is measured here is a LOCAL LSOFS pathology at 45027, on the Minnesota upwelling
        # coast. It is real, and it is not evidence about Thunder Bay.
        "verdict": (
            (f"at {'/'.join(sorted({r['chain'] for r in recs}))}: every forecast lead fails the "
             f"ADR-006 bar AT THIS NODE — but the site check refuses to generalise it "
             f"({site.reason})") if (all_lose and not site.usable) else
            (f"at {'/'.join(sorted({r['chain'] for r in recs}))}: every forecast lead fails the "
             f"ADR-006 bar") if all_lose else
            f"leads {failed_here} fail the ADR-006 bar at this site" if failed_here else
            "no lead is shown to add nothing"),
        "scope": ("SITE-SPECIFIC. This is the model's skill at the validation mooring only. "
                  "Generalising it to the Thunder Bay nearshore is not supported and is "
                  "contradicted by the model's own behaviour elsewhere in the lake."),
        "method": ("Scored in C at the observed sensor depths (never censored, stable units). "
                   "Paired samples; the baseline is the harder of observed persistence and "
                   "other-year climatology per sample; the CI is a block bootstrap whose block "
                   "length is the measured error decorrelation. A lead is benched only when the "
                   "interval EXCLUDES 1.0."),
        "caveat": (
            "Scored at the Duluth LLO1 mooring (LSOFS station 45027, 0.11 km away), 271 km from "
            "Thunder Bay on the Minnesota upwelling coast. THE MODEL IS ANOMALOUS AT THIS NODE: "
            "on 2025-08-12 12Z it puts the deep offshore stations at 17.6-21.7 C over a "
            "3.97-3.99 C hypolimnion and Thunder Bay at 19.5 C, while 45027 alone reads 8.60 C "
            "surface / 7.00 C bottom. Independent satellite SST (GLSEA/ACSPO, 0.43 km from the "
            "buoy) tracks the buoy rather than the model (21.28 vs 9.54 C on 2025-07-28), so the "
            "observation and the pipeline are sound and the model is locally wrong. The ADR-006 "
            "numbers above therefore describe LSOFS AT THIS MOORING and must not be read as a "
            "verdict on the Thunder Bay thermal layer, which this gate cannot reach."),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    print(f"\n{result['verdict']}")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
