"""The candidate queue: hand work to the research Routine, and remember what it already did.

THE GAP THIS CLOSES (found 2026-08-09 auditing "is all of this actually integrated?"): the Reddit
sweep and the manual-report intake both wrote into knowledge/observations/candidates/, and NOTHING
read it. Hundreds of harvested rows sat in a queue with no consumer — harvested is not integrated,
and a pipeline that ends in a directory nobody opens is just a tidier way of losing data. This is
the missing consumer side.

WHY A SCRIPT AND NOT JUST "THE ROUTINE READS THE FILES": the Routine is an LLM, so the one thing it
must not be trusted to do is remember, across nights and across container rebuilds, exactly which
candidates it already turned into ledger rows. That bookkeeping is deterministic, so it lives here
in code: `--next` hands out only unprocessed rows, `--mark` records completion, and both are
idempotent. A crashed or half-finished night re-offers the same rows rather than dropping them.

    python scripts/candidates.py --status
    python scripts/candidates.py --next 40              # JSON array for the Routine to extract from
    python scripts/candidates.py --mark k1 k2 ...       # or pipe keys on stdin
    python scripts/candidates.py --next 40 --oldest     # sweep history instead of recency

Extraction itself stays with the Routine (ADR-042) and never touches the heartbeat (ADR-001).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "knowledge" / "observations" / "candidates"
STATE = DIR / "_processed.json"
# files in the candidates dir that are machinery, not candidates
NOT_CANDIDATES = {"_processed.json", "reddit_checkpoint.json"}


def key_of(row: dict) -> str:
    """Stable identity for a candidate, independent of which file it landed in."""
    if row.get("id") and row.get("sub"):
        return f"{row['sub']}:{row.get('stream', '?')}:{row['id']}"
    # manual reports have no platform id — hash what a human actually entered
    basis = "|".join(str(row.get(k, "")) for k in ("source_label", "date", "text"))
    return "manual:" + hashlib.sha256(basis.encode()).hexdigest()[:16]


def load_all() -> list[dict]:
    rows = []
    if not DIR.exists():
        return rows
    for p in sorted(DIR.glob("*.jsonl")):
        if p.name in NOT_CANDIDATES:
            continue
        for ln in p.read_text().splitlines():
            if not ln.strip():
                continue
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            r["_key"] = key_of(r)
            r["_file"] = p.name
            rows.append(r)
    # a candidate can legitimately appear twice (interrupted window re-append); keep one
    seen, out = set(), []
    for r in rows:
        if r["_key"] in seen:
            continue
        seen.add(r["_key"])
        out.append(r)
    return out


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except ValueError:
            pass
    return {"processed": []}


def save_state(st: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1, sort_keys=True) + "\n")
    tmp.replace(STATE)


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--next", type=int, metavar="N", help="emit N unprocessed candidates as JSON")
    ap.add_argument("--mark", nargs="*", metavar="KEY", help="mark keys processed (or use stdin)")
    ap.add_argument("--oldest", action="store_true",
                    help="oldest first (history sweep) instead of newest first")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args(argv)

    rows = load_all()
    st = load_state()
    done = set(st.get("processed", []))
    todo = [r for r in rows if r["_key"] not in done]

    if a.mark is not None:
        keys = list(a.mark) or [ln.strip() for ln in sys.stdin.read().split() if ln.strip()]
        known = {r["_key"] for r in rows}
        unknown = [k for k in keys if k not in known]
        st["processed"] = sorted(done | set(keys))
        save_state(st)
        print(f"marked {len(keys)} processed ({len(st['processed'])} total)"
              + (f"; {len(unknown)} key(s) not found in the queue" if unknown else ""))
        return 0

    if a.next:
        todo.sort(key=lambda r: r.get("date", ""), reverse=not a.oldest)
        out = []
        for r in todo[:a.next]:
            out.append({k: r[k] for k in ("_key", "date", "text", "url", "sub", "stream",
                                          "title", "source_label", "channel", "date_is_stated",
                                          "confidence") if k in r})
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0

    # default / --status
    by_file: dict[str, list[int]] = {}
    for r in rows:
        b = by_file.setdefault(r["_file"], [0, 0])
        b[0] += 1
        if r["_key"] in done:
            b[1] += 1
    print(f"queue: {len(rows)} candidates, {len(rows) - len(todo)} processed, {len(todo)} pending")
    for f, (tot, dn) in sorted(by_file.items()):
        print(f"  {f:44s} {dn:>5}/{tot:<5} done")
    if todo:
        yrs = sorted({r.get("date", "")[:4] for r in todo if r.get("date")})
        print(f"  pending years: {yrs[0]}..{yrs[-1]}" if yrs else "")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except BrokenPipeError:
        sys.stderr.close()      # `| head` closed the pipe; that is not an error worth a traceback
        raise SystemExit(0)
