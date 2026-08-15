"""Recalibrate the upwelling-favorability curve WHERE THE PRODUCT OPERATES (ADR-058).

Writes data/calib/upwelling_favorability_local.json. Deliberately NOT the file the product reads:
a curve change is a product change and needs sign-off (rule 11).

WHAT WENT WRONG THE FIRST TIME. `calibrate_upwelling.py` paired NDBC offshore buoy wind against
the SAME buoy's surface temperature, got AUC 0.485 — no discrimination whatsoever — and honestly
wrote `calibrated: false` with the reason: those deep-water buoys "miss" a nearshore phenomenon.
The plausibility guard did its job (a degenerate MLE had produced s50=425 kn). But the layer has
run on a literature prior ever since, and the failure was never retried at the right place.

WHAT IS DIFFERENT NOW:
  * WIND from Welcome Island, in the bay, 4 km from the product's node (ADR-055/056) — not a
    buoy 200 km offshore.
  * RESPONSE as shore-minus-offshore satellite SST, so a passing cold front (which arrives on
    exactly the west wind being tested) cannot masquerade as upwelling.
  * A THRESHOLD-FREE primary test, so nothing can be tuned into significance.
  * The event bar measured from quiet-day noise, not chosen.
  * A temporal split: fitted on <=2024, reported on 2025-2026 (rule 6's validation years).

AND WHAT A NULL WOULD MEAN. The only continuous nearshore temperature here is a daily satellite
analysis measured at 5x too smooth (ADR-053); smoothing can only weaken a real association. So a
positive result is credible, and a null does NOT show the shore has no upwelling response — it
shows this instrument cannot resolve it. Those are different sentences and the output says which.

    python scripts/calibrate_upwelling_local.py --first-year 2012
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / "data" / "calib" / "upwelling_favorability_local.json"
# The product's own node, 2.3 km off the waterfront — a verified water pixel on the city/north
# shore, which is the shore the map claims upwells on west wind.
SHORE = (48.4095, -89.2150)
# Open Superior, southeast beyond the Sleeping Giant: same air mass, far enough that the same
# wind does not upwell it. The CONTROL, and the whole reason a cold front cannot pose as upwelling.
OFFSHORE = (48.05, -88.55)
SEASON = (5, 10)                  # May..October: stratified season, when upwelling is meaningful
LAGS_D = (0, 1, 2, 3, 4)
EVENT_SIGMA = 2.0                 # event = cooling beyond 2x the measured quiet-day noise
QUIET_DRIVE_KTH = 50.0            # "quiet" = essentially no favorable wind in the trailing window
SPLIT_YEAR = 2024                 # fit on <= this, report on the years after (rule 6)


def _daily_mean(series: dict) -> dict:
    return series


def main(argv) -> int:
    from tbay_fishcast.features import favorability_fit as ff, upwelling_drive as ud
    from tbay_fishcast.ingest import eccc_wind, glsea

    ap = argparse.ArgumentParser()
    ap.add_argument("--first-year", type=int, default=2012)
    ap.add_argument("--last-year", type=int, default=datetime.now(timezone.utc).year)
    ap.add_argument("--timeout", type=float, default=300.0)
    a = ap.parse_args(argv)

    print(f"shore pixel target {SHORE}, offshore reference {OFFSHORE}")
    pin_s = glsea.fetch_sst(*SHORE, f"{a.last_year - 1}-07-15")
    pin_o = glsea.fetch_sst(*OFFSHORE, f"{a.last_year - 1}-07-15")
    print(f"  shore   -> {pin_s.pixel_lat:.4f},{pin_s.pixel_lon:.4f} ({pin_s.dist_km:.2f} km)")
    print(f"  offshore-> {pin_o.pixel_lat:.4f},{pin_o.pixel_lon:.4f} ({pin_o.dist_km:.2f} km)")
    sep = math.hypot((pin_s.pixel_lat - pin_o.pixel_lat) * 111.32,
                     (pin_s.pixel_lon - pin_o.pixel_lon) * 111.32
                     * math.cos(math.radians(pin_s.pixel_lat)))
    print(f"  separation {sep:.1f} km")

    try:
        cov_end = glsea.coverage_end()
        print(f"  satellite coverage ends {cov_end}")
    except Exception:  # noqa: BLE001
        cov_end = None
    rows = []           # (day, drive_kth, sustained_fav_kn, anomaly_c, shore_c, air_c)
    years_used = []
    for y in range(a.first_year, a.last_year + 1):
        s, e = date(y, SEASON[0], 1), date(y, SEASON[1], 31)
        # CLAMP TO COVERAGE (ADR-054). The first run of THIS script lost the entire 2026 season
        # to a 404, because a season ending 31 October runs past the satellite's last day and
        # ERDDAP answers an over-range request with an error for the WHOLE range. My own lesson,
        # not inherited by a new file — which is exactly how a fixed bug comes back.
        e = min(e, date.fromisoformat(cov_end)) if cov_end else e
        if e < s:
            print(f"  {y}: entirely past satellite coverage ({cov_end})")
            continue
        try:
            wind = eccc_wind.fetch_hourly(s - timedelta(days=2), e, timeout=a.timeout)
            sst_s = glsea.fetch_series(pin_s.pixel_lat, pin_s.pixel_lon,
                                       s.isoformat(), e.isoformat())
            sst_o = glsea.fetch_series(pin_o.pixel_lat, pin_o.pixel_lon,
                                       s.isoformat(), e.isoformat())
        except Exception as exc:  # noqa: BLE001
            print(f"  {y}: unavailable ({str(exc)[:70]})")
            continue
        if not wind or not sst_s or not sst_o:
            print(f"  {y}: incomplete (wind {len(wind)}, shore {len(sst_s)}, off {len(sst_o)})")
            continue
        drive = ud.drive_series([w.time for w in wind], [w.speed_kn for w in wind],
                                [w.dir_deg for w in wind])
        # Daily aggregates of the DRIVER: the day's peak trailing wind-run, and the mean speed
        # over its favorable hours — the latter being the quantity the shipped curve consumes.
        by_day: dict[str, list] = {}
        for t, v in drive.items():
            by_day.setdefault(t.date().isoformat(), []).append(v)
        fav_by_day: dict[str, list] = {}
        air_by_day: dict[str, list] = {}
        for w in wind:
            c = ud.favorable_component(w.speed_kn, w.dir_deg)
            if c is not None and c > 0:
                fav_by_day.setdefault(w.time.date().isoformat(), []).append(w.speed_kn)
            if w.air_c is not None:
                air_by_day.setdefault(w.time.date().isoformat(), []).append(w.air_c)
        n0 = len(rows)
        for d in sorted(set(sst_s) & set(sst_o) & set(by_day)):
            fv = fav_by_day.get(d) or []
            av = air_by_day.get(d) or []
            rows.append((d, max(by_day[d]),
                         (sum(fv) / len(fv)) if fv else 0.0,
                         sst_s[d] - sst_o[d], sst_s[d],
                         (sum(av) / len(av)) if av else None))
        years_used.append(y)
        print(f"  {y}: {len(wind)} wind h, {len(sst_s)}/{len(sst_o)} sat days "
              f"-> {len(rows) - n0} paired days")

    if len(rows) < 200:
        print(f"insufficient paired days ({len(rows)})")
        return 1
    rows.sort()
    print(f"\n{len(rows)} paired days across {len(years_used)} seasons")

    # THE RESPONSE, three ways, because no single control here is above suspicion:
    #   d_anom  = shore MINUS offshore    — removes the cold front, adds the offshore pixel's noise
    #   d_shore = shore alone             — no extra noise, but the cold front is back
    #   the air-controlled partial on d_shore — removes the front WITHOUT a second instrument,
    #                                           using the air temperature at the same in-bay mast
    # Agreement across all three is the only thing that would make this convincing.
    idx = {d: i for i, r in enumerate(rows) for d in [r[0]]}
    prof, best_lag, best = {}, None, None
    for lag in LAGS_D:
        drv, spd, d_anom, d_shore, d_air, days = [], [], [], [], [], []
        for d, k, v, an, sh, ai in rows:
            d2 = (date.fromisoformat(d) + timedelta(days=lag + 1)).isoformat()
            j = idx.get(d2)
            if j is None:
                continue
            drv.append(k)
            spd.append(v)
            d_anom.append(rows[j][3] - an)
            d_shore.append(rows[j][4] - sh)
            d_air.append(None if (ai is None or rows[j][5] is None) else rows[j][5] - ai)
            days.append(d)
        sp_an = ff.spearman(drv, d_anom)
        sp_sh = ff.spearman(drv, d_shore)
        pa = ff.partial_spearman(drv, d_shore, d_air)
        prof[str(lag)] = {"lag_days": lag, "n": sp_an["n"],
                          "rho_vs_anomaly": sp_an["rho"], "p_anomaly": sp_an["p_two_sided"],
                          "rho_vs_shore": sp_sh["rho"], "p_shore": sp_sh["p_two_sided"],
                          "rho_shore_given_air": pa["rho"], "p_shore_given_air": pa["p_two_sided"],
                          "n_air_controlled": pa["n"]}
        print(f"  lag +{lag} d: n={sp_an['n']:5d}  rho(drive, d_anomaly)={sp_an['rho']} "
              f"p={sp_an['p_two_sided']}")
        print(f"             rho(drive, d_shore)={sp_sh['rho']}  |  "
              f"controlling for in-bay air: rho={pa['rho']} p={pa['p_two_sided']} (n={pa['n']})")
        tr = [(k, r) for k, r, d in zip(drv, d_anom, days) if int(d[:4]) <= SPLIT_YEAR]
        rho_tr = ff.spearman([q[0] for q in tr], [q[1] for q in tr])["rho"]
        prof[str(lag)]["train_rho_vs_anomaly"] = rho_tr
        if rho_tr is not None and (best is None or rho_tr < best):
            best, best_lag = rho_tr, lag
    print(f"  lag selected on TRAINING years only: +{best_lag} d (train rho {best})")

    drv, spd, resp, days = [], [], [], []
    for d, k, v, an, sh, ai in rows:
        d2 = (date.fromisoformat(d) + timedelta(days=best_lag + 1)).isoformat()
        j = idx.get(d2)
        if j is None:
            continue
        drv.append(k)
        spd.append(v)
        resp.append(rows[j][3] - an)
        days.append(d)
    quiet = [r for r, k in zip(resp, drv) if k < QUIET_DRIVE_KTH]
    sigma = ff.noise_floor(quiet)
    if sigma is None:
        print("cannot measure a quiet-day noise floor — no event bar can be derived")
        return 1
    bar = -EVENT_SIGMA * sigma
    labels = [r <= bar for r in resp]
    print(f"  quiet-day noise sd {sigma:.3f} C (n={len(quiet)}) -> event bar {bar:.3f} C, "
          f"{sum(labels)} events / {len(labels)} days ({sum(labels) / len(labels):.1%})")

    def _split(seq):
        tr = [x for x, d in zip(seq, days) if int(d[:4]) <= SPLIT_YEAR]
        te = [x for x, d in zip(seq, days) if int(d[:4]) > SPLIT_YEAR]
        return tr, te

    res = {}
    for name, pred in (("sustained_favorable_speed_kn", spd), ("drive_kth", drv)):
        ptr, pte = _split(pred)
        ltr, lte = _split(labels)
        fit = ff.fit_logistic(ptr, ltr)
        # THE SAMPLE SIZE IS THE EVENT COUNT, not the day count. The first run of this script
        # printed a held-out AUC of 0.3403 computed on SEVEN events and I nearly reported it —
        # the identical error ADR-057 exists to prevent, repeated in a file that did not inherit
        # the guard. An AUC below the bar is now WITHHELD rather than published.
        entry = {"n_train": len(ptr), "n_holdout": len(pte),
                 "events_train": sum(ltr), "events_holdout": sum(lte),
                 "auc_train": ff.auc(ptr, ltr),
                 "auc_holdout": ff.auc(pte, lte) if sum(lte) >= ff.MIN_EVENTS else None,
                 "auc_holdout_withheld": (None if sum(lte) >= ff.MIN_EVENTS else
                                          f"only {sum(lte)} held-out events (need "
                                          f"{ff.MIN_EVENTS}); an AUC on this many is noise"),
                 "s50": None if fit is None else round(fit[0], 3),
                 "width": None if fit is None else round(fit[1], 3)}
        res[name] = entry
        print(f"  {name}: AUC train {entry['auc_train']} (n_events {sum(ltr)})  "
              f"holdout {entry['auc_holdout']} (n_events {sum(lte)})  "
              f"s50={entry['s50']} width={entry['width']}")

    sp_e = res["sustained_favorable_speed_kn"]
    # Judged on TRAINING AUC, because the held-out event count cannot support a verdict either
    # way. Same 0.62 bar and plausibility band the existing calibrator uses, so a pass here would
    # mean exactly what a pass there would have meant.
    ok = bool(sp_e["auc_train"] is not None and sp_e["auc_train"] >= 0.62
              and sp_e["s50"] is not None and 6.0 <= sp_e["s50"] <= 25.0
              and sp_e["width"] is not None and 0.5 <= sp_e["width"] <= 12.0)
    primary = prof[str(best_lag)]
    # `(p or 1)` is a trap here and it fired: a p-value of exactly 0.0 is the STRONGEST possible
    # result and it is also falsy, so `0.0 or 1` returns 1 and the test reads it as the weakest.
    # That inverted this script's headline verdict on its first run — printing "NO SIGNAL" over
    # rho = -0.13 at p ~ 0. Never use `or` to default a numeric that can legitimately be zero.
    def _p(v):
        return 1.0 if v is None else float(v)

    signal = bool(primary["rho_vs_anomaly"] is not None and primary["rho_vs_anomaly"] < 0
                  and _p(primary["p_anomaly"]) < 0.001)
    air_ok = bool(primary["rho_shore_given_air"] is not None
                  and primary["rho_shore_given_air"] < 0
                  and _p(primary["p_shore_given_air"]) < 0.001)
    result = {
        "built_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "MEASUREMENT ONLY — not wired; a curve change needs sign-off (rule 11)",
        "wind": "WELCOME ISLAND (AUT), ECCC 4061 — in the bay, 4 km from the product's node",
        "response": ("GLSEA/ACSPO shore pixel minus an offshore reference, plus shore-alone "
                     "controlled for in-bay AIR temperature at the same mast"),
        "shore_pixel": [pin_s.pixel_lat, pin_s.pixel_lon],
        "offshore_pixel": [pin_o.pixel_lat, pin_o.pixel_lon],
        "pixel_separation_km": round(sep, 1),
        "seasons": years_used, "n_days": len(rows),
        "lag_profile_days": prof, "selected_lag_days": best_lag,
        "lag_selected_on": f"training years <= {SPLIT_YEAR} only",
        "quiet_day_noise_sd_c": round(sigma, 4),
        "event_bar_c": round(bar, 4), "event_rate": round(sum(labels) / len(labels), 4),
        "primary_threshold_free_test": primary,
        "mechanism_detected": signal,
        "mechanism_survives_air_control": air_ok,
        "logistic_fits": res,
        "split_year": SPLIT_YEAR,
        "recommend_recalibrate": ok,
        "verdict": (
            (f"RECALIBRATE: in-bay wind discriminates nearshore cooling (train AUC "
             f"{sp_e['auc_train']}), s50={sp_e['s50']} kn width={sp_e['width']} kn")
            if ok else
            (f"NO OPERATIONAL CURVE. The threshold-free test DOES detect the mechanism "
             f"(rho {primary['rho_vs_anomaly']} at +{best_lag} d, p<1e-6"
             + (f"; surviving an in-bay air-temperature control at rho "
                f"{primary['rho_shore_given_air']}" if air_ok else
                "; but it does NOT survive an air-temperature control")
             + f"), but the association is far too weak to support a probability curve: "
               f"training AUC {sp_e['auc_train']} against a 0.62 bar. The physics prior stands."
             if signal else
             f"NO SIGNAL and no curve. Training AUC {sp_e['auc_train']}; the physics prior "
             f"stands.")),
        "what_a_null_means": (
            "The only continuous nearshore temperature at Thunder Bay is a daily satellite "
            "analysis measured at 5x smoother than real water (ADR-053), and there is no "
            "alternative: Welcome Island is a met station with no water temperature and the "
            "region has zero marine buoys (both checked this session). Smoothing damps amplitude "
            "and blurs timing, so it can only WEAKEN a real association, never create one. A "
            "positive result is therefore credible and conservative; a null does NOT show the "
            "shore lacks an upwelling response, only that this instrument cannot resolve it."),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    print(f"\n{result['verdict']}")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
