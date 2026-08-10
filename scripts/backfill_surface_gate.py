"""Hindcast the model's SURFACE temperature AT THUNDER BAY against satellite (ADR-051).

THE GAP THIS CLOSES. Every validation this project has ever had was 180-270 km away. ADR-050
showed why that is not a technicality: the one mooring we could reach turned out to be a site
where LSOFS is 11-12 C wrong in midsummer while the rest of the modelled lake — including Thunder
Bay's own node — looks entirely normal. So the product's central field has never been scored where
the product actually operates.

It can be. LSOFS publishes station **10050 at 48.410,-89.215**, 2.3 km off the Thunder Bay
waterfront, inside its own station files; and GLSEA/ACSPO satellite SST has a multi-year archive
at any point. Truth, model and both cheap baselines are all available locally, for any season.

NOT CIRCULAR, and the framing is what makes it so. The product bias-corrects LSOFS using GLSEA, so
scoring the CORRECTED product against GLSEA would be scoring the model on the input it was forced
to match. This scores the RAW model against GLSEA, with the baselines being satellite persistence
and satellite climatology. The correction's value is then a separate, measurable question rather
than an assumption baked into the test.

TWO LIMITS, stated because they bound what any result here can mean:
  * SURFACE ONLY. GLSEA cannot see the isotherm DEPTH, which is the product's actual claim. A good
    result here is necessary, not sufficient; the subsurface still needs a local profile
    (the Bare Point intake, task #8).
  * GLSEA carries its own error of a few tenths of a degree. It can settle a 3 C model failure
    decisively and cannot resolve a 0.3 C skill difference.

Each row also carries the UPWELLING PHASE at its valid time, because the sharpest question is not
"is the model good on average" but "is it good WHEN THE MAP'S HEADLINE MECHANISM IS RUNNING" — a
model that fails during upwelling and relaxation fails exactly where the product stakes its claim.

    python scripts/backfill_surface_gate.py --start 2025-05-01 --end 2025-10-31
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LOG = ROOT / "data" / "surface_gate_log.csv"
FIELDS = ["valid_date", "site", "lead_h", "issue_date", "sat_c", "model_c",
          "persist_sat_c", "clim_sat_c", "phase", "wind_kn", "wind_dir",
          "model_station", "model_dist_km", "sat_dist_km", "retrieved_utc"]
LEADS_H = [0, 24, 48, 72, 96, 120]
TBAY = (48.43, -89.21)
CLIM_WINDOW_D = 7
# GLSEA for day D publishes on D+1, so the freshest satellite an operational forecast issued at
# 12Z on D could have used is D-1. Using D-1 makes persistence slightly WEAKER, which flatters the
# model — but it is what was actually available, and an honest baseline beats a flattering one in
# only one direction. Stated so the result is read with that in mind.
PERSIST_LAG_D = 1


def _existing(path: Path) -> set:
    if not path.exists():
        return set()
    with path.open() as f:
        return {(r["valid_date"], r["site"], r["lead_h"]) for r in csv.DictReader(f)}


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-05-01")
    ap.add_argument("--end", default="2025-10-31")
    ap.add_argument("--clim-years", default="2019,2020,2021,2022,2023,2024")
    ap.add_argument("--timeout", type=float, default=240.0)
    a = ap.parse_args(argv)

    from tbay_fishcast.features import upwelling_phase as up
    from tbay_fishcast.ingest import glsea, lsofs_stations as ls, wind_archive

    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    if start < ls.ARCHIVE_FIRST:
        print(f"NOTE: station-file archive begins {ls.ARCHIVE_FIRST}; clamping")
        start = ls.ARCHIVE_FIRST
    lat, lon = TBAY

    # RESOLVE THE SATELLITE PIXEL AT THE MODEL STATION, not at the waterfront. The first version
    # of this script pinned the pixel from the waterfront coordinate and compared it against
    # station 10050 nearly 3 km away — in a bay whose entire product is about thermal gradients,
    # and against a station sitting in 14.1 m of water that warms faster than the pixel it was
    # being scored against. That is a geometry artefact wearing a model bias's clothes, and it
    # inflated the apparent warm bias from ~1 C to ~2.7 C.
    tbl = ls.station_table(start)
    si, sname, sdist = ls.nearest(tbl, lat, lon)
    stn_lat, stn_lon = tbl[si][1], tbl[si][2]
    print(f"model station {sname} at {stn_lat:.4f},{stn_lon:.4f} "
          f"({sdist:.2f} km from the waterfront, h={tbl[si][3]:.1f} m)")
    pin = glsea.fetch_sst(stn_lat, stn_lon, start.isoformat())
    import math as _m
    _sep = _m.hypot((pin.pixel_lat - stn_lat) * 111.32,
                    (pin.pixel_lon - stn_lon) * 111.32 * _m.cos(_m.radians(stn_lat)))
    print(f"  satellite pixel is {_sep:.2f} km from the model station")
    print(f"satellite pixel: {pin.pixel_lat:.4f},{pin.pixel_lon:.4f}")
    sat = glsea.fetch_series(pin.pixel_lat, pin.pixel_lon,
                             (start - timedelta(days=PERSIST_LAG_D + 1)).isoformat(),
                             (end + timedelta(days=6)).isoformat())
    print(f"  {len(sat)} cloud-free satellite days in the window")

    print("climatology (other years only — a row is never scored against a baseline that saw it)")
    clim_acc = defaultdict(list)
    used_years = []
    for y in [int(x) for x in a.clim_years.split(",")]:
        if y == start.year:
            continue
        s, err = None, None
        for attempt in range(3):        # ERDDAP drops long griddap requests intermittently;
            try:                        # a silently missing YEAR would thin the climatology
                s = glsea.fetch_series(pin.pixel_lat, pin.pixel_lon,
                                       f"{y}-04-01", f"{y}-11-30")
                break
            except Exception as e:  # noqa: BLE001
                err = e
                time.sleep(2 ** attempt * 2)
        if s is None:
            print(f"  {y}: unavailable after 3 tries ({str(err)[:60]})")
            continue
        if not s:
            print(f"  {y}: no data")
            continue
        used_years.append(y)
        print(f"  {y}: {len(s)} days")
        for iso, v in s.items():
            doy = date.fromisoformat(iso).timetuple().tm_yday
            for d in range(doy - CLIM_WINDOW_D, doy + CLIM_WINDOW_D + 1):
                clim_acc[d].append(v)
    clim = {k: sum(v) / len(v) for k, v in clim_acc.items()}

    print("wind archive for the upwelling-phase stratification ...")
    wt, wdir, wkn = wind_archive.fetch_hourly_wind(start - timedelta(days=4),
                                                   end + timedelta(days=7), lat=lat, lon=lon)
    print(f"  {len(wt)} hourly wind records")

    done = _existing(LOG)
    rows, missing = [], 0
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    day = start
    while day <= end:
        issue = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
        wants = [issue + timedelta(hours=L) for L in LEADS_H]
        try:
            ex = ls.extract(day, wants, station_lat=stn_lat, station_lon=stn_lon,
                            timeout=int(a.timeout))
        except Exception:  # noqa: BLE001
            missing += 1
            day += timedelta(days=1)
            continue
        st = ex.get("station") or {}
        p_day = (day - timedelta(days=PERSIST_LAG_D)).isoformat()
        persist = sat.get(p_day)
        for L, vt in zip(LEADS_H, wants):
            vd = vt.date().isoformat()
            key = (vd, "tbay", str(L))
            if key in done:
                continue
            prof = ex["profiles"].get(vt.isoformat())
            truth = sat.get(vd)
            if not prof or truth is None:
                continue
            done.add(key)
            # top sigma layer = the model's surface. At station 10050 (h 14.1 m) that is ~0.4 m,
            # which is the right thing to compare with a satellite SST product.
            model_c = prof["temp_c"][0]
            hist = [(t, d_, k) for t, d_, k in zip(wt, wdir, wkn) if t <= vt]
            ph = up.classify([h[0] for h in hist[-240:]], [h[1] for h in hist[-240:]],
                             [h[2] for h in hist[-240:]], vt) if len(hist) > 24 else None
            near = hist[-1] if hist else None
            rows.append({
                "valid_date": vd, "site": "tbay", "lead_h": str(L),
                "issue_date": day.isoformat(), "sat_c": f"{truth:.3f}",
                "model_c": f"{model_c:.3f}",
                "persist_sat_c": (f"{persist:.3f}" if persist is not None else ""),
                "clim_sat_c": (f"{clim[vt.timetuple().tm_yday]:.3f}"
                               if vt.timetuple().tm_yday in clim else ""),
                "phase": (ph.phase if ph else ""),
                "wind_kn": (f"{near[2]:.1f}" if near else ""),
                "wind_dir": (f"{near[1]:.0f}" if near else ""),
                "model_station": str(st.get("name", "")),
                "model_dist_km": str(st.get("dist_km", "")),
                "sat_dist_km": f"{_sep:.2f}", "retrieved_utc": now})
        if day.day == 1:
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
    print(f"  station files missing: {missing} | climatology years: {used_years}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
