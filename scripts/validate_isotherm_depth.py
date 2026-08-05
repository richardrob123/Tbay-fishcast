"""Isotherm-depth validation gate — validate the OUTPUT, not a proxy.

Every other check measures temperature error at a point. This one measures the thing
the product actually reports: the DEPTH of the target isotherm. It compares the model's
isotherm depth to the depth read straight off an observed thermistor profile, at the two
GLOS chain sites that resolve the full column — 45216 (0–12 m) and Duluth LLO1 (0–43 m).

The mean |isotherm-depth error| is a standing gate: it is the honest accuracy of the
number that drives the cast-range decision, and it should be driven down as we add data
(finer SST anchor, currents, a local logger). No LLM.

    python scripts/validate_isotherm_depth.py [days] [end YYYY-MM-DD]
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.features import thermocline  # noqa: E402
from tbay_fishcast.features.cross_shore import isotherm_depth  # noqa: E402
from tbay_fishcast.ingest import glos, glsea, lsofs_grid  # noqa: E402
from tbay_fishcast.ingest.backfill import _open_first  # noqa: E402
from tbay_fishcast.ingest.lsofs_extract import extract_nodes, valid_time_from_dataset  # noqa: E402
from tbay_fishcast.ingest.lsofs_paths import LsofsFile, candidate_urls  # noqa: E402

TARGET_C = 12.0
PROFILE_DEPTHS = [1, 2, 4, 6, 8, 10, 15]
# pooled subsurface warm bias (from validate_engine / the live buoys)
CENTRAL, LO, HI = 3.31, 1.51, 5.55


def main(argv) -> int:
    ndays = int(argv[1]) if len(argv) > 1 else 12
    end = date.fromisoformat(argv[2]) if len(argv) > 2 else date(2026, 8, 4)
    days = [end - timedelta(days=k) for k in range(ndays)][::-1]
    cfg = load_config()

    all_err = []
    for cid, chain in glos.CHAINS.items():
        try:
            samples = glos.fetch_chain(cid, days[0] - timedelta(days=1), end + timedelta(days=1))
        except Exception as e:  # noqa: BLE001
            print(f"{cid}: chain fetch failed ({str(e)[:40]})"); continue
        print(f"\n=== {chain.name} ===")
        print(f"{'day':11s} {'obs_iso':>7s} {'raw_iso':>7s} {'corr_iso':>8s} {'|err|_corr':>10s}")
        errs = []
        for day in days:
            vt = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
            obs = glos.profile_at(samples, vt, tol_h=1.5)
            obs = {z: t for z, t in obs.items() if t == t}  # drop NaN
            if len(obs) < 5:
                continue
            zs = sorted(obs)
            obs_iso = isotherm_depth(zs, [obs[z] for z in zs], TARGET_C)
            f = LsofsFile(day, "t12z", "n", 6)
            try:
                ds = _open_first(candidate_urls(f, cfg.lsofs.recent_bucket,
                                                cfg.lsofs.archive_bucket, byterange=False))
            except Exception:  # noqa: BLE001
                continue
            grid = lsofs_grid.read_grid(ds)
            node = lsofs_grid.nearest_node(grid, chain.lat, chain.lon, min_depth_m=3.0).node
            pr = sorted(extract_nodes(ds, {cid: node}, PROFILE_DEPTHS), key=lambda r: r.depth_m)
            ds.close()
            z = [r.depth_m for r in pr]
            raw = [r.temp_c for r in pr]
            raw_iso = isotherm_depth(z, raw, TARGET_C)
            # corrected: surface anchored to satellite, subsurface pooled bias
            try:
                g = glsea.fetch_sst(chain.lat, chain.lon, day).sst_c
            except Exception:  # noqa: BLE001
                g = obs.get(min(obs), raw[0])  # fall back to observed near-surface
            bm = thermocline.BiasModel(raw[0] - g, CENTRAL, LO, HI)
            corr_iso = thermocline.isotherm_band(z, raw, bm, TARGET_C)["central"]
            e = abs(corr_iso - obs_iso) if (obs_iso is not None and corr_iso is not None) else None
            if e is not None:
                errs.append(e); all_err.append(e)
            fmt = lambda x: f"{x:.1f}" if x is not None else "none"
            print(f"{str(day):11s} {fmt(obs_iso):>7s} {fmt(raw_iso):>7s} {fmt(corr_iso):>8s} "
                  f"{(f'{e:.1f} m' if e is not None else 'n/a'):>10s}")
        if errs:
            print(f"  -> mean |isotherm-depth error| = {np.mean(errs):.1f} m (n={len(errs)}, "
                  f"median {np.median(errs):.1f})")

    if all_err:
        print("\n" + "=" * 60)
        print(f"GATE: pooled |isotherm-depth error| = {np.mean(all_err):.2f} m "
              f"(median {np.median(all_err):.2f}, n={len(all_err)}) across both chains")
        print("This is the decision-variable accuracy — drive it down.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
