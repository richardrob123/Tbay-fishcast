"""Reachability FORECAST — when does a spot's cold-water window open/close (0–5 days)?

Pulls the LSOFS forecast (t12z nowcast + f024…f120, one step/day by default),
runs each lead through the same cross-shore logic as the nowcast map, and prints the
isotherm-depth trajectory + the reachable WINDOWS. This is the "here's your window
this week" upgrade over the nowcast-only brief.

    python scripts/forecast_window.py <station_id> [issue YYYY-MM-DD]
    python scripts/forecast_window.py 48.5085 -88.9746 "Silver Harbour pt" [issue]

Correction: subsurface warm bias pooled from live buoys; surface anchored to the
issue-day satellite SST (future satellite is unknown, so the surface anchor is held
constant across lead times — an honest forecast assumption). Bathymetry is
time-invariant, fetched/loaded once. No LLM.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.features import bias_live  # noqa: E402
from tbay_fishcast.features import thermocline  # noqa: E402
from tbay_fishcast.features.forecast import ForecastPoint, reachable_windows, summarize  # noqa: E402
from tbay_fishcast.features.reachability import corrected_fields, reachability  # noqa: E402
from tbay_fishcast.ingest import glsea, nonna  # noqa: E402
from tbay_fishcast.ingest.backfill import _open_first  # noqa: E402
from tbay_fishcast.ingest.lsofs_extract import extract_native_columns, valid_time_from_dataset  # noqa: E402
from tbay_fishcast.ingest.lsofs_paths import LsofsFile, candidate_urls  # noqa: E402
from tbay_fishcast.ingest import lsofs_grid  # noqa: E402

LEADS = [("n", 6, 0)] + [("f", h, h) for h in (24, 48, 72, 96, 120)]  # (kind, file_hour, lead_h)


def resolve_issue(cfg, issue, max_back: int = 3):
    """Most recent day (walking back from `issue`) whose t12z nowcast actually exists.
    Shared by the heartbeat so early-UTC runs read yesterday's cycle instead of
    reading nothing and mistaking missing data for closed windows (AUDIT_ROUND3)."""
    from datetime import timedelta
    for k in range(max_back + 1):
        d = issue - timedelta(days=k)
        try:
            ds = _open_first(candidate_urls(LsofsFile(d, "t12z", "n", 6),
                                            cfg.lsofs.recent_bucket, cfg.lsofs.archive_bucket,
                                            byterange=False))
            ds.close()
            return d
        except Exception:  # noqa: BLE001
            continue
    return None


def _bathy_grid(lat, lon):
    """Fetch the NONNA patch once; return (depth, shore-distance, res_m, note).

    Uses the SAME corrected land/water pipeline as the coast map (imagery shore mask +
    gap bridging) so pin verdicts and map polygons cannot diverge. Degrades to the
    naive NaN=shore field only if imagery is unavailable — and says so in the note."""
    patch = nonna.fetch_patch(lat, lon, half_m=1200, scale_px=300)
    if patch.coverage_frac < 0.05:
        return None, None, None, "no NONNA nearshore coverage here"
    res = nonna.ground_res_m(patch.res_mercator_m, lat)
    depth, dist, degraded = corrected_fields(patch.depth, patch.bounds_3857, res)
    note = f"CHS NONNA-10 2-D patch ({res:.0f} m, water {patch.coverage_frac:.0%})"
    if degraded:
        note += " ⚠ imagery shore-mask unavailable — naive shore (verdicts less reliable)"
    return depth, dist, res, note


def forecast_spot(cfg, lat, lon, name, node, issue, bias_stats, *, exposure=None, cycle="t12z"):
    """Compute the reachability trajectory + windows for one spot. Reusable by the
    heartbeat. Returns (points, windows, meta) or (None, None, meta) if no bathymetry.
    `bias_stats` = (central, lo, hi, n) pooled subsurface bias (computed once by caller)."""
    central, lo, hi, n = bias_stats
    prod = cfg.product
    depth_grid, dist_field, res_m, bsrc = _bathy_grid(lat, lon)
    if depth_grid is None:
        return None, None, {"bathy": bsrc}
    # GLSEA lags ~1 day; fall back to the most recent available day rather than
    # dropping the surface anchor (without it the corrected isotherm collapses to
    # the surface — every spot reads reachable). Slowly-varying SST makes a recent
    # prior day a sound anchor.
    _px = glsea.fetch_recent_sst(lat, lon, issue)
    # Apply the SAME measured nearshore warm-delta the map uses (validation finding #1): GLSEA's
    # ~1 km pixel reads the nearshore too cold, so the pins must warm the anchor identically or
    # the two published views disagree on the same water. Shared helper = single source of truth.
    from tbay_fishcast.features.nearshore import delta_for_exposure
    _ns_delta, _ns_n = delta_for_exposure(exposure)   # class-aware, SIGNED; None -> regional median
    surf_sst = (_px.sst_c + (_ns_delta if _ns_n else 0.0)) if _px is not None else None
    points = []
    grid = None
    for kind, fh, lead in LEADS:
        f = LsofsFile(issue, cycle, kind, fh)
        try:
            ds = _open_first(candidate_urls(f, cfg.lsofs.recent_bucket, cfg.lsofs.archive_bucket,
                                            byterange=False))
        except Exception:  # noqa: BLE001
            continue
        if node is None and grid is None:
            grid = lsofs_grid.read_grid(ds)
        this_node = node if node is not None else lsofs_grid.nearest_node(grid, lat, lon, min_depth_m=6.0).node
        vt = valid_time_from_dataset(ds)
        # isotherm from the native sigma layers (no depth-binning loss near a sharp
        # thermocline — measured up to ~0.7 m tighter than the fixed-depth interp)
        col = extract_native_columns(ds, {name: this_node})[name]
        ds.close()
        depths = col.depths_m
        raw = col.temps_c
        surf_bias = (raw[0] - surf_sst) if surf_sst is not None else 0.0
        bm = thermocline.BiasModel(surf_bias, central, lo, hi, n_buoys=n)
        band = thermocline.isotherm_band(depths, raw, bm, prod.target_c)
        iso = band["central"]
        # honest band -> reachability bookends. A larger bias correction cools the
        # column, so the "shallow" member is the optimistic end for reachability and
        # the "deep" member the conservative one.
        args = (depth_grid, dist_field)
        kw = {"max_depth_m": prod.max_reach_depth_m}
        reach, closest, area = reachability(*args, iso, prod.cast_m, res_m, **kw)
        certain = reachability(*args, band["deep"], prod.cast_m, res_m, **kw)[0]
        possible = reachability(*args, band["shallow"], prod.cast_m, res_m, **kw)[0]
        points.append(ForecastPoint(vt, lead, iso, closest, reach,
                                    reachable_certain=certain, reachable_possible=possible))
    return points, reachable_windows(points), {"bathy": bsrc, "surf_sst": surf_sst}


def main(argv) -> int:
    cfg = load_config()
    if argv[1:] and not _isfloat(argv[1]):
        station = next((s for s in cfg.stations if s.id == argv[1]), None)
        if station is None:
            print(f"unknown station '{argv[1]}'"); return 1
        lat, lon, name, node = station.lat, station.lon, station.name, station.lsofs_node
        rest = argv[2:]
    else:
        lat, lon, name = float(argv[1]), float(argv[2]), (argv[3] if len(argv) > 3 else "pin")
        node, rest = None, argv[4:]
    if rest and _isdate(rest[0]):
        issue = date.fromisoformat(rest[0])
    else:
        from datetime import datetime, timezone
        issue = resolve_issue(cfg, datetime.now(timezone.utc).date())
        if issue is None:
            print("no LSOFS t12z cycle found in the last 3 days"); return 1

    central, lo, hi, _, n, bias_src = bias_live.pooled_or_prior(cfg, issue)
    points, windows, meta = forecast_spot(cfg, lat, lon, name, node, issue, (central, lo, hi, n))
    print(f"=== REACHABILITY FORECAST — {name} (issue {issue} t12z) ===")
    print(f"    target {cfg.product.target_c:.0f}C | cast {cfg.product.cast_m:.0f}m | "
          f"max depth {cfg.product.max_reach_depth_m:.0f}m | bathy: {meta['bathy']}")
    if bias_src != "live":
        print("    ⚠ live buoy bias unavailable — using frozen prior (degraded)")
    if points is None:
        return 1
    print(f"    subsurface bias {central:+.1f}C (band {lo:+.1f}..{hi:+.1f}); surface anchor "
          f"{'GLSEA %.1fC' % meta['surf_sst'] if meta.get('surf_sst') else 'none'} held across leads\n")
    for p in points:
        # GO = reachable across the whole bias band; go? = central says yes but the
        # band disagrees; ~? = only the optimistic end reaches (honest, not binary)
        flag = "GO " if p.reachable_certain else ("go?" if p.reachable
                                                  else ("~? " if p.reachable_possible else "-- "))
        iso_s = f"{p.isotherm_depth_m:.1f} m" if p.isotherm_depth_m is not None else "none"
        reach_s = f"cold water {p.distance_m:.0f} m out" if p.reachable else "not in cast range"
        print(f"  {flag} +{p.lead_h:>3}h  {p.valid_time:%a %b %-d}  iso {iso_s:>7s}  {reach_s}")
    print(f"\nVERDICT: {summarize(points, windows)}")
    return 0


def _isfloat(s):
    try:
        float(s); return True
    except ValueError:
        return False


def _isdate(s):
    try:
        date.fromisoformat(s); return True
    except (ValueError, TypeError):
        return False


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
