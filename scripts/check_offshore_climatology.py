"""Cross-check LSOFS's OFFSHORE stratification against the observed GLERL mooring climatology —
so the one committed multi-year in-situ profile finally does its job (it was built, committed, and
then read by nothing but a unit test; validation inventory). This is a gross-error tripwire on the
model's central-basin thermocline, NOT a nearshore correction: the mooring is ~200 km SE of Thunder
Bay in the central basin, and its climatology is a 2018-2020 half-month mean, so the comparison
band is deliberately generous — it catches a badly-wrong LSOFS day, it does not fine-tune anything.

For the issue date it: pulls the mooring half-month prior (observed iso-12 depth + mixed-layer
temp), extracts the LSOFS nowcast column at the mooring node, computes the modelled iso-12 depth +
mixed-layer temp, and logs both with the difference. Appends data/offshore_check_log.csv and prints
a verdict. Offline of the 4x/day heartbeat (a QA accumulator, like the isotherm gate). No LLM.

    python scripts/check_offshore_climatology.py [issue YYYY-MM-DD]
"""
from __future__ import annotations

import csv
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tbay_fishcast.config import load_config  # noqa: E402
from tbay_fishcast.features import cross_shore  # noqa: E402
from tbay_fishcast.ingest import lsofs_grid, mooring  # noqa: E402
from tbay_fishcast.ingest.backfill import _open_first  # noqa: E402
from tbay_fishcast.ingest.lsofs_extract import extract_native_columns  # noqa: E402
from tbay_fishcast.ingest.lsofs_paths import LsofsFile, candidate_urls  # noqa: E402

LOG = Path(__file__).resolve().parents[1] / "data" / "offshore_check_log.csv"
# Generous agreement bands: interannual variability + a 2-year climatology mean vs one model day.
# Only a GROSS divergence (model thermocline in the wrong place) should trip these.
DEPTH_TOL_M = 8.0
TEMP_TOL_C = 4.0


def _modelled_offshore(cfg, issue: date):
    """(iso12_depth_m, mixed_layer_c) from the LSOFS nowcast column at the mooring node, or None."""
    ds = _open_first(candidate_urls(LsofsFile(issue, "t12z", "n", 6),
                                    cfg.lsofs.recent_bucket, cfg.lsofs.archive_bucket, byterange=False))
    grid = lsofs_grid.read_grid(ds)
    nd = lsofs_grid.nearest_node(grid, mooring.MOORING_LAT, mooring.MOORING_LON, min_depth_m=10.0).node
    col = extract_native_columns(ds, {"m": int(nd)})["m"]
    ds.close()
    depths, temps = np.asarray(col.depths_m, float), np.asarray(col.temps_c, float)
    if depths.size < 4:
        return None
    order = np.argsort(depths)
    depths, temps = depths[order], temps[order]
    iso = cross_shore.isotherm_depth(depths, temps, mooring.TARGET_C) if np.nanmax(temps) >= mooring.TARGET_C else None
    ml = float(np.nanmean(temps[:3]))          # top ~3 sigma layers ≈ mixed layer
    return (iso, ml)


def main(argv) -> int:
    cfg = load_config()
    issue = date.fromisoformat(argv[1]) if len(argv) > 1 else datetime.now(timezone.utc).date()
    prior = mooring.prior_for_day(issue)
    if prior is None:
        print("no mooring climatology for this period — skip"); return 0
    try:
        modelled = _modelled_offshore(cfg, issue)
    except Exception as e:  # noqa: BLE001
        print(f"LSOFS offshore extract failed ({str(e)[:60]}) — skip (no false alarm)"); return 0
    if modelled is None:
        print("LSOFS column too short — skip"); return 0
    m_iso, m_ml = modelled

    d_iso = (m_iso - prior.iso12_depth_m) if (m_iso is not None and prior.iso12_depth_m is not None) else None
    d_ml = m_ml - prior.mixed_layer_c
    iso_ok = d_iso is None or abs(d_iso) <= DEPTH_TOL_M
    ml_ok = abs(d_ml) <= TEMP_TOL_C
    verdict = "ok" if (iso_ok and ml_ok) else "DIVERGENT"
    row = {
        "issue": issue.isoformat(), "period": prior.period,
        "obs_iso12_m": prior.iso12_depth_m, "mdl_iso12_m": round(m_iso, 2) if m_iso is not None else "",
        "d_iso_m": round(d_iso, 2) if d_iso is not None else "",
        "obs_ml_c": prior.mixed_layer_c, "mdl_ml_c": round(m_ml, 2), "d_ml_c": round(d_ml, 2),
        "verdict": verdict, "n_clim_profiles": prior.n_profiles,
        "retrieved_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    new = not LOG.exists()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)
    print(f"offshore check [{prior.period}]: iso12 model {row['mdl_iso12_m']} vs clim "
          f"{prior.iso12_depth_m} m (Δ{row['d_iso_m']}); mixed-layer model {row['mdl_ml_c']} vs "
          f"clim {prior.mixed_layer_c} °C (Δ{row['d_ml_c']}) -> {verdict}")
    if verdict == "DIVERGENT":
        print("⚠ LSOFS offshore stratification diverges from the observed climatology beyond the "
              "gross-error band — the central-basin thermocline may be misplaced this cycle.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
