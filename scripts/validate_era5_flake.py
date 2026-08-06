"""ERA5 FLake cross-check gate — is the independent lake model trustworthy here?

DATA_AUDIT.md named the core limit: the subsurface rests on ONE model (LSOFS/FVCOM) with
no independent cross-check. ERA5's FLake (a 1-D lake scheme, ECMWF-forced, NOT FVCOM) is
that second model. Before trusting it, adjudicate it against the NDBC buoys the same way
we killed MUR SST: a buoy that sits INSIDE FLake's mixed layer should read FLake's
mixed-layer temperature; a buoy BELOW it should read colder (FLake should also then show
a shallow mixed layer + cold bottom).

    python scripts/validate_era5_flake.py [YYYY-MM-DD]   # default: 6 days ago (ERA5T lag)

Needs a CDS key (~/.cdsapirc). No LLM.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone

from tbay_fishcast.ingest import era5_flake, ndbc

# buoy: (id, sensor depth m). Same subsurface set the bias model uses.
BUOYS = [("45027", 6.0), ("45023", 5.0), ("45216", 3.0)]


def _buoy_wtmp(bid: str, day: date) -> float | None:
    """Buoy water temp (WTMP) nearest 12Z on `day` from the NDBC realtime2 archive."""
    import urllib.request
    best = None
    try:
        txt = urllib.request.urlopen(f"https://www.ndbc.noaa.gov/data/realtime2/{bid}.txt",
                                     timeout=20).read().decode()
    except Exception:  # noqa: BLE001
        return None
    for ln in txt.splitlines():
        if ln.startswith("#"):
            continue
        f = ln.split()
        if len(f) < 15:
            continue
        try:
            y, mo, d, hh = int(f[0]), int(f[1]), int(f[2]), int(f[3])
            if date(y, mo, d) == day and 10 <= hh <= 14 and f[14] not in ("MM", "999.0"):
                cand = (abs(hh - 12), float(f[14]))
                best = cand if best is None or cand < best else best
        except ValueError:
            continue
    return best[1] if best else None


def main(argv) -> int:
    day = date.fromisoformat(argv[1]) if len(argv) > 1 else \
        (datetime.now(timezone.utc).date() - timedelta(days=6))
    print(f"ERA5 FLake vs NDBC buoys — target {day} (ERA5T lag ~5 d)\n")
    print(f"{'buoy':6}{'z':>4}  {'buoy':>6}  {'FLake_mixT':>10}{'MLD':>7}{'botT':>7}  verdict")
    inlayer_err = []
    for bid, z in BUOYS:
        b = ndbc.BUOYS[bid]
        cell = era5_flake.fetch_flake(b.lat, b.lon, day)
        wt = _buoy_wtmp(bid, day if cell is None else date.fromisoformat(cell.day))
        if cell is None or wt is None:
            print(f"{bid:6}{z:4.0f}  {'--' if wt is None else f'{wt:6.1f}'}  "
                  f"{'(FLake/buoy unavailable)':>30}")
            continue
        in_layer = z <= cell.mixed_layer_depth_m
        if in_layer:
            e = abs(cell.mixed_layer_temp_c - wt)
            inlayer_err.append(e)
            verdict = f"in-layer  |ΔmixT|={e:.1f}"
        else:
            verdict = (f"below layer (MLD {cell.mixed_layer_depth_m:.1f}m) -> "
                       f"buoy{'' if wt < cell.mixed_layer_temp_c else ' NOT'} colder; "
                       f"botT {cell.bottom_temp_c:.1f}")
        print(f"{bid:6}{z:4.0f}  {wt:6.1f}  {cell.mixed_layer_temp_c:10.1f}"
              f"{cell.mixed_layer_depth_m:7.1f}{cell.bottom_temp_c:7.1f}  {verdict}")
    if inlayer_err:
        mae = sum(inlayer_err) / len(inlayer_err)
        print(f"\nin-mixed-layer FLake-vs-buoy MAE: {mae:.2f} C "
              f"({'TRUST' if mae < 2.5 else 'SUSPECT'}; MUR was ~4 C -> rejected)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
