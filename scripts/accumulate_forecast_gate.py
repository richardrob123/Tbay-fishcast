"""Forecast-lead verification gate (ADR-021) — how good was the 12 C-isotherm-depth FORECAST
made L hours ago, vs what the buoy actually showed. The nowcast gate (accumulate_gate.py)
scores lead 0 only; this scores the FORECAST the map ships at +24…+120 h, so we can finally
say how skill decays with lead. Deterministic, idempotent, no LLM (ADR-001).

For a valid day D (12z) and each lead L in {24,48,72,96,120} h:
  * the forecast that was valid at D was issued on day D − L/24 (t12z, forecast hour L);
  * we correct that forecast column with the anchor available AT ISSUE TIME (issue-day GLSEA)
    — matching what the product actually showed, not a hindsight re-anchor;
  * score its 12 C-isotherm depth against the GLOS thermistor profile at D.

No truth-leak (issue-day GLSEA only, never the valid-day profile). Both far chains apply, with
the same caveats as the nowcast gate (see docs/ACCURACY_SCORECARD.md).

    python scripts/accumulate_forecast_gate.py [end YYYY-MM-DD]
"""
from __future__ import annotations

import csv
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.features import bias_live, thermocline  # noqa: E402
from tbay_fishcast.features.cross_shore import isotherm_depth  # noqa: E402
from tbay_fishcast.ingest import glos, glsea, lsofs_grid  # noqa: E402
from tbay_fishcast.ingest.backfill import _open_first  # noqa: E402
from tbay_fishcast.ingest.lsofs_extract import extract_native_columns  # noqa: E402
from tbay_fishcast.ingest.lsofs_paths import LsofsFile, candidate_urls  # noqa: E402

TARGET_C = 12.0
FROZEN = bias_live.FROZEN_PRIOR
LEADS_H = [24, 48, 72, 96, 120]
LOG = Path(__file__).resolve().parents[1] / "data" / "forecast_gate_log.csv"
FIELDS = ["valid_date", "chain", "lead_h", "issue_date", "obs_iso_m",
          "fcst_raw_iso_m", "fcst_corr_iso_m", "abs_err_m",
          "bias_source", "glsea_anchor", "retrieved_utc"]
LOOKBACK_DAYS = 6


def _existing(path: Path) -> set[tuple[str, str, str]]:
    if not path.exists():
        return set()
    with path.open() as f:
        return {(r["valid_date"], r["chain"], r["lead_h"]) for r in csv.DictReader(f)}


def _r(x, nd=2):
    return round(x, nd) if x is not None else ""


def fcst_row(cfg, chain, samples, valid_day: date, lead_h: int) -> dict | None:
    issue = valid_day - timedelta(hours=lead_h)
    vt = datetime(valid_day.year, valid_day.month, valid_day.day, 12, tzinfo=timezone.utc)
    obs = {z: t for z, t in glos.profile_at(samples, vt, tol_h=1.5).items() if t == t}
    if len(obs) < 5:
        return None
    zs = sorted(obs)
    obs_iso = isotherm_depth(zs, [obs[z] for z in zs], TARGET_C)
    if obs_iso is None:
        return None
    f = LsofsFile(issue, "t12z", "f", lead_h)
    try:
        ds = _open_first(candidate_urls(f, cfg.lsofs.recent_bucket,
                                        cfg.lsofs.archive_bucket, byterange=False))
    except Exception:  # noqa: BLE001 - forecast file absent/unreachable
        return None
    try:
        grid = lsofs_grid.read_grid(ds)
        node = lsofs_grid.nearest_node(grid, chain.lat, chain.lon, min_depth_m=3.0).node
        col = extract_native_columns(ds, {chain.station_id: node})[chain.station_id]
    finally:
        ds.close()
    z, raw = col.depths_m, col.temps_c
    raw_iso = isotherm_depth(z, raw, TARGET_C)
    # anchor to the GLSEA available AT ISSUE TIME (what the forecast could have used), never
    # the valid-day profile — that would leak the truth being scored.
    try:
        g = glsea.fetch_recent_sst(chain.lat, chain.lon, issue)
        g_sst = g.sst_c if g else None
        anchor = "ok" if g_sst is not None else "absent"
    except Exception:  # noqa: BLE001
        g_sst, anchor = None, "absent"
    c, lo, hi, _, n, src = bias_live.pooled_or_prior(cfg, issue)
    corr = None
    if g_sst is not None:
        bm = thermocline.BiasModel(raw[0] - g_sst, c, lo, hi, n_buoys=n)
        corr = thermocline.isotherm_band(z, raw, bm, TARGET_C)["central"]
    return {
        "valid_date": valid_day.isoformat(), "chain": chain.station_id,
        "lead_h": str(lead_h), "issue_date": issue.isoformat(),
        "obs_iso_m": _r(obs_iso), "fcst_raw_iso_m": _r(raw_iso),
        "fcst_corr_iso_m": _r(corr),
        "abs_err_m": _r(abs(corr - obs_iso)) if corr is not None else "",
        "bias_source": src, "glsea_anchor": anchor,
        "retrieved_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def main(argv) -> int:
    end = date.fromisoformat(argv[1]) if len(argv) > 1 else datetime.now(timezone.utc).date()
    cfg = load_config()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    have = _existing(LOG)
    valid_days = [end - timedelta(days=k) for k in range(LOOKBACK_DAYS + 1)][::-1]
    new_rows: list[dict] = []
    for cid, chain in glos.CHAINS.items():
        try:
            samples = glos.fetch_chain(cid, valid_days[0] - timedelta(days=1), end + timedelta(days=1))
        except Exception as e:  # noqa: BLE001
            print(f"{cid}: chain fetch failed ({str(e)[:50]})"); continue
        for vd in valid_days:
            for L in LEADS_H:
                if (vd.isoformat(), cid, str(L)) in have:
                    continue
                row = fcst_row(cfg, chain, samples, vd, L)
                if row:
                    new_rows.append(row)
                    print(f"{cid} {vd} +{L}h: obs {row['obs_iso_m']} fcst {row['fcst_corr_iso_m']} "
                          f"err {row['abs_err_m']} anchor {row['glsea_anchor']}")
    if not new_rows:
        print("forecast-gate: nothing new to append")
        return 0
    write_header = not LOG.exists()
    new_rows.sort(key=lambda r: (r["valid_date"], r["chain"], int(r["lead_h"])))
    with LOG.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            w.writeheader()
        w.writerows(new_rows)
    print(f"forecast-gate: appended {len(new_rows)} row(s) -> {LOG.relative_to(LOG.parents[2])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
