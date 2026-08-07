"""Live wind gate — the "true over-lake test" that wind_model_eval.py explicitly deferred.

The upwelling signal (phase banner, favorability) is driven by the GFS forecast wind. That forecast
was chosen over ICON against ERA5 reanalysis (ADR-032), but reanalysis is not observation. The NDBC
buoys report REAL over-lake wind — the exact quantity that matters — so this gate accumulates, each
run, how the GFS forecast compares to the observed buoy wind over the overlapping recent hours,
focused on the upwelling-favorable west quadrant. Over time data/wind_gate_log.csv builds the record
that can (a) quantify the wind forecast's real skill and (b) justify down-weighting the upwelling
probability when observed and forecast disagree.

Offline of the 4x/day heartbeat (an accumulator, like the isotherm and offshore gates). CYQT METAR
is logged too as a land reference (it reads ~3 kn low vs the lake — documented). No LLM.

    python scripts/accumulate_wind_gate.py [issue YYYY-MM-DD]
"""
from __future__ import annotations

import csv
import json
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tbay_fishcast.features.wind import in_sector  # noqa: E402
from tbay_fishcast.ingest import metar, ndbc  # noqa: E402

LOG = Path(__file__).resolve().parents[1] / "data" / "wind_gate_log.csv"
OVERLAKE_BUOYS = ["45027", "45023"]          # real over-water anemometers on the lake
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
FORECAST_MODEL = "gfs_seamless"              # the deterministic run behind the gfs025 ensemble


def _forecast_wind(lat, lon):
    """{hour_iso: (speed_kn, dir_deg)} GFS forecast spanning the last 2 days + today."""
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "hourly": "wind_speed_10m,wind_direction_10m",
        "past_days": 2, "forecast_days": 1, "wind_speed_unit": "kn", "models": FORECAST_MODEL})
    with urllib.request.urlopen(f"{FORECAST_URL}?{q}", timeout=60) as r:
        h = json.loads(r.read()).get("hourly", {})
    out = {}
    for t, s, d in zip(h.get("time", []), h.get("wind_speed_10m", []), h.get("wind_direction_10m", [])):
        if s is not None and d is not None:
            out[t[:13]] = (float(s), float(d))     # key to the hour, "YYYY-MM-DDTHH"
    return out


def _airport_wind():
    """{hour_iso: speed_kn} observed CYQT (airport) wind, or {} if unavailable."""
    try:
        import urllib.request as _u
        recs = json.loads(_u.urlopen(f"{metar.METAR_URL}?ids={metar.CYQT}&format=json&hours=72",
                                     timeout=40).read())
        return {w.time.strftime("%Y-%m-%dT%H"): w.speed_kn
                for w in metar.parse_metar_json(recs) if w.speed_kn is not None}
    except Exception:  # noqa: BLE001
        return {}


def _score_buoy(bid, airport):
    b = ndbc.BUOYS[bid]
    obs = ndbc.fetch_stdmet_wind(bid)            # WindRecord list
    if not obs:
        return None
    fcst = _forecast_wind(b.lat, b.lon)
    if not fcst:
        return None
    o_all, f_all, o_fav, f_fav = [], [], [], []
    a_fav_diff, a_all_diff = [], []              # airport-minus-buoy offset (the threshold check)
    for w in obs:
        k = w.time.strftime("%Y-%m-%dT%H")
        if k not in fcst:
            continue
        fs, _fd = fcst[k]
        o_all.append(w.speed_kn); f_all.append(fs)
        fav = bool(in_sector(np.array([w.dir_deg]))[0])   # observed wind is in the W quadrant
        if fav:
            o_fav.append(w.speed_kn); f_fav.append(fs)
        if k in airport:
            a_all_diff.append(airport[k] - w.speed_kn)
            if fav:
                a_fav_diff.append(airport[k] - w.speed_kn)
    n = len(o_all)
    if n < 3:
        return None
    o_all, f_all = np.array(o_all), np.array(f_all)
    ofm = float(np.mean(o_fav)) if o_fav else None
    ffm = float(np.mean(f_fav)) if f_fav else None
    return {
        "buoy": bid, "n_hours": n,
        "mae_kn": round(float(np.mean(np.abs(f_all - o_all))), 2),
        "bias_kn": round(float(np.mean(f_all - o_all)), 2),
        "obs_fav_hours": len(o_fav),
        "obs_fav_mean_kn": round(ofm, 2) if ofm is not None else "",
        "fcst_fav_mean_kn": round(ffm, 2) if ffm is not None else "",
        "d_fav_kn": round(ffm - ofm, 2) if (ofm is not None and ffm is not None) else "",
        # airport-minus-buoy offset: accumulates the evidence for/against a separate airport
        # phase threshold (positive = CYQT reads HIGH vs the over-lake buoy).
        "airport_offset_kn": round(float(np.mean(a_all_diff)), 2) if a_all_diff else "",
        "airport_offset_fav_kn": round(float(np.mean(a_fav_diff)), 2) if a_fav_diff else "",
    }


def main(argv) -> int:
    issue = date.fromisoformat(argv[1]) if len(argv) > 1 else datetime.now(timezone.utc).date()
    airport = _airport_wind()
    rows = []
    for bid in OVERLAKE_BUOYS:
        try:
            s = _score_buoy(bid, airport)
        except Exception as e:  # noqa: BLE001
            print(f"buoy {bid}: skip ({type(e).__name__}: {str(e)[:50]})"); continue
        if s:
            rows.append(s)
    if not rows:
        print("no over-lake buoy/forecast overlap this run — nothing logged"); return 0
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fields = ["issue", "buoy", "n_hours", "mae_kn", "bias_kn", "obs_fav_hours",
              "obs_fav_mean_kn", "fcst_fav_mean_kn", "d_fav_kn",
              "airport_offset_kn", "airport_offset_fav_kn", "retrieved_utc"]
    new = not LOG.exists()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        for r in rows:
            r = {"issue": issue.isoformat(), **r, "retrieved_utc": stamp}
            w.writerow(r)
            fav = (f", W-quadrant obs {r['obs_fav_mean_kn']} vs fcst {r['fcst_fav_mean_kn']} kn "
                   f"(Δ{r['d_fav_kn']})" if r["obs_fav_hours"] else " (no W-quadrant hours)")
            print(f"wind gate {r['buoy']}: n={r['n_hours']} MAE {r['mae_kn']} kn, bias {r['bias_kn']}{fav}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
