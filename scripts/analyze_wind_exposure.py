"""What is the airport reading worth over the water? (ADR-056)

Writes data/calib/wind_exposure.json.

The product's phase classifier fires on a sustained-blow threshold and reads CYQT, an airport
anemometer 16 km inland. The scorecard has carried a note for months that the airport-vs-lake
offset "is what a separate phase threshold would need to earn — currently within noise". That
offset was measured against NDBC buoys 130-200 km away, where land-vs-lake exposure and 200 km
of geography are the same number. Welcome Island sits 16 km from CYQT under one synoptic system,
which is what makes exposure separable.

THREE OUTPUTS, and the third is the one that decides anything:

  1. THE RELATIONSHIP. Multiplicative, additive and affine fits, trained on early years and
     scored on held-out later ones (rule 6 — this FITS parameters, so it splits).
  2. THE DIRECTION OFFSET, on the circle.
  3. THE DECISION FLIP. How often does the phase classifier disagree with itself when fed the
     airport instead of the lake — and does the fitted correction recover the lake's answer? A
     1 kt offset is a curiosity; "the bar is called wrong on N% of hours" is a defect.

    python scripts/analyze_wind_exposure.py --train-end 2024 --holdout-start 2025
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / "data" / "calib" / "wind_exposure.json"
SEASON = (4, 11)          # April..November — the open-water season the product operates in


def _pairs(years, *, timeout):
    """[(hour, land_kn, land_dir, lake_kn, lake_dir)] over the open-water season of each year."""
    from tbay_fishcast.ingest import asos_archive as asos, eccc_wind

    rows, used = [], []
    for y in years:
        s, e = date(y, SEASON[0], 1), date(y, SEASON[1], 30)
        try:
            land = asos.hourly(asos.fetch(s, e, timeout=timeout))
            lake = eccc_wind.fetch_hourly(s, e, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            print(f"  {y}: unavailable ({str(exc)[:70]})")
            continue
        lk = {w.time: w for w in lake}
        n0 = len(rows)
        for hr, lw in land.items():
            ow = lk.get(hr)
            if ow is None:
                continue
            rows.append((hr, lw.speed_kn, lw.dir_deg, ow.speed_kn, ow.dir_deg))
        if len(rows) > n0:
            used.append(y)
        print(f"  {y}: {len(land)} airport hours, {len(lake)} lake hours -> "
              f"{len(rows) - n0} paired")
    # The years that ACTUALLY made it in, published rather than assumed. A rate-limited fetch
    # once dropped 2021 and 2024 out of a "2018-2024" fit while the label kept saying 2018-2024.
    return rows, used


def main(argv) -> int:
    from tbay_fishcast.features import upwelling_drive as ud, wind_exposure as we

    ap = argparse.ArgumentParser()
    ap.add_argument("--train-start", type=int, default=2018)
    ap.add_argument("--train-end", type=int, default=2024)
    ap.add_argument("--holdout-start", type=int, default=2025)
    ap.add_argument("--holdout-end", type=int, default=datetime.now(timezone.utc).year)
    ap.add_argument("--timeout", type=float, default=300.0)
    a = ap.parse_args(argv)

    print(f"TRAIN {a.train_start}..{a.train_end}")
    train, train_years = _pairs(range(a.train_start, a.train_end + 1), timeout=a.timeout)
    print(f"HOLDOUT {a.holdout_start}..{a.holdout_end}")
    hold, hold_years = _pairs(range(a.holdout_start, a.holdout_end + 1), timeout=a.timeout)
    if len(train) < 500 or len(hold) < 200:
        print(f"insufficient paired data (train {len(train)}, holdout {len(hold)})")
        return 1
    print(f"\n{len(train)} training pairs · {len(hold)} held-out pairs")

    tr_sp = [(r[1], r[3]) for r in train]
    ho_sp = [(r[1], r[3]) for r in hold]

    # 1. THE RELATIONSHIP.
    fits, scores = {}, {}
    for form in ("multiplicative", "additive", "affine"):
        f = we.fit(tr_sp, form=form)
        s = we.score(f, ho_sp)
        fits[form] = None if f is None else f.as_dict()
        scores[form] = s
        if f and s:
            print(f"  {form:15s} scale {f.scale:.4f} offset {f.offset:+.3f} kn  ->  "
                  f"holdout MAE {s['corrected_mae_kn']:.3f} kn "
                  f"(bias {s['corrected_bias_kn']:+.3f}) vs uncorrected "
                  f"{s['uncorrected_mae_kn']:.3f} (bias {s['uncorrected_bias_kn']:+.3f})")
    ranked = sorted((k for k in scores if scores[k]),
                    key=lambda k: scores[k]["corrected_mae_kn"])
    best = ranked[0] if ranked else None
    print(f"  best on held-out data: {best}")

    # STRONG-WIND CHECK. The threshold only ever fires in the tail, and a fit dominated by light
    # winds can be excellent overall while being wrong exactly where it is consulted.
    strong = [(x, y) for x, y in ho_sp if x >= 10.0]
    strong_scores = {k: we.score(we.fit(tr_sp, form=k), strong) for k in fits if fits[k]}
    for k, s in strong_scores.items():
        if s:
            print(f"    strong-wind only (airport >= 10 kn, n={s['n_holdout']}): {k} MAE "
                  f"{s['corrected_mae_kn']:.3f} bias {s['corrected_bias_kn']:+.3f} "
                  f"(uncorrected {s['uncorrected_mae_kn']:.3f} / "
                  f"{s['uncorrected_bias_kn']:+.3f})")

    # 2. DIRECTION.
    dirs = we.circular_offset_deg([(r[2], r[4]) for r in hold
                                   if r[2] is not None and r[4] is not None])
    if dirs:
        print(f"  direction: lake minus land {dirs['mean_offset_deg']:+.1f} deg "
              f"(concentration {dirs['concentration']:.3f}, n={dirs['n']})")

    # 3. THE DECISION FLIP — the only output that can change the product.
    bf = we.fit(tr_sp, form=best) if best else None
    times = [r[0] for r in hold]
    lake_d = ud.drive_series(times, [r[3] for r in hold], [r[4] for r in hold])
    land_d = ud.drive_series(times, [r[1] for r in hold], [r[2] for r in hold])
    corr_d = ud.drive_series(times, [bf.apply(r[1]) for r in hold], [r[2] for r in hold]) \
        if bf else {}
    common = sorted(set(lake_d) & set(land_d) & (set(corr_d) if corr_d else set(land_d)))

    def _flip(other):
        dis = sum(1 for t in common if ud.is_event(lake_d[t]) != ud.is_event(other[t]))
        miss = sum(1 for t in common
                   if ud.is_event(lake_d[t]) and not ud.is_event(other[t]))
        false = sum(1 for t in common
                    if not ud.is_event(lake_d[t]) and ud.is_event(other[t]))
        return {"n_hours": len(common), "disagree": dis,
                "disagree_rate": round(dis / len(common), 4) if common else None,
                "missed_blows": miss, "false_blows": false}

    raw_flip = _flip(land_d)
    corr_flip = _flip(corr_d) if corr_d else None
    lake_events = sum(1 for t in common if ud.is_event(lake_d[t]))
    print(f"\n  DECISION FLIP over {len(common)} held-out hours "
          f"({lake_events} of them lake-events):")
    print(f"    airport raw:       {raw_flip['disagree']} disagreements "
          f"({raw_flip['disagree_rate']}) — {raw_flip['missed_blows']} missed blows, "
          f"{raw_flip['false_blows']} false")
    if corr_flip:
        print(f"    airport corrected: {corr_flip['disagree']} disagreements "
              f"({corr_flip['disagree_rate']}) — {corr_flip['missed_blows']} missed blows, "
              f"{corr_flip['false_blows']} false")

    result = {
        "built_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "land": "CYQT METAR via the Iowa State ASOS archive — the stream ingest/metar.py reads",
        "lake": "WELCOME ISLAND (AUT), ECCC 4061, an island station inside Thunder Bay",
        "separation_km": 16.0,
        "train_years_requested": [a.train_start, a.train_end],
        "train_years_used": train_years,
        "holdout_years_requested": [a.holdout_start, a.holdout_end],
        "holdout_years_used": hold_years,
        "n_train": len(train), "n_holdout": len(hold),
        "fits": fits, "holdout_scores": scores,
        "strong_wind_scores": strong_scores,
        "best_form": best,
        "direction_offset": dirs,
        "decision_flip_raw": raw_flip,
        "decision_flip_corrected": corr_flip,
        "lake_event_hours_holdout": lake_events,
        "caveat": ("Welcome Island is an ISLAND station, not a buoy — its anemometer stands on "
                   "land and so under-reads a true over-water wind by an unknown amount. It is "
                   "far closer to the lake than the airport is, and it is not the lake. Every "
                   "scale factor here is therefore a LOWER BOUND on the real land-to-water "
                   "speed-up."),
        "split_note": ("This fits parameters, so rule 6 applies and it splits temporally: the "
                       "scale is fitted on the early years and every number reported above is "
                       "from years the fit never saw."),
        "status": ("MEASUREMENT ONLY. Applying a correction to the phase classifier's input, or "
                   "giving the airport its own threshold, is a product change and needs an ADR "
                   "and sign-off per rule 11. Nothing here is wired into the heartbeat."),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    print(f"\nwrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
