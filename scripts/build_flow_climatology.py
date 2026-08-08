"""Per-gauge discharge climatology from the ECCC HYDAT daily-mean ARCHIVE (one-time + rare refresh).

Why: the live river-flow feature deliberately refused to call a flow "high" or "low" — that needs a
per-gauge climatology the system didn't hold (PROVENANCE_LEDGER). But ECCC's `hydrometric-daily-mean`
collection holds DECADES of quality-controlled daily discharge for the same three gauges the map
reads live. This script fetches the full record once and reduces it to day-of-year percentiles, so
the live marker can honestly say "20 m³/s ≈ 35th percentile for early August" — a measured claim.

Method: for each gauge, pull all (date, discharge) rows; for each day-of-year, pool observations in
a ±10-day window across all years (stabilizes the estimate; flow climatology is smooth at that
scale) and record p10/p25/p50/p75/p90 plus the pooled n. Output: data/calib/flow_climatology.json.
Deterministic given the archive; re-run occasionally to fold in newly-published years. No LLM.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tbay_fishcast.ingest.hydat import GAUGES  # noqa: E402

DAILY_MEAN = "https://api.weather.gc.ca/collections/hydrometric-daily-mean/items"
OUT = ROOT / "data" / "calib" / "flow_climatology.json"
WINDOW_D = 10          # ±days pooled around each day-of-year
PAGE = 10000


def fetch_all(station: str) -> list[tuple[str, float]]:
    """All (ISO-date, discharge m³/s) rows for a station, paginated."""
    rows, offset = [], 0
    while True:
        r = requests.get(DAILY_MEAN, params={
            "STATION_NUMBER": station, "limit": PAGE, "offset": offset,
            "properties": "DATE,DISCHARGE", "f": "json"}, timeout=120)
        r.raise_for_status()
        feats = r.json().get("features", [])
        for f in feats:
            p = f.get("properties", {})
            d, q = p.get("DATE"), p.get("DISCHARGE")
            if d and q is not None and float(q) >= 0.0:
                rows.append((d[:10], float(q)))
        if len(feats) < PAGE:
            return rows
        offset += PAGE


def doy_percentiles(rows: list[tuple[str, float]]) -> dict:
    by_doy = defaultdict(list)
    for d, q in rows:
        try:
            doy = datetime.fromisoformat(d).timetuple().tm_yday
        except ValueError:
            continue
        by_doy[min(doy, 365)].append(q)     # fold Dec 31 of leap years into 365
    out = {}
    for doy in range(1, 366):
        pool = []
        for k in range(doy - WINDOW_D, doy + WINDOW_D + 1):
            pool.extend(by_doy.get(((k - 1) % 365) + 1, []))
        if len(pool) < 30:                  # too thin to claim a percentile
            continue
        a = np.asarray(pool)
        out[str(doy)] = {"p10": round(float(np.percentile(a, 10)), 3),
                         "p25": round(float(np.percentile(a, 25)), 3),
                         "p50": round(float(np.percentile(a, 50)), 3),
                         "p75": round(float(np.percentile(a, 75)), 3),
                         "p90": round(float(np.percentile(a, 90)), 3),
                         "n": int(a.size)}
    return out


def main() -> int:
    out = {"built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "source": "ECCC hydrometric-daily-mean archive", "window_days": WINDOW_D,
           "gauges": {}}
    for mid, station in GAUGES.items():
        rows = fetch_all(station)
        years = sorted({d[:4] for d, _ in rows})
        clim = doy_percentiles(rows)
        out["gauges"][mid] = {"station": station, "n_days": len(rows),
                              "years": f"{years[0]}-{years[-1]}" if years else "none",
                              "doy": clim}
        print(f"{mid} ({station}): {len(rows)} daily means, {years[0] if years else '?'}–"
              f"{years[-1] if years else '?'}, {len(clim)} DOY bins")
    OUT.write_text(json.dumps(out))
    print(f"wrote {OUT} ({OUT.stat().st_size/1e3:.0f} kB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
