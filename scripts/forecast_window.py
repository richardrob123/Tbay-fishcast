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
from tbay_fishcast.features.reachability import reachability, shore_distance_m  # noqa: E402
from tbay_fishcast.ingest import glsea, nonna  # noqa: E402
from tbay_fishcast.ingest.backfill import _open_first  # noqa: E402
from tbay_fishcast.ingest.lsofs_extract import extract_nodes, valid_time_from_dataset  # noqa: E402
from tbay_fishcast.ingest.lsofs_paths import LsofsFile, candidate_urls  # noqa: E402
from tbay_fishcast.ingest import lsofs_grid  # noqa: E402

TARGET_C = 12.0
CAST_M = 75.0
PROFILE_DEPTHS = [1, 2, 4, 6, 8, 10, 15]
LEADS = [("n", 6, 0)] + [("f", h, h) for h in (24, 48, 72, 96, 120)]  # (kind, file_hour, lead_h)


def _bathy_grid(lat, lon):
    """Fetch the NONNA patch once; return (depth grid, shore-distance field, res_m, note).

    Same 2-D bathymetry the isotherm map uses, so reachability is computed identically
    (any shoal/point in cast range counts, not just a single offshore transect)."""
    patch = nonna.fetch_patch(lat, lon, half_m=1200, scale_px=300)
    if patch.coverage_frac < 0.05:
        return None, None, None, "no NONNA nearshore coverage here"
    res = nonna.ground_res_m(patch.res_mercator_m, lat)
    dist = shore_distance_m(patch.depth, res)
    return patch.depth, dist, res, f"CHS NONNA-10 2-D patch ({res:.0f} m, water {patch.coverage_frac:.0%})"


def forecast_spot(cfg, lat, lon, name, node, issue, bias_stats):
    """Compute the reachability trajectory + windows for one spot. Reusable by the
    heartbeat. Returns (points, windows, meta) or (None, None, meta) if no bathymetry.
    `bias_stats` = (central, lo, hi, n) pooled subsurface bias (computed once by caller)."""
    central, lo, hi, n = bias_stats
    depth_grid, dist_field, res_m, bsrc = _bathy_grid(lat, lon)
    if depth_grid is None:
        return None, None, {"bathy": bsrc}
    try:
        surf_sst = glsea.fetch_sst(lat, lon, issue).sst_c
    except Exception:  # noqa: BLE001
        surf_sst = None
    points = []
    grid = None
    for kind, fh, lead in LEADS:
        f = LsofsFile(issue, "t12z", kind, fh)
        try:
            ds = _open_first(candidate_urls(f, cfg.lsofs.recent_bucket, cfg.lsofs.archive_bucket,
                                            byterange=False))
        except Exception:  # noqa: BLE001
            continue
        if node is None and grid is None:
            grid = lsofs_grid.read_grid(ds)
        this_node = node if node is not None else lsofs_grid.nearest_node(grid, lat, lon, min_depth_m=6.0).node
        vt = valid_time_from_dataset(ds)
        pr = sorted(extract_nodes(ds, {name: this_node}, PROFILE_DEPTHS), key=lambda r: r.depth_m)
        ds.close()
        depths = [r.depth_m for r in pr]
        raw = [r.temp_c for r in pr]
        surf_bias = (raw[0] - surf_sst) if surf_sst else 0.0
        bm = thermocline.BiasModel(surf_bias, central, lo, hi, n_buoys=n)
        iso = thermocline.isotherm_band(depths, raw, bm, TARGET_C)["central"]
        reach, closest, area = reachability(depth_grid, dist_field, iso, CAST_M, res_m)
        points.append(ForecastPoint(vt, lead, iso, closest, reach))
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
    issue = date.fromisoformat(rest[0]) if rest and _isdate(rest[0]) else date(2026, 8, 4)

    central, lo, hi, _, n = bias_live.pooled_subsurface_bias(cfg, issue)
    points, windows, meta = forecast_spot(cfg, lat, lon, name, node, issue, (central, lo, hi, n))
    print(f"=== REACHABILITY FORECAST — {name} (issue {issue} t12z) ===")
    print(f"    target {TARGET_C:.0f}C | cast {CAST_M:.0f}m | bathy: {meta['bathy']}")
    if points is None:
        return 1
    print(f"    subsurface bias {central:+.1f}C (band {lo:+.1f}..{hi:+.1f}); surface anchor "
          f"{'GLSEA %.1fC' % meta['surf_sst'] if meta.get('surf_sst') else 'none'} held across leads\n")
    for p in points:
        flag = "GO " if p.reachable else "-- "
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
