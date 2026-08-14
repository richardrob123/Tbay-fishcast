"""Hindcast the thermal forecast gate over a whole season (ADR-049).

Replaces waiting. The live gate accrues at most one usable profile a day and, for most of 2026,
none at all — the answer to "does this forecast beat the cheap alternative?" was months away and
resting on a censored reference. Both archives needed to answer it NOW exist:

  * NOAA publishes `lsofs.tHHz.YYYYMMDD.stations.forecast.nc` — 10 MB, one file per cycle,
    carrying the ENTIRE f000-f120 window at 6-minute resolution at 19 fixed stations, one of
    which (45027) sits 0.11 km from the Duluth LLO1 thermistor chain. Archive from 2024-12.
  * GLOS publishes the chain's ARCHIVE dataset (`obs_42`) in wide form with per-sensor depths
    and QARTOD flags, back years — as opposed to the 3-day rolling window the product reads.

One file per issue day yields five leads x ~10 sensor depths of PAIRED samples.

    python scripts/backfill_thermal_gate.py --start 2025-05-15 --end 2025-09-16

Writes data/thermal_gate_log.csv (append-only, idempotent by key).

WHAT EACH ROW IS. One (issue day, lead, sensor depth): the observed temperature, the model's
temperature interpolated to that same depth, and the two cheap alternatives a forecast has to
beat — OBSERVED PERSISTENCE (the last observation available at issue time, held) and CLIMATOLOGY
(other years' mean at that depth and day-of-year). Note "observed" persistence: the previous gate
used the model's own nowcast held forward, which makes the comparison model-versus-model rather
than forecast-versus-the-cheap-thing-a-person-would-do.

TEMPORAL SPLIT (rule 6). 2025 is on the VALIDATION side, so nothing tuned on 2022-2024 is
contaminated. Climatology is built from OTHER years only, so a row is never scored against a
baseline that saw it.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LOG = ROOT / "data" / "thermal_gate_log.csv"
FIELDS = ["valid_utc", "chain", "lead_h", "issue_date", "depth_m",
          "obs_c", "fcst_c", "persist_obs_c", "clim_c", "qc", "model_station",
          "model_dist_km",
          # ADR-054: the INDEPENDENT third party, carried in the row so the analyzer can run the
          # site- and reference-validity guards offline. Without it those guards were built and
          # then left out of the path they exist to protect — documentation, not a guard.
          "sat_c",
          "retrieved_utc"]


def _migrate_log(path: Path) -> None:
    """Bring an old-schema log up to the current FIELDS; old rows keep a blank sat_c.

    Blank means "no third party recorded for this row", which the analyzer treats as unable to
    judge rather than as agreement — the same discipline as the ADR-048 obs_status migration."""
    if not path.exists():
        return
    with path.open() as f:
        rows = list(csv.DictReader(f))
    if not rows or all(k in rows[0] for k in FIELDS):
        return
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    print(f"migrated {path.name} to the satellite-cross-check schema ({len(rows)} rows kept)")
# Lead 0 is the NOWCAST at issue time, and it is in this file for free. It is the decisive
# diagnostic: if the error at lead 0 matches the error at lead 120, the problem is the model's
# STATE at this location, not its ability to forecast — and no amount of lead-tuning would help.
LEADS_H = [0, 24, 48, 72, 96, 120]
CHAIN_POS = {"llo1": (46.86, -91.93)}
CLIM_WINDOW_D = 7          # +/- days pooled around the day-of-year for the climatology
CLIM_MIN_YEARS = 2         # below this a climatology is not a reference, it is an anecdote


def _load_existing(path: Path) -> set:
    if not path.exists():
        return set()
    with path.open() as f:
        return {(r["valid_utc"], r["chain"], r["lead_h"], r["depth_m"])
                for r in csv.DictReader(f)}


def _climatology(chain: str, years, exclude_year: int, timeout: float):
    """{(doy, depth): mean_c} from OTHER years — never the year being scored."""
    from tbay_fishcast.ingest import glos_archive as ga

    acc = defaultdict(list)
    used = set()
    for y in years:
        if y == exclude_year:
            continue
        try:
            s, st = ga.fetch_chain(chain, date(y, 4, 1), date(y, 12, 1), timeout=timeout)
        except Exception as e:  # noqa: BLE001
            print(f"  climatology {y}: unavailable ({str(e)[:60]})")
            continue
        if not s:
            print(f"  climatology {y}: no rows")
            continue
        used.add(y)
        print(f"  climatology {y}: {st['kept']} samples, depths {st['depths_seen']}")
        for smp in s:
            if smp.time.hour == 12 and smp.time.minute < 20:
                acc[(smp.time.timetuple().tm_yday, round(smp.depth_m, 1))].append(smp.temp_c)
    out = {}
    for (doy, z), v in acc.items():
        for d in range(doy - CLIM_WINDOW_D, doy + CLIM_WINDOW_D + 1):
            out.setdefault((d, z), []).extend(v)
    return {k: sum(v) / len(v) for k, v in out.items()}, sorted(used)


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-05-15")
    ap.add_argument("--end", default="2025-09-16")
    ap.add_argument("--chain", default="llo1")
    ap.add_argument("--clim-years", default="2019,2021,2022,2023,2024")
    ap.add_argument("--timeout", type=float, default=240.0)
    a = ap.parse_args(argv)

    from tbay_fishcast.ingest import glos_archive as ga
    from tbay_fishcast.ingest import glsea
    from tbay_fishcast.ingest import lsofs_stations as ls

    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    if start < ls.ARCHIVE_FIRST:
        print(f"NOTE: station-file archive begins {ls.ARCHIVE_FIRST}; clamping start")
        start = ls.ARCHIVE_FIRST
    lat, lon = CHAIN_POS[a.chain]

    print(f"observations: {a.chain} {start} .. {end + timedelta(days=6)}")
    obs, ostats = ga.fetch_chain(a.chain, start, end + timedelta(days=6), timeout=a.timeout)
    print(f"  {ostats['kept']} samples, sensors {ostats['sensors_seen']}, "
          f"depths {ostats['depths_seen']}")
    print(f"  QARTOD flags {ostats['flags']}, rejected {ostats['rejected_qc']}")
    if not obs:
        print("ABORT: no observations")
        return 2
    obs_by_time = defaultdict(dict)
    for s in obs:
        obs_by_time[s.time][round(s.depth_m, 2)] = (s.temp_c, s.qc)

    print("climatology (other years only — a row is never scored against a baseline that saw it)")
    clim, clim_years = _climatology(a.chain, [int(y) for y in a.clim_years.split(",")],
                                    start.year, a.timeout)
    if len(clim_years) < CLIM_MIN_YEARS:
        print(f"  only {len(clim_years)} usable year(s) {clim_years} — climatology will be "
              f"recorded where present but is too thin to be a reference on its own")

    # INDEPENDENT THIRD PARTY at the chain, for the ADR-052/053 guards. One series call; a
    # failure costs the guards, not the gate, so it degrades to blank rather than aborting.
    sat = {}
    try:
        pin = glsea.fetch_sst(lat, lon, start.isoformat())
        # CLAMP TO COVERAGE. Asking past the dataset's last day returns a 404 for the WHOLE
        # range, so a request running a few days into the future silently cost every guard its
        # evidence — a validation check disabled by an off-by-a-week.
        last = min((end + timedelta(days=6)).isoformat(), glsea.coverage_end())
        sat = glsea.fetch_series(pin.pixel_lat, pin.pixel_lon, start.isoformat(), last)
        print(f"  satellite cross-check: {len(sat)} cloud-free days "
              f"({pin.dist_km:.2f} km from the chain)")
    except Exception as e:  # noqa: BLE001
        print(f"    WARN: satellite cross-check unavailable ({str(e)[:60]}) — the site and "
              f"reference guards will have nothing to judge with")

    _migrate_log(LOG)
    # SELF-HEALING, not just forward-filling. The guards need sat_c on the HISTORIC rows too, and
    # a column that only ever populates for new rows would leave the checks unable to judge for
    # months — which is indistinguishable from the checks not being wired at all, the exact
    # failure this is fixing.
    if sat and LOG.exists():
        with LOG.open() as f:
            old = list(csv.DictReader(f))
        filled = 0
        for r in old:
            if not (r.get("sat_c") or "").strip():
                d = (r.get("valid_utc") or "")[:10]
                if d in sat:
                    r["sat_c"] = f"{sat[d]:.3f}"
                    filled += 1
        if filled:
            with LOG.open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS)
                w.writeheader()
                w.writerows([{k: r.get(k, "") for k in FIELDS} for r in old])
            print(f"  filled sat_c on {filled} existing rows")

    done = _load_existing(LOG)
    rows, missing_files, skipped = [], 0, 0
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    day = start
    while day <= end:
        issue = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
        wants = [issue + timedelta(hours=L) for L in LEADS_H]
        try:
            ex = ls.extract(day, wants, station_lat=lat, station_lon=lon, timeout=int(a.timeout))
        except Exception as e:  # noqa: BLE001
            missing_files += 1
            print(f"  {day}: no station file ({str(e)[:50]})")
            day += timedelta(days=1)
            continue
        st = ex.get("station") or {}
        # PERSISTENCE = the last OBSERVATION available at issue time, held. Not the model's own
        # nowcast: that would score the model against itself and is not the cheap thing a person
        # would do.
        p_time = min(obs_by_time, key=lambda t: abs((t - issue).total_seconds()),
                     default=None) if obs_by_time else None
        persist = {}
        if p_time is not None and abs((p_time - issue).total_seconds()) <= 3 * 3600:
            persist = {z: v[0] for z, v in obs_by_time[p_time].items()}
        for L, wt in zip(LEADS_H, wants):
            prof = ex["profiles"].get(wt.isoformat())
            if not prof:
                skipped += 1
                continue
            o_time = min(obs_by_time, key=lambda t: abs((t - wt).total_seconds()),
                         default=None)
            if o_time is None or abs((o_time - wt).total_seconds()) > 45 * 60:
                skipped += 1
                continue
            doy = wt.timetuple().tm_yday
            for z, (tc, qc) in sorted(obs_by_time[o_time].items()):
                fc = ls.interp_to(z, prof)
                if fc is None:                 # above the top sigma layer / below the bottom
                    continue
                key = (wt.isoformat(), a.chain, str(L), f"{z:.2f}")
                if key in done:
                    continue
                done.add(key)
                rows.append({
                    "valid_utc": wt.isoformat(), "chain": a.chain, "lead_h": str(L),
                    "issue_date": day.isoformat(), "depth_m": f"{z:.2f}",
                    "obs_c": f"{tc:.3f}", "fcst_c": f"{fc:.3f}",
                    "persist_obs_c": (f"{persist[z]:.3f}" if z in persist else ""),
                    "clim_c": (f"{clim[(doy, round(z, 1))]:.3f}"
                               if (doy, round(z, 1)) in clim else ""),
                    "qc": str(qc), "model_station": str(st.get("name", "")),
                    "model_dist_km": str(st.get("dist_km", "")),
                    "sat_c": (f"{sat[wt.date().isoformat()]:.3f}"
                              if wt.date().isoformat() in sat else ""),
                    "retrieved_utc": now})
        if day.day == 1 or day == start:
            print(f"  {day}: {len(rows)} rows so far")
        day += timedelta(days=1)

    if not rows:
        print("no new rows")
        return 0
    new = not LOG.exists()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {len(rows)} rows -> {LOG.relative_to(ROOT)}")
    print(f"  station files missing: {missing_files} | lead/obs pairs skipped: {skipped}")
    print(f"  climatology years used: {clim_years}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
