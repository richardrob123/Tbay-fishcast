"""Does the wind forecast still know about upwelling at +120 h? (ADR-055)

THE ASSUMPTION THIS TESTS. The map publishes upwelling out to +120 h. Every hour of that rests
on a wind forecast crossing the Wedderburn bar. The existing wind gate scores that forecast — but
aggregated over all leads at once (`issue,buoy,n_hours,mae_kn,...`, no lead column), against
buoys 130-200 km away. So the product's forecast HORIZON has been assumed since day one. If the
+120 h wind forecast cannot call a sustained west-quadrant blow, rule 8 says the long-lead
upwelling layer gets benched — which is a change to what ships, not a footnote.

WHAT MAKES THE TEST HONEST:
  * TRUTH is a real anemometer INSIDE the bay — Welcome Island, 4 km from the LSOFS node the
    product forecasts at. Not a reanalysis, not an analysis, not a buoy 200 km away. ADR-053
    happened because a smoothed reference was mistaken for truth; the analyzer re-runs that guard
    on this one rather than trusting the argument.
  * THE SCORED QUANTITY is the upwelling DRIVE (trailing 24 h favorable wind-run integral), from
    the product's own `wind.favorable_wind_run`, not raw speed error. A forecast can be 3 kt off
    and call the mechanism perfectly, or 1 kt off and invert it.
  * BASELINES are the same harder-of-two discipline used everywhere else: observed persistence
    (the drive last seen at issue time) and other-year climatology at the same day-of-year. Both
    built from the same in-bay anemometer.
  * NOTHING IS FITTED HERE, so there is no train/test split to make (rule 6 governs tuning, and
    this tunes nothing). If a lead-dependent confidence is ever fitted FROM this record, that
    fit needs the split.

    python scripts/backfill_wind_lead_gate.py --past-days 120 --clim-years 8
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LOG = ROOT / "data" / "wind_lead_gate_log.csv"
FIELDS = ["valid_utc", "site", "lead_h", "obs_kn", "obs_dir", "obs_fav_kn", "obs_drive_kth",
          "fcst_kn", "fcst_dir", "fcst_fav_kn", "fcst_drive_kth",
          "persist_drive_kth", "clim_drive_kth", "obs_event", "fcst_event",
          "obs_station", "obs_dist_km", "clim_years", "retrieved_utc"]
# The climatology baseline is rebuilt from whatever years resolve on the day of the run, and a
# rate-limited year is merely printed and skipped. Without recording WHICH years produced a row's
# clim_drive_kth, a log accumulated across runs silently mixes vintages that the analyzer then
# pools into one clim_mae_kth — the number the ADR-006 demotion decision for the +120 h upwelling
# layer rests on. The vintage is now part of every row.
LEGACY_CLIM_YEARS = "2018-2025"     # what every row written before this column was built from
CLIM_WINDOW_D = 7          # same +/- 7 day window the surface gate uses for its climatology
SEASON_START, SEASON_END = (4, 1), (11, 30)     # the open-water season the product runs in


def _migrate_log(path: Path) -> None:
    """Add `clim_years` to a log written before the column existed, stamped with the vintage
    those rows were actually built from. Rewriting in place beats leaving a blank, which would be
    indistinguishable from a run that failed to resolve any climatology year."""
    if not path.exists():
        return
    with path.open() as f:
        rdr = csv.DictReader(f)
        if rdr.fieldnames and "clim_years" in rdr.fieldnames:
            return
        rows = list(rdr)
    for r in rows:
        r["clim_years"] = LEGACY_CLIM_YEARS
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows({c: r.get(c, "") for c in FIELDS} for r in rows)
    print(f"migrated {len(rows)} rows -> clim_years={LEGACY_CLIM_YEARS}")


def _existing(path: Path) -> set:
    if not path.exists():
        return set()
    with path.open() as f:
        return {(r["valid_utc"], r["site"], r["lead_h"]) for r in csv.DictReader(f)}


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--past-days", type=int, default=120)
    ap.add_argument("--season-years", default="",
                    help="comma-separated prior years to backfill whole open-water seasons for, "
                         "e.g. 2024,2025. Uses the dated archive rather than the rolling window; "
                         "the two were verified identical to 0.0000 kn where they overlap.")
    ap.add_argument("--clim-years", type=int, default=8,
                    help="years of in-bay obs used to build the day-of-year climatology")
    ap.add_argument("--timeout", type=float, default=300.0)
    a = ap.parse_args(argv)

    from tbay_fishcast.features import upwelling_drive as ud
    from tbay_fishcast.ingest import eccc_wind, openmeteo_prev_runs as pr

    st = eccc_wind.STATIONS["welcome_island"]
    today = datetime.now(timezone.utc).date()
    years = [int(y) for y in a.season_years.split(",") if y.strip()]
    # WHOLE PRIOR SEASONS, when asked for. The rolling window held 3 storms in 121 days, which is
    # why the event verdict is withheld; prior seasons are the only way to accumulate enough
    # distinct blows to ever issue it without waiting years.
    if years:
        start = date(min(years), *SEASON_START)
    else:
        start = today - timedelta(days=a.past_days + 2)   # +2 so the first drive window is full
    print(f"truth: {st.name} (ECCC {st.stn_id}) at {st.lat:.4f},{st.lon:.4f}, hourly since "
          f"{st.hourly_from}")

    obs = eccc_wind.fetch_hourly(start, today, timeout=a.timeout)
    if not obs:
        print("no observed wind — nothing to score against")
        return 1
    print(f"  {len(obs)} clean hourly observations {obs[0].time.date()} .. {obs[-1].time.date()}")
    obs_drive = ud.drive_series([w.time for w in obs], [w.speed_kn for w in obs],
                                [w.dir_deg for w in obs])
    obs_at = {w.time: w for w in obs}

    # CLIMATOLOGY from the same instrument, OTHER YEARS ONLY — a row is never scored against a
    # baseline that saw it. Fetched year by year so one bad year is a gap, not a total failure.
    print(f"climatology: {a.clim_years} prior years at the same station (other years only)")
    clim_acc: dict[int, list] = {}
    used = []
    for y in range(today.year - a.clim_years, today.year):
        if y < int(st.hourly_from[:4]):
            continue
        try:
            yr = eccc_wind.fetch_hourly(date(y, 4, 1), date(y, 11, 30), timeout=a.timeout)
        except Exception as e:  # noqa: BLE001
            print(f"  {y}: unavailable ({str(e)[:60]})")
            continue
        if not yr:
            print(f"  {y}: no data")
            continue
        used.append(y)
        yd = ud.drive_series([w.time for w in yr], [w.speed_kn for w in yr],
                             [w.dir_deg for w in yr])
        print(f"  {y}: {len(yr)} obs -> {len(yd)} drive hours")
        for t, v in yd.items():
            doy = t.timetuple().tm_yday
            for d in range(doy - CLIM_WINDOW_D, doy + CLIM_WINDOW_D + 1):
                clim_acc.setdefault(d, []).append(v)
    clim = {k: sum(v) / len(v) for k, v in clim_acc.items() if v}

    print(f"forecasts: {pr.MODEL}, previous_day1..7 at the station point")
    fc: dict = {}
    for y in years:
        s_, e_ = date(y, *SEASON_START), date(y, *SEASON_END)
        got = pr.fetch_lead_wind_range(st.lat, st.lon, s_, min(e_, today), timeout=a.timeout)
        n = sum(len(v) for v in got.values())
        # COUNT WHAT CAME BACK, never trust the range requested: the archive returns the FIELDS
        # for years it has no runs for, populated entirely with nulls.
        print(f"  {y} season: {n} lead-hours across leads {sorted(got)}"
              + ("" if n else "  <-- EMPTY, before the archive begins"))
        for k, v in got.items():
            fc.setdefault(k, []).extend(v)
    rolling = pr.fetch_lead_wind(st.lat, st.lon, past_days=a.past_days, timeout=a.timeout)
    print(f"  rolling {a.past_days} d: leads {sorted(rolling)}")
    for k, v in rolling.items():
        fc.setdefault(k, []).extend(v)
    # De-duplicate the seam: the dated archive and the rolling window overlap by design, and they
    # are the same bytes there (verified to 0.0000 kn), so last-write-wins is safe.
    for k in fc:
        fc[k] = sorted({t: (t, spd, drc) for t, spd, drc in fc[k]}.values())

    _migrate_log(LOG)
    done = _existing(LOG)
    rows = []
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for n, series in sorted(fc.items()):
        lead_h = n * 24
        fdrive = ud.drive_series([t for t, _s, _d in series], [s for _t, s, _d in series],
                                 [d for _t, _s, d in series])
        fat = {t: (s, d) for t, s, d in series}
        kept = 0
        for vt, fd in sorted(fdrive.items()):
            key = (vt.isoformat(), "welcome_island", str(lead_h))
            if key in done:
                continue
            od = obs_drive.get(vt)
            w = obs_at.get(vt)
            if od is None or w is None:
                continue
            # PERSISTENCE = the drive last OBSERVABLE when this forecast was issued, i.e. one
            # lead earlier. Not the drive at valid time (which is the answer) and not "now"
            # (which a forecaster at issue time did not have).
            persist = obs_drive.get(vt - timedelta(hours=lead_h))
            fs, fdir = fat[vt]
            done.add(key)
            kept += 1
            rows.append({
                "valid_utc": vt.isoformat(), "site": "welcome_island", "lead_h": str(lead_h),
                "obs_kn": f"{w.speed_kn:.2f}",
                "obs_dir": ("" if w.dir_deg is None else f"{w.dir_deg:.0f}"),
                "obs_fav_kn": f"{ud.favorable_component(w.speed_kn, w.dir_deg):.3f}",
                "obs_drive_kth": f"{od:.2f}",
                "fcst_kn": f"{fs:.2f}", "fcst_dir": f"{fdir:.0f}",
                "fcst_fav_kn": f"{ud.favorable_component(fs, fdir):.3f}",
                "fcst_drive_kth": f"{fd:.2f}",
                "persist_drive_kth": ("" if persist is None else f"{persist:.2f}"),
                "clim_drive_kth": (f"{clim[vt.timetuple().tm_yday]:.2f}"
                                   if vt.timetuple().tm_yday in clim else ""),
                "obs_event": str(int(ud.is_event(od))),
                "fcst_event": str(int(ud.is_event(fd))),
                "obs_station": st.name, "obs_dist_km": "0.0",
                "clim_years": "-".join([str(min(used)), str(max(used))]) if used else "",
                "retrieved_utc": now})
        print(f"  lead {lead_h:4d} h: {kept} new paired hours")

    if not rows:
        print("no new rows")
        return 0
    new = not LOG.exists()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", newline="") as f:
        w_ = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w_.writeheader()
        w_.writerows(rows)
    print(f"\nwrote {len(rows)} rows -> {LOG.relative_to(ROOT)}")
    print(f"  climatology years used: {used}")
    print(f"  event bar: drive >= {ud.EVENT_DRIVE_KTH:.0f} kt*h "
          f"(= {ud.OBSERVED_THRESHOLD_KN:.0f} kt sustained over {ud.WINDOW_H:.0f} h)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
