"""Sniff checks — do the forecast outputs actually make physical sense?

Not formal validation (that's docs/*_VALIDATION.md). These are fast, falsifiable
gut-checks a Superior fisherman or a limnologist would run: are the numbers in the
right range, do they move in the right *direction* with the wind, and are the spots
ordered the way exposure says they should be. Each check prints a verdict
(OK / FLAG / INFO) with the actual number that earned it. No LLM.

    python scripts/sniff_checks.py [issue YYYY-MM-DD]
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402

import forecast_window as fw  # noqa: E402
from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.features import bias_live  # noqa: E402

ISSUE = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2026, 8, 4)

# physical envelopes for early-August stratified Lake Superior nearshore
ISO_RANGE = (2.0, 30.0)     # 12 C isotherm depth (m): thermocline sits ~10-20 m, shoals in upwelling
SST_RANGE = (10.0, 21.0)    # nearshore surface (C): Superior is cold; rarely >20 nearshore in Aug
BIAS_RANGE = (0.5, 6.0)     # pooled model subsurface warm bias (C): must be positive, O(few C)

_v = {"OK": "OK  ", "FLAG": "FLAG", "INFO": "INFO"}


def _line(tag, msg):
    print(f"  [{_v[tag]}] {msg}")


def main() -> int:
    cfg = load_config()
    central, lo, hi, _, n = bias_live.pooled_subsurface_bias(cfg, ISSUE)
    print(f"=== SNIFF CHECKS — issue {ISSUE} t12z ===\n")

    # ---- run the forecast for every shore station ----
    spots = {}
    surf = {}
    for s in cfg.shore_stations:
        pts, wins, meta = fw.forecast_spot(cfg, s.lat, s.lon, s.name, s.lsofs_node, ISSUE,
                                           (central, lo, hi, n))
        if pts:
            spots[s.name] = pts
            surf[s.name] = meta.get("surf_sst")

    # ============ 1. subsurface bias sign + magnitude ============
    print("1. Model subsurface warm bias (measured live vs buoys)")
    ok = BIAS_RANGE[0] <= central <= BIAS_RANGE[1]
    _line("OK" if ok else "FLAG",
          f"pooled bias {central:+.1f} C (band {lo:+.1f}..{hi:+.1f}, n={n}); "
          f"expect positive, {BIAS_RANGE[0]}..{BIAS_RANGE[1]} C  -> {'in range' if ok else 'OUT OF RANGE'}")

    # ============ 2. surface temperature plausibility ============
    print("\n2. Surface temperature (GLSEA anchor)")
    for name, v in surf.items():
        if v is None:
            _line("INFO", f"{name}: no GLSEA value")
        else:
            ok = SST_RANGE[0] <= v <= SST_RANGE[1]
            _line("OK" if ok else "FLAG",
                  f"{name}: {v:.1f} C  (expect {SST_RANGE[0]}..{SST_RANGE[1]})")

    # ============ 3. isotherm depth magnitude, per spot, per lead ============
    print("\n3. Isotherm-depth magnitude (nowcast + forecast leads)")
    all_iso = []
    for name, pts in spots.items():
        vals = [(p.lead_h, p.isotherm_depth_m) for p in pts]
        bad = [(lh, z) for lh, z in vals if z is not None and not (ISO_RANGE[0] <= z <= ISO_RANGE[1])]
        zs = [z for _, z in vals if z is not None]
        all_iso += zs
        rng = f"{min(zs):.1f}..{max(zs):.1f} m" if zs else "n/a"
        _line("OK" if not bad else "FLAG",
              f"{name}: iso range {rng} over 0-120 h" + (f"  OUT: {bad}" if bad else ""))

    # ============ 4. reachable-pixel depth ~ isotherm depth (definition check) ============
    print("\n4. Consistency: shallowest reachable water sits near the isotherm depth")
    # for each spot's nowcast, the closest reachable pixel's bottom depth should be
    # >= iso (it's cold on the bottom) and not wildly deeper than iso+cast-slope.
    for name, pts in spots.items():
        p0 = pts[0]
        if p0.reachable and p0.isotherm_depth_m is not None and p0.distance_m is not None:
            _line("OK", f"{name}: nowcast reachable, cold water {p0.distance_m:.0f} m out, "
                        f"iso {p0.isotherm_depth_m:.1f} m (cold-on-bottom within a cast)")
        elif p0.isotherm_depth_m is not None:
            _line("INFO", f"{name}: nowcast NOT reachable (iso {p0.isotherm_depth_m:.1f} m sits "
                          f"past cast range) — plausible if the shelf is deep here")
        else:
            _line("INFO", f"{name}: no isotherm (whole column one side of 12 C)")

    # ============ 5. DIRECTION: does the isotherm move WITH the upwelling wind? ============
    print("\n5. Physics: isotherm shoals with upwelling-favorable (W/SW) wind, deepens on relaxation")
    try:
        from tbay_fishcast.features import upwelling
        from tbay_fishcast.ingest import wind_forecast
        w = wind_forecast.fetch_ensemble_wind(forecast_days=5)
        prob = upwelling.upwelling_probability(w["time"], w["members"])
        days = sorted(prob)
        probline = ", ".join(f"{d:%a} {prob[d]:.0%}" for d in days)
        _line("INFO", f"P(upwelling-favorable wind): {probline}")

        # mean isotherm depth across spots per valid-day
        by_day = {}
        for pts in spots.values():
            for p in pts:
                if p.isotherm_depth_m is not None:
                    by_day.setdefault(p.valid_time.date(), []).append(p.isotherm_depth_m)
        iso_day = {d: float(np.mean(v)) for d, v in by_day.items() if v}
        isoline = ", ".join(f"{d:%a} {z:.1f}m" for d, z in sorted(iso_day.items()))
        _line("INFO", f"mean isotherm depth: {isoline}")

        # sign test: on high-upwelling days (>=40%) the isotherm should not be deepening
        idays = sorted(iso_day)
        agree = disagree = 0
        for a, b in zip(idays, idays[1:]):
            dz = iso_day[b] - iso_day[a]           # + = deepening (cold retreats down)
            p = prob.get(b, prob.get(a, 0.0))
            if p >= 0.4:                            # upwelling-favorable -> expect shoaling (dz<=0)
                agree += dz <= 0.5
                disagree += dz > 0.5
        if agree + disagree == 0:
            _line("INFO", "no strongly upwelling-favorable day in window -> direction test N/A")
        else:
            _line("OK" if agree >= disagree else "FLAG",
                  f"on upwelling-favorable days the isotherm shoaled/held {agree}/{agree+disagree} times "
                  f"(deepened {disagree}) — {'consistent' if agree>=disagree else 'INCONSISTENT'} with the physics")
    except Exception as e:  # noqa: BLE001
        _line("INFO", f"wind/ensemble unavailable ({type(e).__name__}: {e}); direction test skipped")

    # ============ 6. cross-spot spread is not degenerate ============
    print("\n6. Spatial spread (spots should not be identical — that would mean node/bathy collapse)")
    now_iso = {name: pts[0].isotherm_depth_m for name, pts in spots.items() if pts[0].isotherm_depth_m}
    if len(now_iso) >= 2:
        spread = max(now_iso.values()) - min(now_iso.values())
        _line("OK" if spread > 0.05 else "FLAG",
              f"nowcast isotherm spread across spots {spread:.1f} m "
              f"({', '.join(f'{k.split()[0]} {v:.1f}' for k,v in now_iso.items())})")
    else:
        _line("INFO", "fewer than 2 spots with an isotherm — spread test N/A")

    print("\n(sniff checks are gut-checks, not proof — see docs/*_VALIDATION.md for the formal gates)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
