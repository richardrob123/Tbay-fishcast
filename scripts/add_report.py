"""Hand-entered fishing report -> observation candidate (ADR-042 stage 1).

WHY THIS EXISTS: the richest local reporting sits in places no crawler may legitimately reach —
Facebook groups above all. Meta's robots.txt prohibits automated collection without their written
permission, and the Groups API was retired in 2024, so group content has no sanctioned machine
path at all. But a MEMBER reading their own feed and typing what they saw is not scraping; it is
the oldest form of field reporting, and it is exactly how the good stuff gets into the ledger.

So this is the human lane. Paste what you read, name where it came from, and it lands in the same
candidate queue the Reddit sweep feeds — after which the research Routine extracts schema rows
under the same validation, gazetteer and dedup gate as every other source. No shortcut around the
schema, just a different way in.

    python scripts/add_report.py --source "TBay Fishing (FB group)" --text "Kam mouth was
        stacked this morning, buddy got 4 chinook before 8am"

    python scripts/add_report.py --source "Bob at the boat launch" --spoken <<'EOF'
    said the pinks showed up in the Current about a week ago, thick now
    EOF

    --date       when the FISH activity happened, if the report states it (default: today)
    --url        permalink, when there is one that a person could re-open
    --confidence 0-1, your own read on how solid it is (default 0.5)

PROVENANCE IS EXPLICIT: every row is stamped `entry: "manual"` with who entered it and when, so a
later audit can always tell hand-entered testimony from machine-fetched text. That distinction
matters — a manual row cannot be re-verified by re-fetching a URL, so it should never be silently
treated as though it could be.
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "knowledge" / "observations" / "candidates" / "manual_reports.jsonl"


def main(argv) -> int:
    ap = argparse.ArgumentParser(description="Add a hand-entered fishing report to the queue.")
    ap.add_argument("--source", required=True,
                    help='where it came from, e.g. "TBay Fishing (FB group)" or a person')
    ap.add_argument("--text", help="the report itself; omit to read stdin")
    ap.add_argument("--url", default="", help="permalink if one exists")
    ap.add_argument("--date", default="", help="date of the FISH ACTIVITY (YYYY-MM-DD)")
    ap.add_argument("--confidence", type=float, default=0.5)
    ap.add_argument("--spoken", action="store_true",
                    help="heard in person rather than read — recorded as such, no URL expected")
    a = ap.parse_args(argv)

    text = (a.text or sys.stdin.read()).strip()
    if len(text) < 10:
        print("nothing to add (need at least a sentence)")
        return 1
    if a.date:
        try:
            date.fromisoformat(a.date)
        except ValueError:
            print(f"--date must be YYYY-MM-DD, got {a.date!r}")
            return 1
    if not 0.0 <= a.confidence <= 1.0:
        print("--confidence must be between 0 and 1")
        return 1

    row = {
        "sub": None, "stream": "manual", "entry": "manual",
        "channel": "spoken" if a.spoken else "read",
        "source_label": a.source,
        "url": a.url,
        "date": a.date or datetime.now(timezone.utc).date().isoformat(),
        "date_is_stated": bool(a.date),   # False => the Routine must treat it as "reported on"
        "text": text[:2000],
        "confidence": a.confidence,
        "entered_by": getpass.getuser(),
        "entered_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    n = sum(1 for _ in OUT.open())
    print(f"queued ({n} manual report{'s' if n != 1 else ''} pending extraction)")
    print(f"  {row['date']}  {a.source}")
    print(f"  \"{text[:100]}{'...' if len(text) > 100 else ''}\"")
    if not a.date:
        print("  note: no --date given, so this is filed as REPORTED today, not caught today")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
