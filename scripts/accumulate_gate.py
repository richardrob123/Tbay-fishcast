"""Daily isotherm-depth gate accumulation — the standing accuracy log.

The depth-bias study (docs/ERA5_CROSSCHECK.md) found LSOFS's thermocline runs too deep
in stratified conditions, but the fix isn't shippable yet: too few observed-profile days
(n=4) to fit a LOBO-surviving, site-aware correction. This script closes that by running
the isotherm-DEPTH gate (product's 12 C-crossing depth vs an observed thermistor profile)
once per day and APPENDING the result to data/gate_log.csv. Over a season this accumulates
the observations a robust correction needs.

Deterministic, no LLM (ADR-001): it is a plain step in the daily coast-site Action, not a
Routine. Idempotent — a (date, chain) already logged is skipped, so re-runs never duplicate.

    python scripts/accumulate_gate.py [end YYYY-MM-DD]   # default: today (UTC)
"""
from __future__ import annotations

import csv
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.features import thermocline  # noqa: E402
from tbay_fishcast.features.cross_shore import isotherm_depth  # noqa: E402
from tbay_fishcast.ingest import glos, glsea, lsofs_grid  # noqa: E402
from tbay_fishcast.ingest.backfill import _open_first  # noqa: E402
from tbay_fishcast.ingest.lsofs_extract import extract_native_columns  # noqa: E402
from tbay_fishcast.ingest.lsofs_paths import LsofsFile, candidate_urls  # noqa: E402

TARGET_C = 12.0
# pooled subsurface warm bias (from validate_engine / the live buoys)
CENTRAL, LO, HI = 3.31, 1.51, 5.55
LOG = Path(__file__).resolve().parents[1] / "data" / "gate_log.csv"
FIELDS = ["date", "chain", "obs_iso_m", "raw_iso_m", "corr_iso_m", "abs_err_m", "retrieved_utc"]
LOOKBACK_DAYS = 5   # walk back this far to find the most recent day both feeds resolve


def _existing_keys(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    with path.open() as f:
        return {(r["date"], r["chain"]) for r in csv.DictReader(f)}


def gate_for_day(cfg, chain, samples, day: date) -> dict | None:
    """Run the isotherm-depth gate for one chain on one day; None if either feed is absent."""
    vt = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
    obs = {z: t for z, t in glos.profile_at(samples, vt, tol_h=1.5).items() if t == t}
    if len(obs) < 5:
        return None
    zs = sorted(obs)
    obs_iso = isotherm_depth(zs, [obs[z] for z in zs], TARGET_C)
    f = LsofsFile(day, "t12z", "n", 6)
    try:
        ds = _open_first(candidate_urls(f, cfg.lsofs.recent_bucket,
                                        cfg.lsofs.archive_bucket, byterange=False))
    except Exception:  # noqa: BLE001
        return None
    try:
        grid = lsofs_grid.read_grid(ds)
        node = lsofs_grid.nearest_node(grid, chain.lat, chain.lon, min_depth_m=3.0).node
        col = extract_native_columns(ds, {chain.station_id: node})[chain.station_id]
    finally:
        ds.close()
    z, raw = col.depths_m, col.temps_c
    raw_iso = isotherm_depth(z, raw, TARGET_C)
    try:
        g = glsea.fetch_sst(chain.lat, chain.lon, day).sst_c
    except Exception:  # noqa: BLE001
        g = obs.get(min(obs), raw[0])
    bm = thermocline.BiasModel(raw[0] - g, CENTRAL, LO, HI)
    corr_iso = thermocline.isotherm_band(z, raw, bm, TARGET_C)["central"]
    if obs_iso is None or corr_iso is None:
        return None
    return {
        "date": day.isoformat(), "chain": chain.station_id,
        "obs_iso_m": round(obs_iso, 2),
        "raw_iso_m": round(raw_iso, 2) if raw_iso is not None else "",
        "corr_iso_m": round(corr_iso, 2), "abs_err_m": round(abs(corr_iso - obs_iso), 2),
        "retrieved_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def main(argv) -> int:
    end = date.fromisoformat(argv[1]) if len(argv) > 1 else datetime.now(timezone.utc).date()
    cfg = load_config()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    have = _existing_keys(LOG)
    new_rows: list[dict] = []

    for cid, chain in glos.CHAINS.items():
        try:
            samples = glos.fetch_chain(cid, end - timedelta(days=LOOKBACK_DAYS + 1),
                                       end + timedelta(days=1))
        except Exception as e:  # noqa: BLE001
            print(f"{cid}: chain fetch failed ({str(e)[:50]})"); continue
        # newest available day this chain resolves that we haven't logged yet
        for day in (end - timedelta(days=k) for k in range(LOOKBACK_DAYS + 1)):
            if (day.isoformat(), cid) in have:
                break  # already have this and (walking older) everything before it
            row = gate_for_day(cfg, chain, samples, day)
            if row:
                new_rows.append(row)
                print(f"{cid} {day}: |err|={row['abs_err_m']} m "
                      f"(obs {row['obs_iso_m']} / corr {row['corr_iso_m']})")
                break
        else:
            print(f"{cid}: no unlogged day resolved in last {LOOKBACK_DAYS} d")

    if not new_rows:
        print("gate: nothing new to append")
        return 0
    write_header = not LOG.exists()
    with LOG.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            w.writeheader()
        w.writerows(new_rows)
    print(f"gate: appended {len(new_rows)} row(s) -> {LOG.relative_to(LOG.parents[2])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
