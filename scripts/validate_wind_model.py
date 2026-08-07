"""Which wind model should drive the upwelling signal — decided by DATA, not by 'finer = better'.

GFS 0.25 deg (~28 km) barely resolves the over-lake blow; icon_seamless blends ICON-D2 (2.2 km)
/ ICON-EU (7 km) and *should* resolve the lake fetch better. But "should" is a hypothesis, and
this project does not swap a heartbeat input on a hunch (rules 6/8). So we test it:

  * REFERENCE: ERA5 reanalysis at the Thunder Bay over-lake point (archive-api.open-meteo.com).
    ERA5 is itself ~31 km reanalysis, NOT ground truth — but it assimilates the obs network
    (incl. the NDBC over-lake buoys) and is the standard independent reference for over-water
    wind, far better than the land-based CYQT airport for the over-LAKE wind that matters here.
  * CANDIDATES: each model's ARCHIVED FORECAST (historical-forecast-api.open-meteo.com) over the
    same point and window — what the model actually predicted, not its own analysis.

We score each candidate against ERA5 on the metric the product cares about: the UPWELLING-
FAVORABLE (west-quadrant) wind SPEED. A model that nails calm easterlies but misses the
west blow is useless here. Verdict recommends a switch ONLY if a candidate beats the incumbent
(gfs) by a MATERIAL margin on favorable-sector speed error; otherwise it records "keep gfs,
inconclusive" — a null result is a valid, honest outcome (cf. calibrate_upwelling.py).

Writes data/calib/wind_model_eval.json. Network; run offline of the heartbeat. No LLM.

Usage: python scripts/validate_wind_model.py [--days N] [--end YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import math
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "calib" / "wind_model_eval.json"

# Over-LAKE point off the Thunder Bay city shore (not the airport) — where the upwelling wind
# fetch matters. Matches the ensemble fetch point in ingest/wind_forecast.py.
LAT, LON = 48.42, -89.22
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
HIST_FCST = "https://historical-forecast-api.open-meteo.com/v1/forecast"
INCUMBENT = "gfs_seamless"
CANDIDATES = ["gfs_seamless", "icon_seamless"]
FAVORABLE_SECTOR = (200.0, 320.0)   # west-through-northwest: the upwelling-driving quadrant
MATERIAL_GAIN = 0.10                  # require >=10% lower favorable-sector MAE to recommend a swap


def _get(url: str, params: dict) -> dict:
    q = urllib.parse.urlencode(params, safe=",")
    with urllib.request.urlopen(f"{url}?{q}", timeout=60) as r:
        return json.loads(r.read())


def _hourly(url, model, start, end):
    p = {"latitude": LAT, "longitude": LON,
         "hourly": "wind_speed_10m,wind_direction_10m",
         "start_date": start, "end_date": end, "wind_speed_unit": "kn"}
    if model:
        p["models"] = model
    h = _get(url, p).get("hourly", {})
    return h.get("time", []), h.get("wind_speed_10m", []), h.get("wind_direction_10m", [])


def _in_sector(d, lo, hi):
    return d is not None and lo <= d <= hi


def _score(ref_t, ref_s, ref_d, mod_t, mod_s, mod_d):
    """MAE / bias of a model's speed vs ERA5, overall and in the favorable sector, on the
    hours both have valid data. Favorable = ERA5 says the wind is in the upwelling quadrant."""
    ref = {t: (s, d) for t, s, d in zip(ref_t, ref_s, ref_d) if s is not None}
    alld, favd = [], []
    n_fav = 0
    for t, s, d in zip(mod_t, mod_s, mod_d):
        if s is None or t not in ref:
            continue
        rs, rd = ref[t]
        err = s - rs
        alld.append(err)
        if _in_sector(rd, *FAVORABLE_SECTOR):
            favd.append(err)
            n_fav += 1
    def mae(xs):
        return sum(abs(x) for x in xs) / len(xs) if xs else None
    def bias(xs):
        return sum(xs) / len(xs) if xs else None
    return {
        "n_hours": len(alld), "n_favorable_hours": n_fav,
        "mae_kn": round(mae(alld), 3) if alld else None,
        "bias_kn": round(bias(alld), 3) if alld else None,
        "favorable_mae_kn": round(mae(favd), 3) if favd else None,
        "favorable_bias_kn": round(bias(favd), 3) if favd else None,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=35)
    ap.add_argument("--end", default=None, help="YYYY-MM-DD; default = 3 days ago (archive lag)")
    a = ap.parse_args(argv)
    end = date.fromisoformat(a.end) if a.end else date.today() - timedelta(days=3)
    start = end - timedelta(days=a.days)
    s, e = start.isoformat(), end.isoformat()

    try:
        ref = _hourly(ARCHIVE, None, s, e)               # ERA5 reference
    except Exception as ex:  # noqa: BLE001
        print(f"ERA5 archive unreachable ({type(ex).__name__}: {ex}); cannot validate — keeping incumbent.")
        return 1
    if not ref[0]:
        print("ERA5 archive returned no hours; cannot validate.")
        return 1

    scores = {}
    for m in CANDIDATES:
        try:
            mod = _hourly(HIST_FCST, m, s, e)
            scores[m] = _score(*ref, *mod)
        except Exception as ex:  # noqa: BLE001
            scores[m] = {"error": f"{type(ex).__name__}: {ex}"}

    inc = scores.get(INCUMBENT, {})
    inc_fav = inc.get("favorable_mae_kn")
    best, verdict = INCUMBENT, "keep incumbent (inconclusive)"
    if inc_fav:
        gains = {}
        for m, sc in scores.items():
            fm = sc.get("favorable_mae_kn")
            if m == INCUMBENT or not fm:
                continue
            gains[m] = (inc_fav - fm) / inc_fav       # positive = better than incumbent
        winner = max(gains, key=gains.get) if gains else None
        if winner and gains[winner] >= MATERIAL_GAIN:
            best, verdict = winner, (
                f"switch to {winner}: {gains[winner]*100:.0f}% lower favorable-sector MAE vs "
                f"{INCUMBENT} ({scores[winner]['favorable_mae_kn']} vs {inc_fav} kn)")
        elif gains:
            m2 = max(gains, key=gains.get)
            verdict = (f"keep {INCUMBENT}: best candidate {m2} only "
                       f"{gains[m2]*100:+.0f}% (< {MATERIAL_GAIN*100:.0f}% bar) on favorable-sector MAE")

    result = {
        "point": [LAT, LON], "window": {"start": s, "end": e, "days": a.days},
        "reference": "ERA5 reanalysis (archive-api) — independent reference, NOT ground truth",
        "favorable_sector_deg": list(FAVORABLE_SECTOR),
        "material_gain_bar": MATERIAL_GAIN,
        "incumbent": INCUMBENT, "scores": scores,
        "recommended_model": best, "verdict": verdict,
        "note": ("Candidates scored on upwelling-favorable (west-quadrant) wind-speed error vs "
                 "ERA5. A switch is recommended only on a material favorable-sector gain; a null "
                 "result keeps the reliably-available GFS (rule 6: don't swap a heartbeat input "
                 "on a hunch). ERA5 is a reanalysis reference; the true over-lake test is the "
                 "NDBC buoy wind, deferred to a live wind gate."),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("window", "scores", "recommended_model", "verdict")}, indent=2))
    print(f"\nwrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
