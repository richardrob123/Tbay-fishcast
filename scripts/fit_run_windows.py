"""Fit local run windows from the observation ledger -> data/calib/run_windows_fitted.json.

    python scripts/fit_run_windows.py            # fit + write (idempotent)
    python scripts/fit_run_windows.py --dry-run  # report only

The run calendar reads the output automatically: any entry with a fit uses the MEASURED window
and is stamped fitted (n, n_years, shift vs the authored dates); entries without enough evidence
keep their authored literature window. No LLM (ADR-001) — pure statistics over validated rows.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tbay_fishcast.features import run_calendar as rc  # noqa: E402
from tbay_fishcast.knowledge import fit_run_windows as frw  # noqa: E402
from tbay_fishcast.knowledge import observations as obs  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "data" / "calib" / "run_windows_fitted.json"


def main(argv) -> int:
    dry = "--dry-run" in argv
    rows = obs.load()
    # fit against the AUTHORED calendar, never against already-fitted windows (no feedback loop:
    # a fitted window must not become the prior for the next fit, or it would drift each run)
    import copy
    raw = copy.deepcopy(_authored_entries())
    fits = frw.fit_all(raw, rows)
    print(f"ledger rows: {len(rows)}; calendar entries eligible: "
          f"{sum(1 for e in raw if str(e.get('species','')) in frw.MIGRATORY)}")
    if not fits:
        print("no entry met the evidence bar "
              f"(>={frw.MIN_REPORTS} dated reports across >={frw.MIN_YEARS} years) — "
              "authored windows stand unchanged")
    for fid, f in sorted(fits.items()):
        tag = "APPLIED" if f["applied"] else "candidate (below apply bar)"
        print(f"  {fid:22s} {f['authored_start']}..{f['authored_end']} -> "
              f"{f['start']}..{f['end']}  (n={f['n']}, years={f['n_years']}, "
              f"shift {f['shift_days']:+d} d)  [{tag}]")
    if dry:
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    applied = {k: v for k, v in fits.items() if v["applied"]}
    candidates = {k: v for k, v in fits.items() if not v["applied"]}
    OUT.write_text(json.dumps({
        "windows": applied,          # ONLY these are consumed by run_calendar
        "candidates": candidates,    # computed, visible, deliberately NOT shipped yet
        "apply_min_reports": frw.APPLY_MIN_REPORTS, "apply_min_years": frw.APPLY_MIN_YEARS,
        "min_reports": frw.MIN_REPORTS, "min_years": frw.MIN_YEARS,
        "tol_days": frw.TOL_DAYS, "percentiles": [frw.LO_PCT, frw.HI_PCT],
        "definition": ("Run windows fitted from the LOCAL observation ledger: percentiles of "
                       "dated report day-of-year within each authored window +/-tol, anchored to "
                       "the authored window so multi-run species (spring/fall steelhead) stay "
                       "separate. Effort-biased: describes when people REPORT fish, not "
                       "escapement. Entries below the evidence bar are absent -> authored window."),
        "ledger_rows": len(rows),
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, indent=1))
    print(f"wrote {OUT} ({len(fits)} fitted window(s))")
    return 0


def _authored_entries():
    """Calendar entries as AUTHORED (bypassing any existing fit), so fits never feed themselves."""
    import yaml
    raw = yaml.safe_load(rc._CALENDAR.read_text()) or []
    out = []
    for e in raw:
        win = e.get("window") or {}
        if "start" in win and "end" in win and e.get("mode") != "dip_net" and "trigger" not in e:
            out.append(e)
    return out


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
