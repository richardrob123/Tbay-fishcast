"""Daily isotherm-depth gate accumulation — the standing accuracy log.

Runs the isotherm-DEPTH gate (product's 12 C-crossing depth vs an observed GLOS
thermistor profile) for EVERY not-yet-logged day in the lookback window and appends
the results to data/gate_log.csv. Over a season this accumulates the observations a
robust depth-bias correction needs (docs/ERA5_CROSSCHECK.md).

AUDIT_ROUND3 redesign (the first version had three defects):
  * walks every unlogged (day, chain) — no contiguity assumption, no one-row cap, so
    feed lag or a missed Action run can no longer punch permanent holes in the log;
  * logs BOTH corrections: the frozen 2026-08 prior (cross-run comparable) and the
    live product correction via pooled_or_prior with its source — the log now measures
    the shipped product, not just a constant;
  * NO truth-leak fallback: when GLSEA is absent for a day the corrected columns stay
    empty and glsea_anchor records it — the observed profile is never fed into the
    prediction being scored. Raw (uncorrected) isotherm error is still logged.

Known transfer limits (documented, not hidden): only 2 chains exist, both far from
Thunder Bay (45216/Ontonagon ~170 km, LLO1/Duluth ~270 km) and site-dependence is
already observed — a leave-one-site-out fit over 2 sites is structurally impossible,
so this log supports (a) a POOLED-offset check with honest caveats and (b) per-site
error tracking, not a site-aware model. Deterministic, idempotent, no LLM (ADR-001).

    python scripts/accumulate_gate.py [end YYYY-MM-DD]   # default: today (UTC)
"""
from __future__ import annotations

import csv
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.features import bias_live, thermocline  # noqa: E402
from tbay_fishcast.features.cross_shore import isotherm_crossing, isotherm_depth  # noqa: E402
from tbay_fishcast.ingest import glos, glsea, lsofs_grid  # noqa: E402
from tbay_fishcast.ingest.backfill import _open_first  # noqa: E402
from tbay_fishcast.ingest.lsofs_extract import extract_native_columns  # noqa: E402
from tbay_fishcast.ingest.lsofs_paths import LsofsFile, candidate_urls  # noqa: E402

TARGET_C = 12.0
FROZEN = bias_live.FROZEN_PRIOR          # (central, lo, hi) — 2026-08 prior, comparable across runs
LOG = Path(__file__).resolve().parents[1] / "data" / "gate_log.csv"
FIELDS = ["date", "chain", "obs_iso_m", "raw_iso_m",
          "corr_iso_frozen_m", "abs_err_frozen_m",
          "corr_iso_live_m", "abs_err_live_m", "bias_source",
          "glsea_anchor", "retrieved_utc"]
LOOKBACK_DAYS = 5


def _existing_keys(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    with path.open() as f:
        return {(r["date"], r["chain"]) for r in csv.DictReader(f)}


def _r(x, nd=2):
    return round(x, nd) if x is not None else ""


def gate_for_day(cfg, chain, samples, day: date, live_bias) -> dict | None:
    """One (chain, day) gate row; None if the observed profile or LSOFS is absent."""
    vt = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
    obs = {z: t for z, t in glos.profile_at(samples, vt, tol_h=1.5).items() if t == t}
    if len(obs) < 5:
        return None
    zs = sorted(obs)
    # ADR-048: the same censoring guard as the forecast gate. isotherm_depth returns the TOP
    # SENSOR DEPTH when the whole column is already colder than the target — deliberate for the
    # map, a fabrication as a validation reference. Duluth LLO1 runs 4.0-8.2 C over its whole
    # 3-38 m column in August, so it produced obs_iso_m = 3.00 (its shallowest thermistor) every
    # single day. Note the asymmetry that let this hide for a week: a too-WARM column already
    # returned None and was skipped, so only the too-cold direction leaked through.
    obs_iso, obs_status = isotherm_crossing(zs, [obs[z] for z in zs], TARGET_C)
    if obs_status != "crossing":
        return None
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
    # surface anchor: same-day GLSEA ONLY. No fallback to the observed profile — that
    # injects the truth into the prediction being scored (AUDIT_ROUND3 truth-leak).
    try:
        g = glsea.fetch_sst(chain.lat, chain.lon, day).sst_c
        anchor = "ok"
    except Exception:  # noqa: BLE001
        g, anchor = None, "absent"
    corr_f = corr_l = None
    live_c, live_lo, live_hi, live_n, live_src = live_bias
    if g is not None:
        bm_f = thermocline.BiasModel(raw[0] - g, *FROZEN)
        corr_f = thermocline.isotherm_band(z, raw, bm_f, TARGET_C)["central"]
        bm_l = thermocline.BiasModel(raw[0] - g, live_c, live_lo, live_hi, n_buoys=live_n)
        corr_l = thermocline.isotherm_band(z, raw, bm_l, TARGET_C)["central"]
    return {
        "date": day.isoformat(), "chain": chain.station_id,
        "obs_iso_m": _r(obs_iso), "raw_iso_m": _r(raw_iso),
        "corr_iso_frozen_m": _r(corr_f),
        "abs_err_frozen_m": _r(abs(corr_f - obs_iso)) if corr_f is not None else "",
        "corr_iso_live_m": _r(corr_l),
        "abs_err_live_m": _r(abs(corr_l - obs_iso)) if corr_l is not None else "",
        "bias_source": live_src, "glsea_anchor": anchor,
        "retrieved_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def main(argv) -> int:
    end = date.fromisoformat(argv[1]) if len(argv) > 1 else datetime.now(timezone.utc).date()
    cfg = load_config()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    have = _existing_keys(LOG)
    days = [end - timedelta(days=k) for k in range(LOOKBACK_DAYS + 1)][::-1]
    new_rows: list[dict] = []
    live_cache: dict[str, tuple] = {}

    for cid, chain in glos.CHAINS.items():
        try:
            samples = glos.fetch_chain(cid, days[0] - timedelta(days=1), end + timedelta(days=1))
        except Exception as e:  # noqa: BLE001
            print(f"{cid}: chain fetch failed ({str(e)[:50]})"); continue
        for day in days:                       # EVERY unlogged day — no breaks
            if (day.isoformat(), cid) in have:
                continue
            if day.isoformat() not in live_cache:
                c, lo, hi, _, n, src = bias_live.pooled_or_prior(cfg, day)
                live_cache[day.isoformat()] = (c, lo, hi, n, src)
            row = gate_for_day(cfg, chain, samples, day, live_cache[day.isoformat()])
            if row:
                new_rows.append(row)
                print(f"{cid} {day}: obs {row['obs_iso_m']} raw {row['raw_iso_m']} "
                      f"frozen-err {row['abs_err_frozen_m']} live-err {row['abs_err_live_m']} "
                      f"anchor {row['glsea_anchor']}")

    if not new_rows:
        print("gate: nothing new to append")
        return 0
    write_header = not LOG.exists()
    new_rows.sort(key=lambda r: (r["date"], r["chain"]))
    with LOG.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            w.writeheader()
        w.writerows(new_rows)
    print(f"gate: appended {len(new_rows)} row(s) -> {LOG.relative_to(LOG.parents[2])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
