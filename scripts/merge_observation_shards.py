"""Merge agent-written observation shards into the main ledger (ADR-042).

Backfill agents write to knowledge/observations/shards/*.jsonl rather than the ledger directly,
so several can run in parallel without racing on a read-modify-write. This folds them in through
the same validation + content-hash dedup gate every other row passes, then clears the shard.

    python scripts/merge_observation_shards.py [--keep]

Idempotent: re-running with the same shards adds nothing (every row dedupes). Rejected rows are
printed with their violations and left in a .rejected file rather than silently dropped.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tbay_fishcast.knowledge import observations as obs  # noqa: E402

SHARDS = Path(__file__).resolve().parents[1] / "knowledge" / "observations" / "shards"


def main(argv) -> int:
    keep = "--keep" in argv
    if not SHARDS.exists():
        print("no shards directory — nothing to merge")
        return 0
    files = sorted(SHARDS.glob("*.jsonl"))
    if not files:
        print("no shards to merge")
        return 0
    tot_add = tot_dupe = tot_rej = 0
    for f in files:
        try:
            rows = [json.loads(ln) for ln in f.read_text().splitlines() if ln.strip()]
        except (OSError, ValueError) as e:
            print(f"  {f.name}: unreadable ({e}) — left in place")
            continue
        for r in rows:
            r.pop("id", None)          # re-derive under the ledger's current hash definition
        out = obs.append_rows(rows)
        tot_add += out["added"]; tot_dupe += out["skipped_dupe"]; tot_rej += len(out["rejected"])
        print(f"  {f.name}: +{out['added']} added, {out['skipped_dupe']} dupe, "
              f"{len(out['rejected'])} rejected")
        if out["rejected"]:
            rej = f.with_suffix(".rejected.jsonl")
            rej.write_text("\n".join(json.dumps({"row": r, "errors": e})
                                     for r, e in out["rejected"]) + "\n")
            print(f"      -> {len(out['rejected'])} written to {rej.name} for inspection")
            for _r, errs in out["rejected"][:3]:
                print(f"         {errs}")
        if not keep:
            f.unlink()                 # merged rows live in the ledger now; shard is transient
    print(f"TOTAL: +{tot_add} added, {tot_dupe} dupes, {tot_rej} rejected "
          f"({len(files)} shard file(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
