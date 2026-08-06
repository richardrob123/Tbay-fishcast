"""Field-session logger — rule 7's pre-registration capture (ADR-005).

CLAUDE rule 7: "Forecasts freeze at session start; the frozen score is stored with
every field-session record (selection-bias correction later depends on this column
existing now)." AUDIT_ROUND3 found the mechanism did not exist in code — every outing
without it leaks out of the future calibration dataset permanently. This closes it.

Usage — at the START of an outing (freezes the current forecast for the station):

    python scripts/log_session.py start silver_harbour_outer

Afterwards (attaches the ≤2-min debrief, RESEARCH_PROTOCOL fields):

    python scripts/log_session.py debrief silver_harbour_outer \
        --hours 2.5 --casts 60 --lures "spoon,jig" \
        --catch "laker:1@62cm" --water "hand-cold, clear, seam 40m out" --notes "..."

Records land in data/field_sessions.csv (append-only, committed). The frozen forecast
(verdict + isotherm + band + bias source at invocation) is auto-attached at `start`
and NEVER recomputed at debrief time — that's the point. Blanks are logged (protocol:
"blanks are logged"). Deterministic, no LLM.
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

LOG = Path(__file__).resolve().parents[1] / "data" / "field_sessions.csv"
FIELDS = ["session_utc", "station", "phase",
          # frozen at start — the pre-registered forecast
          "frozen_issue", "frozen_verdict", "frozen_iso_m", "frozen_reach",
          "frozen_reach_certain", "frozen_bias_source",
          # debrief (protocol fields; blanks are logged)
          "hours", "casts", "lures", "catch", "water_obs", "notes"]


def _append(row: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    write_header = not LOG.exists()
    with LOG.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})


def cmd_start(args) -> int:
    from datetime import date as _date

    import forecast_window as fw
    from tbay_fishcast.config import load_config
    from tbay_fishcast.features import bias_live
    from tbay_fishcast.features.forecast import summarize

    cfg = load_config()
    st = cfg.station(args.station)
    issue = fw.resolve_issue(cfg, datetime.now(timezone.utc).date())
    if issue is None:
        print("no LSOFS cycle available — session logged with empty frozen forecast")
        _append({"session_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "station": st.id, "phase": "start", "frozen_issue": "",
                 "frozen_verdict": "NO DATA"})
        return 0
    c, lo, hi, _, n, src = bias_live.pooled_or_prior(cfg, issue)
    pts, wins, meta = fw.forecast_spot(cfg, st.lat, st.lon, st.name, st.lsofs_node,
                                       issue, (c, lo, hi, n))
    now0 = pts[0] if pts else None
    row = {
        "session_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "station": st.id, "phase": "start", "frozen_issue": issue.isoformat(),
        "frozen_verdict": summarize(pts, wins) if pts else "NO DATA",
        "frozen_iso_m": (round(now0.isotherm_depth_m, 1)
                         if now0 and now0.isotherm_depth_m is not None else ""),
        "frozen_reach": (str(bool(now0.reachable)) if now0 else ""),
        "frozen_reach_certain": (str(bool(now0.reachable_certain)) if now0 else ""),
        "frozen_bias_source": src,
    }
    _append(row)
    print(f"FROZEN {st.id} @ {row['session_utc']}: {row['frozen_verdict']}"
          f" (iso {row['frozen_iso_m'] or 'n/a'} m, reach {row['frozen_reach']},"
          f" certain {row['frozen_reach_certain']}, bias {src})")
    print("Fish. Then: python scripts/log_session.py debrief", st.id, "...")
    return 0


def cmd_debrief(args) -> int:
    _append({
        "session_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "station": args.station, "phase": "debrief",
        "hours": args.hours or "", "casts": args.casts or "", "lures": args.lures or "",
        "catch": args.catch or "", "water_obs": args.water or "", "notes": args.notes or "",
    })
    print(f"debrief logged for {args.station} (blanks are logged — that's the protocol)")
    return 0


def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("start"); p1.add_argument("station")
    p2 = sub.add_parser("debrief"); p2.add_argument("station")
    for flag in ("--hours", "--casts", "--lures", "--catch", "--water", "--notes"):
        p2.add_argument(flag, default="")
    args = ap.parse_args(argv[1:])
    return cmd_start(args) if args.cmd == "start" else cmd_debrief(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
