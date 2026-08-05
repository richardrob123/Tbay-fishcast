"""Rigorous, generalized validation of the temperature engine against buoy truth.

The product's accuracy rests on one thing: can LSOFS (+ our correction) tell where
the target-temperature water sits? Thunder Bay has no in-water thermometer, so we
validate against every NDBC subsurface buoy in Lake Superior, over the full window
where LSOFS and buoy data overlap, and ask the questions that actually matter:

  1. TEMPERATURE SKILL  — raw vs corrected: MAE, bias, RMSE, correlation, and
     detrended anomaly correlation (timing skill, the decision-relevant metric).
  2. GENERALIZATION     — leave-one-buoy-out: fit the correction on the other buoys,
     predict the held-out one. This IS the Thunder Bay transfer test.
  3. DECISION SKILL     — isotherm-crossing contingency: does the model correctly
     call whether the 12 °C water is above/below the sensor depth? POD/FAR/accuracy.
  4. BASELINE           — does the corrected model beat persistence (yesterday's temp)?
     (ADR-008 demotion rule: beat the naive baseline or bench it.)

Two stages so the slow network fetch is cached:
    python scripts/validate_engine.py gather   # pull buoy+LSOFS paired series -> cache
    python scripts/validate_engine.py report   # compute metrics + write report/artifact

Truth: NDBC ocean thermistors. Model: LSOFS t12z n006. No LLM in the path.
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.verification.scorecard import (  # noqa: E402
    anomaly_correlation, band_coverage, leave_one_out_mae,
)
from tbay_fishcast.ingest import ndbc, lsofs_grid  # noqa: E402
from tbay_fishcast.ingest.backfill import _open_first  # noqa: E402
from tbay_fishcast.ingest.lsofs_extract import extract_nodes, valid_time_from_dataset  # noqa: E402
from tbay_fishcast.ingest.lsofs_paths import LsofsFile, candidate_urls  # noqa: E402

CACHE = Path(__file__).resolve().parents[1] / "scratch" / "engine_validation.json"
BUOY_SENSOR = {"45027": 6.0, "45023": 5.0, "45216": 3.0}  # fresh subsurface sensors
MATCH_H = 1.5
WINDOW_DAYS = 30   # walk back from the latest full LSOFS day


def gather(argv) -> int:
    cfg = load_config()
    latest = date(2026, 8, 4)
    days = [date.fromordinal(latest.toordinal() - k) for k in range(WINDOW_DAYS)]
    # buoy series once
    series = {}
    for sid in BUOY_SENSOR:
        try:
            series[sid] = [r for r in ndbc.fetch_ocean_realtime(int(sid))
                           if abs(r.depth_m - BUOY_SENSOR[sid]) < 0.6]
        except Exception as e:  # noqa: BLE001
            print(f"{sid}: buoy fetch failed {e}"); series[sid] = []

    def buoy_at(sid, vt):
        pool = [r for r in series[sid] if abs((r.time - vt).total_seconds()) <= MATCH_H * 3600]
        return float(np.mean([r.temp_c for r in pool])) if pool else None

    records = []
    for day in sorted(days):
        f = LsofsFile(day, "t12z", "n", 6)
        try:
            ds = _open_first(candidate_urls(f, cfg.lsofs.recent_bucket,
                                            cfg.lsofs.archive_bucket, byterange=False))
        except Exception:  # noqa: BLE001
            print(f"{day}: LSOFS unavailable"); continue
        grid = lsofs_grid.read_grid(ds)
        vt = valid_time_from_dataset(ds)
        for sid, z in BUOY_SENSOR.items():
            bt = buoy_at(sid, vt)
            if bt is None:
                continue
            nm = lsofs_grid.nearest_node(grid, ndbc.BUOYS[sid].lat, ndbc.BUOYS[sid].lon,
                                         min_depth_m=3.0)
            lz = extract_nodes(ds, {sid: nm.node}, [z])[0].temp_c
            records.append({"day": day.isoformat(), "buoy": sid, "depth": z,
                            "buoy_c": round(bt, 3), "lsofs_c": round(lz, 3)})
        ds.close()
        print(f"{day}: {sum(1 for r in records if r['day']==day.isoformat())} buoys")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(records, indent=1))
    print(f"\ncached {len(records)} paired records -> {CACHE}")
    return 0


# ---- metrics (pure) ----
def _mae(a, b): return float(np.mean(np.abs(np.asarray(a) - np.asarray(b))))
def _bias(a, b): return float(np.mean(np.asarray(a) - np.asarray(b)))   # model - truth
def _rmse(a, b): return float(np.sqrt(np.mean((np.asarray(a) - np.asarray(b)) ** 2)))


def _pearson(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def _anomaly_corr(model, truth, days):
    """Time-order then defer to the tested scorecard.anomaly_correlation."""
    order = np.argsort(days)
    return anomaly_correlation(np.asarray(model, float)[order], np.asarray(truth, float)[order])


def report(argv) -> int:
    if not CACHE.exists():
        print("no cache — run `gather` first"); return 1
    recs = json.loads(CACHE.read_text())
    if not recs:
        print("empty cache"); return 1
    by = {}
    for r in recs:
        by.setdefault(r["buoy"], []).append(r)

    # per-buoy correction = mean bias on that buoy (in-sample) vs leave-one-buoy-out
    buoy_bias = {b: _bias([r["lsofs_c"] for r in rs], [r["buoy_c"] for r in rs])
                 for b, rs in by.items()}
    pooled = float(np.mean(list(buoy_bias.values())))

    print("=" * 74)
    print("TEMPERATURE SKILL — raw vs corrected (per buoy, in-sample)")
    print(f"{'buoy':7s} {'z':>4s} {'n':>3s} | {'raw_MAE':>7s} {'raw_bias':>8s} | "
          f"{'cor_MAE':>7s} {'cor_bias':>8s} | {'r':>5s} {'anomR':>6s}")
    allm, allt = [], []
    for b, rs in by.items():
        m = [r["lsofs_c"] for r in rs]; t = [r["buoy_c"] for r in rs]
        d = [r["day"] for r in rs]
        cm = [x - buoy_bias[b] for x in m]  # in-sample corrected
        allm += m; allt += t
        print(f"{b:7s} {rs[0]['depth']:>4.0f} {len(rs):>3d} | {_mae(m,t):7.2f} {_bias(m,t):+8.2f} | "
              f"{_mae(cm,t):7.2f} {_bias(cm,t):+8.2f} | {_pearson(m,t):5.2f} {_anomaly_corr(m,t,[date.fromisoformat(x).toordinal() for x in d]):6.2f}")
    print(f"\nPOOLED raw MAE {_mae(allm,allt):.2f}  bias {_bias(allm,allt):+.2f}  "
          f"(pooled correction = {-pooled:+.2f} C)")

    # LEAVE-ONE-BUOY-OUT generalization
    print("\n" + "=" * 74)
    print("GENERALIZATION — leave-one-buoy-out (fit correction on others, predict held-out)")
    groups = {b: ([r["lsofs_c"] for r in rs], [r["buoy_c"] for r in rs]) for b, rs in by.items()}
    lobo = leave_one_out_mae(groups)
    for held, mae in lobo.items():
        others = [bb for bb in by if bb != held]
        corr = float(np.mean([buoy_bias[o] for o in others]))
        raw_mae = _mae(*groups[held])
        print(f"  held-out {held}: applied correction {-corr:+.2f} (from {others}) -> "
              f"MAE {mae:.2f} (raw {raw_mae:.2f})")
    print(f"  LOBO mean MAE {np.mean(list(lobo.values())):.2f}  <- the honest Thunder Bay transfer number")

    # DECISION SKILL — isotherm-crossing contingency at 12 C
    print("\n" + "=" * 74)
    print("DECISION SKILL — is 12 C water above the sensor? (model vs truth, corrected)")
    TP = FP = TN = FN = 0
    for b, rs in by.items():
        for r in rs:
            truth_cold = r["buoy_c"] <= 12.0            # cold water reached this depth
            model_cold = (r["lsofs_c"] - pooled) <= 12.0
            if truth_cold and model_cold: TP += 1
            elif model_cold and not truth_cold: FP += 1
            elif not model_cold and truth_cold: FN += 1
            else: TN += 1
    n = TP + FP + TN + FN
    pod = TP / (TP + FN) if (TP + FN) else float("nan")
    far = FP / (TP + FP) if (TP + FP) else float("nan")
    acc = (TP + TN) / n if n else float("nan")
    print(f"  n={n}  TP={TP} FP={FP} TN={TN} FN={FN}")
    print(f"  POD(hit rate)={pod:.2f}  FAR(false-alarm)={far:.2f}  accuracy={acc:.2f}")

    # BAND CALIBRATION — does the product's claimed uncertainty band contain truth?
    # Product band (from the 4-day live pooling): subsurface correction 3.31, band 1.51..5.55.
    print("\n" + "=" * 74)
    print("BAND CALIBRATION — is the honest interval actually honest?")
    LO_C, HI_C = 1.51, 5.55  # product's subsurface-bias band (C), same for all buoys
    cov = band_coverage([r["lsofs_c"] for r in recs], [r["buoy_c"] for r in recs], LO_C, HI_C)
    print(f"  product band [{LO_C:+.1f},{HI_C:+.1f}] C contains truth in "
          f"{round(cov*len(recs))}/{len(recs)} = {cov:.0%} of cases (target ~68% for +/-1sigma)")
    print(f"  -> {'well-calibrated' if 0.55<=cov<=0.85 else ('too NARROW' if cov<0.55 else 'too WIDE')}")

    # BASELINE — corrected model vs persistence (yesterday's buoy temp)
    print("\n" + "=" * 74)
    print("BASELINE — corrected model vs persistence (does it beat naive?)")
    for b, rs in by.items():
        rs = sorted(rs, key=lambda r: r["day"])
        t = [r["buoy_c"] for r in rs]
        cm = [r["lsofs_c"] - buoy_bias[b] for r in rs]
        pers_mae = _mae(t[1:], t[:-1])          # yesterday predicts today
        mod_mae = _mae(cm[1:], t[1:])
        verdict = "BEATS" if mod_mae < pers_mae else "loses to"
        print(f"  {b}: corrected MAE {mod_mae:.2f}  {verdict} persistence MAE {pers_mae:.2f}")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    raise SystemExit(gather(sys.argv) if cmd == "gather" else report(sys.argv))
