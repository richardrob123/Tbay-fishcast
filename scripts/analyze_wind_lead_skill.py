"""Where does the wind forecast stop knowing about upwelling? (ADR-055)

Reads data/wind_lead_gate_log.csv and writes data/calib/wind_lead_skill.json.

Three questions, and the third is the one the map's long leads live or die by:

  1. DRIVE SKILL. Per lead, MAE(forecast drive) / MAE(harder of persistence and climatology),
     with the discipline used everywhere else in this project: paired samples, harder-of-two per
     sample, a block bootstrap whose block length is measured from the error's own daily
     autocorrelation, and a day-level sign test that assumes nothing about correlation.

  2. THE EVENT. Upwelling is not a continuous score to a fisher — it either sets up or it does
     not. Scored as a contingency table per lead with the PEIRCE SKILL SCORE (POD - POFD), which
     is exactly the right bar for a rare event: a forecast that always says "no blow" scores 0 no
     matter how rare the blow is, so it cannot be flattered by the base rate the way accuracy or
     a raw hit rate can.

  3. THE SIGN. Before any of that: does the forecast get the DIRECTION of the drive right? If it
     cannot say whether the wind is upwelling- or downwelling-favorable, nothing downstream of it
     means anything, and the finer metrics are decoration.

Also audits ADR-032 in passing. That decision picked GFS over ICON by scoring both against ERA5
reanalysis. ADR-053 is the standing lesson that a reference is not automatically truth, so the
same reference guard is run here on ERA5 against the in-bay anemometer, and reported whatever it
says.

    python scripts/analyze_wind_lead_skill.py
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LOG = ROOT / "data" / "wind_lead_gate_log.csv"
OUT = ROOT / "data" / "calib" / "wind_lead_skill.json"
BOOT_N = 2000
# THE SAMPLE THAT LOOKS BIG AND IS NOT. 20,195 paired hours sounds decisive, and for the
# continuous drive it is. For the EVENT it is not: over 121 days the in-bay record contains 44
# observed event hours, and those 44 hours are THREE synoptic episodes (24 h in April, 19 h in
# May, 1 h in June). A day-block bootstrap cannot rescue that — most resamples contain a copy of
# the April blow, so the interval tightens around "how did it do on one storm" and reports that
# tightness as confidence. The first run of this analyzer duly published PSS 0.87 with a CI of
# [0.75, 0.98] at +24 h, off one storm. Distinct episodes, not event hours, are the sample size.
MIN_EVENT_EPISODES = 5
EPISODE_GAP_H = 6            # event hours closer than this belong to the same blow


def _episodes(times, gap_h: int = EPISODE_GAP_H) -> list:
    """Contiguous runs of observed event hours, merged across gaps shorter than `gap_h`."""
    from datetime import timedelta
    out = []
    for t in sorted(times):
        if out and (t - out[-1][-1]) <= timedelta(hours=gap_h):
            out[-1].append(t)
        else:
            out.append([t])
    return out


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _pss(rows):
    """Peirce skill score and its contingency table. rows = [(obs_event, fcst_event)]."""
    h = sum(1 for o, f in rows if o and f)
    m = sum(1 for o, f in rows if o and not f)
    fa = sum(1 for o, f in rows if not o and f)
    cn = sum(1 for o, f in rows if not o and not f)
    pod = h / (h + m) if (h + m) else None
    pofd = fa / (fa + cn) if (fa + cn) else None
    far = fa / (h + fa) if (h + fa) else None
    # CSI alongside PSS, because PSS is POD-dominated when the event is rare: with a 1.5% base
    # rate the correct-negative pile makes POFD tiny, so PSS ~ POD and a forecast that catches
    # most blows scores 0.87 while three of every four of its calls are false alarms. CSI ignores
    # correct negatives entirely and cannot be flattered that way.
    csi = h / (h + m + fa) if (h + m + fa) else None
    return {"hits": h, "misses": m, "false_alarms": fa, "correct_negatives": cn,
            "base_rate": round((h + m) / len(rows), 4) if rows else None,
            "pod": None if pod is None else round(pod, 4),
            "pofd": None if pofd is None else round(pofd, 4),
            "far": None if far is None else round(far, 4),
            "csi": None if csi is None else round(csi, 4),
            "pss": None if (pod is None or pofd is None) else round(pod - pofd, 4)}


def _ratio(sub, key):
    """MAE(forecast) / MAE(one named baseline), over the samples that baseline covers."""
    pairs = [(r["err"], r[key]) for r in sub if r[key] is not None]
    if not pairs:
        return None
    den = sum(abs(b) for _e, b in pairs) / len(pairs)
    return round((sum(abs(e) for e, _b in pairs) / len(pairs)) / den, 4) if den > 0 else None


def _pss_ci(by_day, *, block_days: int, seed: int = 12345):
    """Block-bootstrap CI on the Peirce skill score, resampling contiguous BLOCKS OF DAYS.

    Same reason as everywhere else: hourly rows inside a day (and days inside a synoptic system)
    are nothing like independent, so a naive interval would be far too tight. Zero must lie
    outside the interval before an event forecast is called skillful.
    """
    days = sorted(by_day)
    blocks = [days[i:i + block_days] for i in range(0, len(days), block_days)]
    if len(blocks) < 3:
        return None, None, len(blocks)
    rnd = random.Random(seed)
    vals = []
    for _ in range(BOOT_N):
        pick = [blocks[rnd.randrange(len(blocks))] for _ in range(len(blocks))]
        rows = [r for blk in pick for d in blk for r in by_day[d]]
        v = _pss(rows)["pss"]
        if v is not None:
            vals.append(v)
    if len(vals) < BOOT_N // 2:
        return None, None, len(blocks)
    vals.sort()
    return (round(vals[int(0.025 * len(vals))], 4),
            round(vals[int(0.975 * len(vals)) - 1], 4), len(blocks))


def main(argv) -> int:
    from tbay_fishcast.features import site_validity, thermal_skill as ts, upwelling_drive as ud

    ap = argparse.ArgumentParser()
    ap.add_argument("--min-days", type=int, default=10)
    a = ap.parse_args(argv)
    if not LOG.exists():
        print(f"no log at {LOG}")
        return 1

    recs = []
    for r in csv.DictReader(LOG.open()):
        od, fd = _f(r["obs_drive_kth"]), _f(r["fcst_drive_kth"])
        if od is None or fd is None:
            continue
        p, c = _f(r["persist_drive_kth"]), _f(r["clim_drive_kth"])
        base, which = ts.best_baseline(None if p is None else p - od,
                                       None if c is None else c - od)
        recs.append({"day": r["valid_utc"][:10], "t": r["valid_utc"], "lead_h": int(r["lead_h"]),
                     "err": fd - od, "persist_err": None if p is None else p - od,
                     "clim_err": None if c is None else c - od,
                     "base_err": base, "base_which": which,
                     "obs_event": r["obs_event"] == "1", "fcst_event": r["fcst_event"] == "1",
                     "persist_event": (None if p is None else ud.is_event(p)),
                     "obs_fav": _f(r["obs_fav_kn"]), "fcst_fav": _f(r["fcst_fav_kn"])})
    if not recs:
        print("no usable rows")
        return 1

    days = sorted({r["day"] for r in recs})
    leads = sorted({r["lead_h"] for r in recs})
    print(f"{len(recs)} paired hours · {len(days)} days ({days[0]} .. {days[-1]}) · "
          f"leads {leads}")
    print(f"event bar: drive >= {ud.EVENT_DRIVE_KTH:.0f} kt*h "
          f"({ud.OBSERVED_THRESHOLD_KN:.0f} kt sustained {ud.WINDOW_H:.0f} h) · "
          f"favorable axis {ud.FAVORABLE_AXIS_DEG:.0f} deg")

    n_ep = len(_episodes([datetime.fromisoformat(r["t"]) for r in recs
                          if r["lead_h"] == leads[0] and r["obs_event"]]))
    ev_ok = n_ep >= MIN_EVENT_EPISODES
    print(f"observed event episodes: {n_ep} (need {MIN_EVENT_EPISODES} to judge the event)"
          + ("" if ev_ok else " — EVENT VERDICT WITHHELD"))

    err_by_day = defaultdict(list)
    for r in recs:
        err_by_day[r["day"]].append(r["err"])
    block = ts.decorrelation_days(err_by_day)
    print(f"measured error decorrelation: {block} day(s)\n")

    per_lead = {}
    print("  === DRIVE SKILL (kt*h; ratio < 1 means the forecast beats the cheap baseline) ===")
    for L in leads:
        sub = [r for r in recs if r["lead_h"] == L]
        pairs = [(r["day"], r["err"], r["base_err"]) for r in sub if r["base_err"] is not None]
        boot = ts.block_bootstrap_ratio(pairs, block_days=block)
        sign = ts.sign_test_days(pairs)
        verdict = boot["verdict"]
        if boot["n_days"] < a.min_days:
            verdict = f"insufficient sample ({boot['n_days']} independent days)"

        # SIGN AGREEMENT — the precondition for everything else.
        both = [r for r in sub if r["obs_fav"] is not None and r["fcst_fav"] is not None]
        agree = sum(1 for r in both if (r["obs_fav"] >= 0) == (r["fcst_fav"] >= 0))
        sign_rate = round(agree / len(both), 4) if both else None

        # THE EVENT.
        ev_by_day = defaultdict(list)
        for r in sub:
            ev_by_day[r["day"]].append((r["obs_event"], r["fcst_event"]))
        ev = _pss([(r["obs_event"], r["fcst_event"]) for r in sub])
        lo, hi, nb = _pss_ci(ev_by_day, block_days=block)
        pev = [(r["obs_event"], r["persist_event"]) for r in sub if r["persist_event"] is not None]
        ev_persist = _pss(pev) if pev else None

        per_lead[str(L)] = {
            "n": len(sub), "n_days": boot["n_days"],
            "drive_mae_kth": round(ts.mae([r["err"] for r in sub]), 3),
            "drive_bias_kth": round(ts.bias([r["err"] for r in sub]), 3),
            "persist_mae_kth": round(ts.mae([r["persist_err"] for r in sub]), 3),
            "clim_mae_kth": round(ts.mae([r["clim_err"] for r in sub]), 3),
            # The verdict ratio is against an ORACLE composite: `best_baseline` takes the SMALLER
            # error of persistence and climatology per sample, which no real forecaster could
            # pick in advance. It is deliberately conservative and it is the bar the other gates
            # use, so it stays — but reporting only that ratio would badly understate the
            # forecast (0.95 against the oracle, 0.58 against persistence alone). Both are shown.
            "skill_ratio": boot["ratio"], "ci95": [boot["lo"], boot["hi"]],
            "ratio_vs_persistence": _ratio(sub, "persist_err"),
            "ratio_vs_climatology": _ratio(sub, "clim_err"),
            "n_blocks": boot.get("n_blocks"), "interval_quality": boot.get("interval_quality"),
            "days_model_better": sign["wins"], "days_model_worse": sign["losses"],
            "sign_test_p": sign["p_two_sided"],
            "favorable_sign_agreement": sign_rate,
            "event": ev, "event_pss_ci95": [lo, hi], "event_pss_blocks": nb,
            "event_episodes": n_ep, "event_persistence": ev_persist,
            "verdict": verdict,
            # ADR-006: a lead is benched only when the interval says it is BEATEN, not merely
            # when the point estimate is unflattering.
            "demote": bool(boot["lo"] is not None and boot["lo"] > 1.0
                           and boot["n_days"] >= a.min_days),
            # And the event bar: skillful only if the whole interval clears zero AND there were
            # enough distinct blows to be judging a forecast rather than one storm.
            "event_skillful": (bool(lo is not None and lo > 0.0) if ev_ok else None),
            "event_verdict_withheld": (None if ev_ok else
                                       f"only {n_ep} distinct observed blow(s) in the record "
                                       f"(need {MIN_EVENT_EPISODES}); the contingency table is "
                                       f"reported, the skill claim is not"),
        }
        e = per_lead[str(L)]
        print(f"  lead {L:4d} h  n={len(sub):5d} ({boot['n_days']:3d} d)  "
              f"drive MAE {e['drive_mae_kth']:7.2f} (bias {e['drive_bias_kth']:+7.2f})  "
              f"persist {e['persist_mae_kth']:7.2f}  clim {e['clim_mae_kth']:7.2f}  "
              f"ratio {boot['ratio']} CI [{boot['lo']}, {boot['hi']}]")
        print(f"              vs persistence {e['ratio_vs_persistence']} · "
              f"vs climatology {e['ratio_vs_climatology']} · sign agreement {sign_rate}")
        print(f"              event: POD {ev['pod']} FAR {ev['far']} CSI {ev['csi']} "
              f"PSS {ev['pss']} CI [{lo}, {hi}] (base rate {ev['base_rate']})"
              + ("" if ev_ok else "  [WITHHELD]"))

    # THE HORIZON: the longest lead whose interval clears the ADR-006 bar.
    #
    # NOT a sequential "first failure ends it" scan, and the data are why. Lead +24 h does NOT
    # clear the bar while +48 and +72 do — because the ORACLE baseline is hardest at short lead
    # (persistence MAE 54 kt*h at 24 h against 71 at 48 h), not because the forecast is worse
    # there. Its raw error is the lowest of any lead. Reading that as "no horizon" would bench a
    # lead for the strength of its competition.
    clears = [L for L in leads
              if per_lead[str(L)]["ci95"][1] is not None and per_lead[str(L)]["ci95"][1] < 1.0]
    horizon = max(clears) if clears else None

    # ADR-032 AUDIT. That decision scored candidate wind models against ERA5 reanalysis. ADR-053
    # is the standing lesson that a reference is not automatically truth, so ask the same
    # question of ERA5 that disqualified GLSEA — against the in-bay anemometer, not an argument.
    era5_check = None
    try:
        from tbay_fishcast.ingest import eccc_wind, wind_archive
        st = eccc_wind.STATIONS["welcome_island"]
        d0, d1 = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
        wt, wdir, wkn = wind_archive.fetch_hourly_wind(d0, d1, lat=st.lat, lon=st.lon)
        obs = eccc_wind.fetch_hourly(d0, d1)
        e_drive = ud.drive_series(wt, wkn, wdir)
        o_drive = ud.drive_series([w.time for w in obs], [w.speed_kn for w in obs],
                                  [w.dir_deg for w in obs])

        def _daily(series):                      # one value per day: the day's mean drive
            acc = defaultdict(list)
            for t, v in series.items():
                acc[t.date().isoformat()].append(v)
            return {k: sum(v) / len(v) for k, v in acc.items()}

        era5_check = site_validity.reference_variability(_daily(e_drive), _daily(o_drive),
                                                         quantity="wind", unit="kt*h")
        print(f"\n  ADR-032 REFERENCE AUDIT (ERA5 vs the in-bay anemometer): "
              f"{era5_check['reason']}")
    except Exception as exc:  # noqa: BLE001
        print(f"\n  ADR-032 reference audit unavailable ({str(exc)[:80]})")

    result = {
        "source": str(LOG.relative_to(ROOT)),
        "built_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "truth": ("WELCOME ISLAND (AUT), ECCC station 4061 — a real anemometer on an island IN "
                  "Thunder Bay, ~4 km from the LSOFS node the product forecasts at"),
        "forecast": "gfs_seamless via Open-Meteo previous-runs (the run the product ships)",
        "scored_quantity": (f"upwelling DRIVE = trailing {ud.WINDOW_H:.0f} h favorable wind-run "
                            f"integral (kt*h) from the product's own wind.favorable_wind_run; "
                            f"favorable axis {ud.FAVORABLE_AXIS_DEG:.0f} deg"),
        "event_bar_kth": ud.EVENT_DRIVE_KTH,
        "n": len(recs), "n_days": len(days), "window": [days[0], days[-1]],
        "block_days_measured": block,
        "per_lead": per_lead,
        "leads_clearing_bar_h": clears,
        "skillful_horizon_h": horizon,
        "demote_leads": [L for L in leads if per_lead[str(L)]["demote"]],
        "event_verdict": ("issued" if ev_ok else
                          f"WITHHELD — {n_ep} distinct observed blow(s) in {len(days)} days "
                          f"(need {MIN_EVENT_EPISODES}). 44 event HOURS is three storms, and a "
                          f"day-block bootstrap tightens around whichever storm dominates the "
                          f"resamples rather than measuring the forecast. The contingency tables "
                          f"below are reported as a record to accumulate, not as a verdict."),
        "event_episodes": n_ep,
        "baseline_note": ("`skill_ratio` is against an ORACLE composite — the smaller of the "
                          "persistence and climatology errors PER SAMPLE, which no forecaster "
                          "could select in advance. It is the bar the other gates use and it is "
                          "deliberately conservative. `ratio_vs_persistence` and "
                          "`ratio_vs_climatology` are the honest single-baseline comparisons and "
                          "are far more favourable to the forecast."),
        "era5_reference_audit": era5_check,
        "lead_axis_note": ("previous_dayN is indexed by DAY, so a nominal lead of N*24 h is the "
                           "FLOOR of the true lead, which spans [N*24, N*24+23] h. Bins stay "
                           "ordered and non-overlapping; do not read an exact hour off them."),
        "method": ("Paired hours at one station. Baselines are observed persistence (the drive "
                   "last available at issue time, one lead earlier) and other-year day-of-year "
                   "climatology from the SAME instrument, harder of the two per sample. Block "
                   "bootstrap over contiguous day-blocks with the block length measured from the "
                   "daily error ACF. Nothing is fitted here, so there is no train/test split to "
                   "make; a lead-dependent confidence fitted FROM this record would need one."),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    print(f"\nleads clearing the ADR-006 bar: {clears or 'none'}  ->  horizon "
          + (f"+{horizon} h" if horizon else "NONE"))
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
