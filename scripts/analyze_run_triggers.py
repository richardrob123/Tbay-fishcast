"""Do the run TRIGGERS the calendar asserts show up in the local record? (ADR-061)

WHAT IS BEING TESTED, AND WHY IT MATTERS. `events_calendar.yaml` carries two trigger claims for
the fall runs, both T2 literature:

    pink_run.modifiers.rain_trigger   "first cool rain after Aug 20 = starting gun"  (effect 1.8)
    chinook_staging                   "nights-cooling trigger"

and `features/river_flow.py` ships a `freshet` boolean on every river-mouth marker, documented as
"the staging/run trigger". None of those has ever been checked against this bay's own record. A
trigger that is asserted but not measured is the exact shape rule 8 exists to catch.

THE TWO CONTROLS.

  FLOW. For every dated pink report in the observation ledger, compare the daily-mean discharge on
  the report day against the trailing 7-day median at the same gauge, and against that season's
  median. If reports rode freshets, both ratios sit well above 1.

  THERMAL. Onset temperature alone proves nothing: mid-September is ~12 C at night here whether or
  not fish care, so ANY September onset date will "cross" ~12 C. The test that can fail is whether
  warm years run LATE — i.e. whether the observed year<->onset-date pairing gives a TIGHTER spread
  of onset temperature than a shuffled pairing. That permutation null is the whole test.

WHAT THIS CANNOT SETTLE. The ledger is dominated by iNaturalist sightings, and observer effort
falls on rainy, high-water days. A null on the flow control is therefore CONFOUNDED — it cannot
separate "fish do not run on freshets" from "nobody photographs fish in the rain". It is still
enough to retire the ASSERTION: the product may not tell someone a rising river means fish until
something here says it does. The thermal control has no such confound, because it compares
pairings of the same dates, so effort bias cancels.

Truth for temperature is the IN-BAY station (Welcome Island, ECCC hourly since 1994) — the same
instrument ADR-056 moved the wind layer onto, not a reanalysis grid point.

No LLM (ADR-001); pure statistics over validated rows.

    python scripts/analyze_run_triggers.py
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import requests  # noqa: E402

OUT = ROOT / "data" / "calib" / "run_trigger_skill.json"
DAILY_MEAN = "https://api.weather.gc.ca/collections/hydrometric-daily-mean/items"

# Gauges that drain to the run-species mouths. The Kam is deliberately excluded: at 6481 km2 it
# integrates a basin far larger than the creeks the pinks actually enter, so its hydrograph is a
# different signal.
FLOW_GAUGES = (("current", "02AB021"), ("neebing", "02AB008"))

SEASON = (8, 10)               # Aug-Oct: the run months
MIN_HOURS_PER_DAY = 20         # a 3-hour day can miss the night minimum entirely
TRAIL_D = 7                    # trailing window for both the flow baseline and the night-min mean
PERM_DRAWS = 4000
MIN_YEARS = 5                  # below this the permutation null has no resolution worth printing


def _get(url, params, timeout=90.0, tries=4):
    """Retried. ECCC and Open-Meteo both answer an overload with a body, not an error."""
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            import time
            time.sleep(2 ** i * 2)
    raise last


def report_days(rows) -> list[date]:
    """Distinct dated pink report DAYS in the run months.

    DEDUPED TO THE DAY on purpose: 2023-09-14 carries six iNaturalist records that are one
    observer's afternoon, not six independent events. Counting them six times would let a single
    outing dominate every statistic below."""
    return sorted({date.fromisoformat(r["date"]) for r in rows
                   if r.get("species") == "pink" and r.get("date_precision") == "day"
                   and SEASON[0] <= date.fromisoformat(r["date"]).month <= SEASON[1]})


def flow_control(days: list[date], *, timeout: float) -> dict:
    out = {}
    years = sorted({d.year for d in days})
    for name, stn in FLOW_GAUGES:
        q: dict[date, float] = {}
        got_years = []
        for y in years:
            r = _get(DAILY_MEAN, {"STATION_NUMBER": stn, "limit": 400,
                                  "datetime": f"{y}-07-01T00:00:00Z/{y}-11-15T00:00:00Z",
                                  "f": "json"}, timeout=timeout)
            n = 0
            for f in r.json().get("features", []):
                p = f["properties"]
                if p.get("DISCHARGE") is None or p.get("DATE") is None:
                    continue
                q[date.fromisoformat(p["DATE"][:10])] = float(p["DISCHARGE"])
                n += 1
            if n:
                got_years.append(y)
        vs_trail, vs_season, rows = [], [], []
        for d in days:
            if d not in q:
                continue
            trail = [q[d - timedelta(days=k)] for k in range(1, TRAIL_D + 1) if d - timedelta(days=k) in q]
            seas = [v for k, v in q.items()
                    if k.year == d.year and date(d.year, 8, 15) <= k <= date(d.year, 10, 15)]
            a = statistics.median(trail) if len(trail) >= 4 else None
            b = statistics.median(seas) if len(seas) >= 20 else None
            r1 = q[d] / a if a else None
            r2 = q[d] / b if b else None
            if r1 is not None:
                vs_trail.append(r1)
            if r2 is not None:
                vs_season.append(r2)
            rows.append({"date": d.isoformat(), "q_cms": round(q[d], 3),
                         "trail_med": None if a is None else round(a, 3),
                         "ratio_trailing": None if r1 is None else round(r1, 2),
                         "ratio_season": None if r2 is None else round(r2, 2)})
        out[name] = {
            "station": stn, "years_with_archive": got_years, "n_report_days": len(rows),
            "median_ratio_trailing_7d": round(statistics.median(vs_trail), 3) if vs_trail else None,
            "median_ratio_season": round(statistics.median(vs_season), 3) if vs_season else None,
            "frac_above_1p2x_trailing": (round(sum(1 for v in vs_trail if v > 1.2) / len(vs_trail), 3)
                                         if vs_trail else None),
            "rows": rows,
        }
    return out


def _daily_min_inbay(years, *, timeout: float) -> dict[date, float]:
    from tbay_fishcast.ingest import eccc_wind
    today = datetime.now(timezone.utc).date()
    t: dict[date, float] = {}
    for y in years:
        end = min(date(y, 10, 31), today)
        if end < date(y, 7, 1):
            continue
        obs = eccc_wind.fetch_hourly(date(y, 7, 1), end, timeout=timeout, allow_partial=True)
        by_day: dict[date, list[float]] = {}
        for w in obs:
            if w.air_c is None:
                continue
            # local calendar day (UTC-5 in season): "night minimum" should mean the night a person
            # standing on the shore actually felt, not a UTC day split mid-evening
            by_day.setdefault((w.time - timedelta(hours=5)).date(), []).append(w.air_c)
        for d, v in by_day.items():
            if len(v) >= MIN_HOURS_PER_DAY:
                t[d] = min(v)
    return t


def thermal_control(days: list[date], *, timeout: float, seed: int = 1) -> dict:
    first = {}
    for d in days:
        first.setdefault(d.year, d)
        first[d.year] = min(first[d.year], d)
    years = sorted(first)
    t = _daily_min_inbay(years, timeout=timeout)

    def trail(y: int, ref: date):
        d0 = ref.replace(year=y)
        w = [t[d0 - timedelta(days=k)] for k in range(TRAIL_D) if d0 - timedelta(days=k) in t]
        return statistics.mean(w) if len(w) == TRAIL_D else None

    obs = {y: trail(y, first[y]) for y in years}
    usable = [y for y in years if obs[y] is not None]
    res = {"station": "welcome_island (ECCC hourly)", "trailing_days": TRAIL_D,
           "onsets": [{"year": y, "first_report": first[y].isoformat(),
                       "night_min_7d_c": None if obs[y] is None else round(obs[y], 1)}
                      for y in years],
           "n_years": len(usable)}
    if len(usable) < MIN_YEARS:
        res["verdict"] = f"insufficient in-bay coverage ({len(usable)} years, need {MIN_YEARS})"
        return res
    vals = [obs[y] for y in usable]
    obs_sd = statistics.pstdev(vals)
    refs = [first[y] for y in usable]
    rnd = random.Random(seed)
    null = []
    for _ in range(PERM_DRAWS):
        perm = rnd.sample(refs, len(refs))
        v = [trail(y, r) for y, r in zip(usable, perm)]
        v = [x for x in v if x is not None]
        if len(v) == len(usable):
            null.append(statistics.pstdev(v))
    null.sort()
    p = sum(1 for v in null if v <= obs_sd) / len(null) if null else None
    res.update({
        "onset_night_min_median_c": round(statistics.median(vals), 2),
        "onset_night_min_range_c": [round(min(vals), 2), round(max(vals), 2)],
        "observed_sd_c": round(obs_sd, 3),
        "null_draws": len(null),
        "null_sd_median_c": round(statistics.median(null), 3) if null else None,
        "null_sd_p05_p95_c": ([round(null[int(.05 * len(null))], 3),
                               round(null[int(.95 * len(null))], 3)] if null else None),
        "p_null_tighter_or_equal": None if p is None else round(p, 3),
        "detected": bool(p is not None and p < 0.05),
        "onset_doy_range": [min(d.timetuple().tm_yday for d in refs),
                            max(d.timetuple().tm_yday for d in refs)],
    })
    res["verdict"] = ("thermal anchoring DETECTED — warm years run late"
                      if res["detected"] else
                      "NO thermal anchoring beyond the calendar: the onset temperature is what "
                      "mid-September is in this bay, whatever date the fish arrive on")
    return res


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=300.0)
    a = ap.parse_args(argv)

    from tbay_fishcast.knowledge import observations as obs_mod
    rows = obs_mod.load()
    days = report_days(rows)
    print(f"ledger rows: {len(rows)}; distinct dated pink report DAYS in Aug-Oct: {len(days)} "
          f"across {len({d.year for d in days})} years")
    if len(days) < 10:
        print("too few dated reports to test a trigger")
        return 1

    print("\nFLOW control — does a report day sit on rising water?")
    flow = flow_control(days, timeout=a.timeout)
    for name, r in flow.items():
        print(f"  {name:9s} ({r['station']}): {r['n_report_days']} report-days with archive; "
              f"median Q/trailing-7d = {r['median_ratio_trailing_7d']}, "
              f"median Q/season = {r['median_ratio_season']}, "
              f"{r['frac_above_1p2x_trailing']} of days >1.2x trailing")

    print("\nTHERMAL control — do warm years run late?")
    therm = thermal_control(days, timeout=a.timeout)
    for o in therm["onsets"]:
        print(f"  {o['year']}  first report {o['first_report']}  "
              f"night-min 7d {o['night_min_7d_c']}")
    if "observed_sd_c" in therm:
        print(f"  observed SD {therm['observed_sd_c']} C vs null median "
              f"{therm['null_sd_median_c']} C (p={therm['p_null_tighter_or_equal']}, "
              f"{therm['null_draws']} draws)")
    print(f"  {therm['verdict']}")

    ratios = [r["median_ratio_trailing_7d"] for r in flow.values()
              if r["median_ratio_trailing_7d"] is not None]
    freshet_supported = bool(ratios) and min(ratios) > 1.2
    doc = {
        "built_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "question": ("Do the run triggers asserted in events_calendar.yaml and shipped as "
                     "river_flow.freshet appear in this bay's own record?"),
        "n_report_days": len(days), "n_years": len({d.year for d in days}),
        "flow_control": flow, "thermal_control": therm,
        "rain_trigger_supported": freshet_supported,
        "thermal_trigger_supported": therm.get("detected", False),
        "verdict": (
            "NEITHER asserted trigger is visible in the local record. Pink report days sit at or "
            "slightly BELOW the trailing week's flow, and onset temperature is no tighter than a "
            "shuffled year<->date pairing. The calendar, not the weather, is what this record "
            "supports."
            if not freshet_supported and not therm.get("detected", False) else
            "at least one asserted trigger is supported — see the per-control fields"),
        "confound": (
            "EFFORT BIAS, and it is one-sided. The ledger is mostly iNaturalist sightings, and "
            "observer effort drops on rainy, high-water days, so the FLOW null cannot separate "
            "'fish do not run on freshets' from 'nobody photographs fish in the rain'. It is "
            "enough to retire the assertion, not enough to assert the opposite. The THERMAL "
            "control is not affected: it compares pairings of the same dates, so effort cancels."),
        "consequence": ("river_flow.freshet stays as a described OBSERVATION of the hydrograph "
                        "(the river is rising) and must not be labelled a run trigger until "
                        "something measures it. Changing the shipped field's semantics beyond "
                        "that label needs an ADR (rule 11)."),
        "method": ("flow: daily-mean discharge on each deduped report day vs trailing-7d median "
                   "and season median at the same gauge. thermal: permutation test on the "
                   "year<->onset-date pairing of trailing-7d in-bay night minima, "
                   f"{PERM_DRAWS} draws, seed 1."),
        "sources": {"flow": "ECCC hydrometric-daily-mean archive",
                    "temperature": "ECCC climate-hourly, Welcome Island (in-bay)",
                    "reports": "knowledge/observations/observations.jsonl"},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"\nwrote {OUT.relative_to(ROOT)}")
    print(f"VERDICT: {doc['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
