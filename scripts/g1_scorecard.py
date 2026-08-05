"""G1 commissioning scorecard — LSOFS model SST vs GLSEA SST (PLAN task 5, gate G1).

G1 target: MAE <= 2.0 C, ice-free months, vs best available truth.

Method (deliberately un-hand-wavy):
  * MODEL: LSOFS surface temperature (topmost sigma layer ~0.3 m = model SST). For
    each UTC day we average ALL nowcast valid-hours (deduped across cycles, keeping
    the latest issuance) into a daily-mean SST. This removes diurnal aliasing that a
    single-hour snapshot would inject. Days with < 18 valid hours are dropped, not
    thinly averaged.
  * TRUTH: GLSEA_ACSPO_GCS daily SST at the nearest valid water pixel to each LSOFS
    NODE's own coordinate (not the shore point) — model and truth sample the same
    water. Cloud-gapped (null) pixels drop that day.
  * SCORE: MAE / bias / RMSE / median|e| / p90|e| / Pearson r, per station + pooled.

Honest limit (ADR-019): this is SURFACE model SST vs SURFACE skin truth — it bounds
how well LSOFS tracks observed surface temperature. The 6 m gate proper needs an
in-situ logger; every line carries the depth caveat.

    python scripts/g1_scorecard.py 2025-07-01 2025-07-31 [--min-hours 18] [--workers 16]

Allowlisted sources only (LSOFS S3 + GLERL ERDDAP).
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.ingest.backfill import (  # noqa: E402
    BackfillItem, extract_surface_item, station_node_map,
)
from tbay_fishcast.ingest.glsea import fetch_sst  # noqa: E402
from tbay_fishcast.verification.g1 import (  # noqa: E402
    SurfaceSample, build_daily_surface, pair_model_truth,
)
from tbay_fishcast.verification.scorecard import temperature_error  # noqa: E402

CYCLE_HOURS = {"t00z": 0, "t06z": 6, "t12z": 12, "t18z": 18}


def _days(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _fetch_one(cfg, nodes, item) -> list[SurfaceSample]:
    """All station surface samples for one (day, cycle, hour), tagged with issuance."""
    issued = datetime(item.day.year, item.day.month, item.day.day,
                      CYCLE_HOURS[item.cycle])
    rows = extract_surface_item(cfg, item, nodes)
    return [SurfaceSample(r.station_id, r.valid_time.replace(tzinfo=None), r.temp_c, issued)
            for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("start", type=lambda s: datetime.fromisoformat(s).date())
    ap.add_argument("end", type=lambda s: datetime.fromisoformat(s).date())
    ap.add_argument("--min-hours", type=int, default=18)
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()

    cfg = load_config()
    nodes = station_node_map(cfg)
    split = cfg.temporal_split

    # Fetch full cycles for [start .. end+1] so each day's 00-23 UTC hours are covered
    # (a day's 18-24 h come from the next day's t00z cycle).
    fetch_days = list(_days(args.start, args.end + timedelta(days=1)))
    items = [BackfillItem(d, cyc, h)
             for d in fetch_days for cyc in cfg.lsofs.cycles
             for h in range(cfg.lsofs.nowcast_hours)]
    print(f"G1 {args.start}..{args.end} (split={split.assign(args.start)}): "
          f"fetching {len(items)} LSOFS files @ {args.workers} workers ...")

    samples: list[SurfaceSample] = []
    miss = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_fetch_one, cfg, nodes, it): it for it in items}
        for fut in as_completed(futs):
            try:
                samples.extend(fut.result())
            except Exception:  # noqa: BLE001 — missing file/hour; counted, not fatal
                miss += 1

    daily, dropped = build_daily_surface(samples, min_hours=args.min_hours)
    # keep only target window (buffer day drops out)
    daily = [d for d in daily if args.start <= d.day <= args.end]
    print(f"  LSOFS: {len(samples)} hourly samples, {miss} file misses; "
          f"{len(daily)} station-days kept, {len(dropped)} dropped (<{args.min_hours} h)")

    # GLSEA truth at each node coordinate, per kept day
    want = sorted({d.day for d in daily})
    truth: dict[tuple[str, date], float] = {}
    px_dist: list[float] = []
    gaps = 0

    def _truth(sid, s, d):
        return sid, d, fetch_sst(s.node_lat, s.node_lon, d.isoformat())

    jobs = [(s.id, s, d) for d in want for s in cfg.shore_stations]
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(_truth, sid, s, d) for sid, s, d in jobs]
        for fut in as_completed(futs):
            sid, d, px = fut.result()
            if px is None:
                continue
            truth[(sid, d)] = px.sst_c
            px_dist.append(px.dist_km)
    gaps = len(jobs) - len(truth)
    if px_dist:
        print(f"  GLSEA: {len(truth)} truth points ({gaps} cloud/mask gaps); "
              f"pixel dist mean {sum(px_dist)/len(px_dist):.1f} km, max {max(px_dist):.1f} km")

    # mean diurnal range at each station (context for the mean-vs-max spread)
    for s in cfg.shore_stations:
        rr = [d.diurnal_range_c for d in daily if d.station_id == s.id]
        if rr:
            print(f"  {s.id:22s} mean diurnal surface range {sum(rr)/len(rr):.2f} C")

    def scorecard(model_attr: str, label: str) -> None:
        paired = pair_model_truth(daily, truth, model_attr=model_attr)
        print(f"\n== model = LSOFS daily {label} surface vs GLSEA ==")
        print(f"{'station':22s} {'n':>3s} {'MAE':>5s} {'bias':>6s} {'RMSE':>5s} "
              f"{'med':>5s} {'p90':>5s} {'r':>6s}")
        all_m, all_t = [], []
        for s in cfg.shore_stations:
            pr = paired.get(s.id, [])
            if not pr:
                print(f"{s.id:22s}   0     —      —     —     —     —      —")
                continue
            m = [p[0] for p in pr]; t = [p[1] for p in pr]
            all_m += m; all_t += t
            st = temperature_error(m, t)
            print(f"{s.id:22s} {st.n:3d} {st.mae:5.2f} {st.bias:+6.2f} {st.rmse:5.2f} "
                  f"{st.median_abs:5.2f} {st.p90_abs:5.2f} {st.pearson_r:+6.2f}")
        if all_m:
            o = temperature_error(all_m, all_t)
            verdict = "PASS" if o.mae <= 2.0 else "FAIL"
            print(f"{'POOLED':22s} {o.n:3d} {o.mae:5.2f} {o.bias:+6.2f} {o.rmse:5.2f} "
                  f"{o.median_abs:5.2f} {o.p90_abs:5.2f} {o.pearson_r:+6.2f}   "
                  f"G1(<=2.0 C): {verdict}")

    if not any(paired_any := [truth.get((s.id, d.day)) is not None
                              for d in daily for s in cfg.shore_stations]):
        print("\nno paired data — cannot compute G1")
        return 1
    # Two framings bracket GLSEA's unknown temporal weighting (daily composite).
    scorecard("mean_temp_c", "MEAN")
    scorecard("max_temp_c", "MAX ")
    print(f"\ncaveat: surface model SST vs GLSEA skin, not the 6 m gate (ADR-019). "
          f"Window split: {split.assign(args.start)}. High r with a cold bias at "
          f"exposed nodes = LSOFS tracks the signal but runs cold vs satellite "
          f"(upwelling variability GLSEA smooths). The logger adjudicates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
